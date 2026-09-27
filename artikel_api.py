"""
TEXMA — Artikel-Sammelpflege (Welle 10a)
========================================

Artikel eines Shops in einer Tabelle pflegen: EK, VK, Verkaufspreis,
Artikelnummer, Artikelname. Nichts wird sofort geschrieben:

  artikel_laden(shop)            Artikel und Varianten lesen (nur lesend)
  artikel_vorschau(shop, änderungen, andere_shops)
                                 frisch aus dem Shop lesen, prüfen, alt/neu
                                 zeigen — auf Wunsch auch für andere Shops mit
                                 derselben Artikelnummer (nur Preise)
  artikel_sichern(vorschau_id)   genau die geprüfte Vorschau schreiben
                                 (WooCommerce-Batch, je 100); vorher Rücknahme-
                                 Datei in Backup\\Artikel\\, danach Verlauf
  artikel_letzte()               letzte Sammeländerung (für „Zurücknehmen")
  artikel_zuruecknehmen(datei, bestaetigt)
                                 alte Werte zurückschreiben — nur dort, wo im
                                 Shop noch der damals gesicherte Wert steht

EK/VK stehen im Shop in Länge/Breite (w.EK_FIELD/w.VK_FIELD). Alles nur im
Admin-Modus; die Prüfungen sitzen hier, nicht im Fenster.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import woo_to_cdh as w
from einstellungen_api import _Fehler, _schreibe_atomar
from shop_api import ShopApi

FELDER = {"ek": "EK", "vk": "VK", "preis": "Verkaufspreis",
          "sku": "Artikelnummer", "name": "Artikelname"}
PREISFELDER = ("ek", "vk", "preis")
UEBERTRAGBAR = PREISFELDER        # in andere Shops mit derselben Artikelnummer
MARGE_WARNUNG = 10                # Prozent (VK − EK) / VK
SPRUNG_WARNUNG = 50               # Prozent Preisänderung: Tippfehler?
BATCH = 100                       # WooCommerce nimmt höchstens 100 je Batch
KATALOG_SEKUNDEN = 600            # so lange gilt ein gelesener Katalog für die Vorschau
PRODUKT_FELDER = "id,type,status,name,sku,regular_price,dimensions"
VARIANTEN_FELDER = "id,status,sku,regular_price,dimensions,attributes"
_ZAHL = re.compile(r"^\d+(?:[.,]\d{1,4})?$")


# --- Werte ------------------------------------------------------------------

def _zeile(p: dict, eltern: dict | None = None) -> dict:
    """Produkt oder Variante als Tabellenzeile."""
    dims = p.get("dimensions") or {}
    return {"id": int(p["id"]), "parent": int(eltern["id"]) if eltern else 0,
            "typ": "variation" if eltern else (p.get("type") or "simple"),
            "status": p.get("status") or "",
            "sku": str(p.get("sku") or "").strip(),
            "name": str((eltern or p).get("name") or ""),
            "variante": " / ".join(str(a.get("option") or "")
                                   for a in p.get("attributes") or []) if eltern else "",
            "ek": w._zahl_oder_none(dims.get(w.EK_FIELD)),
            "vk": w._zahl_oder_none(dims.get(w.VK_FIELD)),
            "preis": w._zahl_oder_none(p.get("regular_price"))}


def _wert(feld: str, roh):
    """Eingabe aus der Tabelle → Wert. Preise: 12 / 12,5 / 12.50, leer = None."""
    if feld in PREISFELDER:
        if isinstance(roh, (int, float)) and not isinstance(roh, bool):
            if roh < 0:
                raise ValueError
            return round(float(roh), 4)
        t = str(roh if roh is not None else "").replace("€", "").replace(" ", "").strip()
        if t == "":
            return None
        if not _ZAHL.match(t):
            raise ValueError
        return round(float(t.replace(",", ".")), 4)
    return str(roh if roh is not None else "").strip()


def _gleich(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 0.00005
    return (a if a not in ("",) else None) == (b if b not in ("",) else None)


def _text(feld: str, x) -> str:
    if x is None or x == "":
        return "leer"
    if feld in PREISFELDER:
        return _zahl_schreiben(x).replace(".", ",")
    return str(x)


def _zahl_schreiben(x) -> str:
    """Wie WooCommerce es speichert: Punkt, mindestens 2, höchstens 4 Stellen."""
    if x is None:
        return ""
    ganz, _, rest = f"{float(x):.4f}".partition(".")
    return f"{ganz}.{rest.rstrip('0').ljust(2, '0')}"


def _bez(z: dict) -> str:
    teile = [z["sku"] or f"#{z['id']}", z["name"]]
    return " ".join(t for t in teile if t) + (f" ({z['variante']})" if z.get("variante") else "")


def _stuecke(liste, n=BATCH):
    for i in range(0, len(liste), n):
        yield liste[i:i + n]


# --- Shop lesen und schreiben ------------------------------------------------

def _seiten(client, pfad: str, felder: str, **params) -> list[dict]:
    out, seite = [], 1
    while True:
        teil = client._get(pfad, {"per_page": 100, "page": seite, "_fields": felder,
                                  **params}) or []
        out += teil
        if len(teil) < 100:
            return out
        seite += 1


def katalog_lesen(client) -> list[dict]:
    """Alle Artikel, Varianten direkt unter ihrem Hauptartikel."""
    produkte = _seiten(client, "/products", PRODUKT_FELDER)
    variabel = [p for p in produkte if p.get("type") == "variable"]
    with ThreadPoolExecutor(max_workers=w.ABRUF_PARALLEL) as ex:
        varianten = dict(zip(
            [p["id"] for p in variabel],
            ex.map(lambda p: _seiten(client, f"/products/{p['id']}/variations",
                                     VARIANTEN_FELDER), variabel)))
    zeilen = []
    for p in sorted(produkte, key=lambda p: (str(p.get("name") or "").lower(), p.get("id"))):
        zeilen.append(_zeile(p))
        zeilen += [_zeile(v, p) for v in varianten.get(p["id"], [])]
    return zeilen


def _frisch(client, eintraege: dict[int, tuple[int, str]]) -> dict[int, dict]:
    """Aktueller Stand genau dieser Artikel. eintraege: id → (parent, Name
    des Hauptartikels). Fehlt eine id im Ergebnis, gibt es sie nicht mehr."""
    out = {}
    produkte = sorted(i for i, (parent, _) in eintraege.items() if not parent)
    for teil in _stuecke(produkte):
        for p in client._get("/products", {"include": ",".join(map(str, teil)),
                                           "per_page": 100, "_fields": PRODUKT_FELDER}) or []:
            out[int(p["id"])] = _zeile(p)
    nach_parent = defaultdict(list)
    for i, (parent, name) in eintraege.items():
        if parent:
            nach_parent[(parent, name)].append(i)
    for (parent, name), ids in nach_parent.items():
        for teil in _stuecke(sorted(ids)):
            for v in client._get(f"/products/{parent}/variations",
                                 {"include": ",".join(map(str, teil)), "per_page": 100,
                                  "_fields": VARIANTEN_FELDER}) or []:
                out[int(v["id"])] = _zeile(v, {"id": parent, "name": name})
    return out


def _schreiben(client, zeilen: list[dict]) -> tuple[set, set, dict]:
    """Änderungen als WooCommerce-Batch. Liefert (ok, unklar, fehler):
    ok = bestätigt geschrieben, unklar = ohne Antwort (Netz), fehler = id →
    Meldung des Shops."""
    je_artikel: dict[int, dict] = {}
    for z in zeilen:
        e = je_artikel.setdefault(z["id"], {"parent": z["parent"], "daten": {"id": z["id"]}})
        d = e["daten"]
        if z["feld"] in ("ek", "vk"):
            d.setdefault("dimensions", {})[w.EK_FIELD if z["feld"] == "ek" else w.VK_FIELD] = \
                _zahl_schreiben(z["neu"])
        elif z["feld"] == "preis":
            d["regular_price"] = _zahl_schreiben(z["neu"])
        else:
            d[z["feld"]] = z["neu"]
    gruppen = defaultdict(list)
    for e in je_artikel.values():
        pfad = f"/products/{e['parent']}/variations/batch" if e["parent"] else "/products/batch"
        gruppen[pfad].append(e["daten"])
    ok, unklar, fehler = set(), set(), {}
    for pfad, daten in gruppen.items():
        for teil in _stuecke(daten):
            ids = {d["id"] for d in teil}
            try:
                antwort = client._post(pfad, {"update": teil}) or {}
            except Exception as e:  # noqa: BLE001
                logging.error("Artikel-Batch %s ohne Antwort: %s", pfad, w.ohne_schluessel(e))
                unklar |= ids
                continue
            for r in antwort.get("update") or []:
                rid = int(r.get("id") or 0)
                if r.get("error"):
                    fehler[rid] = w.ohne_schluessel(
                        (r["error"] or {}).get("message") or "abgelehnt")
                elif rid in ids:
                    ok.add(rid)
            unklar |= ids - ok - set(fehler)
    return ok, unklar, fehler


# --- Schnittstelle -----------------------------------------------------------

class ArtikelApi(ShopApi):

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._kataloge: dict[str, tuple[float, list[dict]]] = {}
        self._vorschau: dict | None = None
        self._artikel_sperre = threading.Lock()

    @property
    def _p_artikel(self):
        return self._p_backup / "Artikel"

    # --- intern -------------------------------------------------------------
    def _artikel_client(self, shop_id: str):
        shop = self._shop_cfg(shop_id)
        if w.fehlende_zugangsdaten(shop):
            raise _Fehler(f"{shop.get('name')}: kein Zugang hinterlegt.")
        return shop, self._client(shop["url"], shop["consumer_key"], shop["consumer_secret"])

    def _katalog(self, shop_id: str, client, neu: bool = False) -> list[dict]:
        zeit, zeilen = self._kataloge.get(shop_id, (0.0, None))
        if neu or zeilen is None or time.monotonic() - zeit > KATALOG_SEKUNDEN:
            zeilen = katalog_lesen(client)
            self._kataloge[shop_id] = (time.monotonic(), zeilen)
        return zeilen

    def _pruefen(self, katalog: list[dict], frisch: dict[int, dict], wuensche: dict,
                 fehler: list, warnungen: list, vor: str = "") -> list[dict]:
        """Wünsche {(id, feld): neu} gegen den frischen Stand prüfen. Liefert
        die echten Änderungen; Fehler blockieren das Sichern, Warnungen nicht."""
        nach_id: dict[int, dict] = defaultdict(dict)
        for (iid, feld), neu in wuensche.items():
            nach_id[iid][feld] = neu
        zeilen = []
        for iid, felder in nach_id.items():
            z = frisch.get(iid)
            if z is None:
                fehler.append(f"{vor}Artikel #{iid} gibt es im Shop nicht mehr.")
                continue
            bez = vor + _bez(z)
            echt = {}
            for feld, neu in felder.items():
                if feld == "name" and z["typ"] == "variation":
                    fehler.append(f"{bez}: Varianten haben keinen eigenen Namen.")
                elif feld == "preis" and z["typ"] == "variable":
                    fehler.append(f"{bez}: Verkaufspreis wird an den Varianten gepflegt.")
                elif feld in ("sku", "name") and not neu:
                    fehler.append(f"{bez}: {FELDER[feld]} darf nicht leer sein.")
                elif not _gleich(neu, z[feld]):
                    echt[feld] = neu
            for feld, neu in echt.items():
                zeilen.append({"id": iid, "parent": z["parent"], "sku": z["sku"],
                               "name": z["name"], "variante": z["variante"], "bez": bez,
                               "feld": feld, "alt": z[feld], "neu": neu})
                alt = z[feld]
                if feld in PREISFELDER and alt and neu and \
                        abs(neu - alt) / alt * 100 >= SPRUNG_WARNUNG:
                    warnungen.append(f"{bez}: {FELDER[feld]} ändert sich um "
                                     f"{(neu - alt) / alt * 100:+.0f} % — Tippfehler?")
            if "ek" in echt or "vk" in echt:
                ek, vk = echt.get("ek", z["ek"]), echt.get("vk", z["vk"])
                if ek is not None and vk is not None and ek > vk:
                    fehler.append(f"{bez}: EK ({_text('ek', ek)}) größer als VK ({_text('vk', vk)}).")
                elif ek is not None and vk and (vk - ek) / vk * 100 < MARGE_WARNUNG:
                    warnungen.append(f"{bez}: Marge nur {(vk - ek) / vk * 100:.0f} %.")
            if "ek" in echt and echt["ek"] is None:
                warnungen.append(f"{bez}: EK leer — fehlt dann im CDH-Auftrag.")
            if "vk" in echt and not echt["vk"]:
                warnungen.append(f"{bez}: VK {'leer' if echt['vk'] is None else '0'} — "
                                 "Position geht ohne Preis an CDH.")
            if "preis" in echt and not echt["preis"]:
                warnungen.append(f"{bez}: Verkaufspreis {'leer — im Shop nicht kaufbar' if echt['preis'] is None else '0 — im Shop kostenlos'}.")
        # Artikelnummern eindeutig (WooCommerce lehnt doppelte ab)
        neue_sku = {z["id"]: z["neu"] for z in zeilen if z["feld"] == "sku"}
        if neue_sku:
            wer = defaultdict(list)
            for k in katalog:
                sku = neue_sku.get(k["id"], k["sku"])
                if sku:
                    wer[sku.lower()].append(k)
            for iid, sku in neue_sku.items():
                andere = [k for k in wer[sku.lower()] if k["id"] != iid]
                if andere:
                    fehler.append(f"{vor}Artikelnummer {sku} gibt es im Shop schon "
                                  f"({_bez(andere[0])}).")
        return zeilen

    @staticmethod
    def _anzeige(zeilen: list[dict]) -> list[dict]:
        return [{"id": z["id"], "bez": z["bez"], "feld": z["feld"], "feldname": FELDER[z["feld"]],
                 "alt": _text(z["feld"], z["alt"]), "neu": _text(z["feld"], z["neu"])}
                for z in zeilen]

    def _andere_shops(self, shop_id: str, zeilen: list[dict], fehler: list,
                      warnungen: list) -> tuple[list[dict], list[str]]:
        preise = [z for z in zeilen if z["feld"] in UEBERTRAGBAR and z["sku"]]
        if not preise:
            return [], []
        cfg, _ = w.load_config(self._base)
        andere = [s for s in cfg.get("shops") or []
                  if (s.get("id") or w.shop_id_aus_name(s.get("name"))) != shop_id
                  and not w.fehlende_zugangsdaten(s)]

        def lesen(s):
            sid = s.get("id") or w.shop_id_aus_name(s.get("name"))
            client = self._client(s["url"], s["consumer_key"], s["consumer_secret"])
            try:
                return sid, s, client, self._katalog(sid, client), None
            except Exception as e:  # noqa: BLE001
                return sid, s, client, None, w.ohne_schluessel(e)

        with ThreadPoolExecutor(max_workers=w.ABRUF_PARALLEL) as ex:
            gelesen = list(ex.map(lesen, andere))
        ergebnis, gefunden = [], set()
        for sid, s, client, katalog, problem in gelesen:
            name = s.get("name") or sid
            if katalog is None:
                fehler.append(f"{name}: Artikel nicht lesbar ({problem}).")
                continue
            nach_sku = defaultdict(list)
            for k in katalog:
                if k["sku"]:
                    nach_sku[k["sku"].lower()].append(k)
            wuensche = {}
            for z in preise:
                treffer = nach_sku.get(z["sku"].lower(), [])
                if len(treffer) > 1:
                    warnungen.append(f"{name}: Artikelnummer {z['sku']} gibt es mehrfach — "
                                     "dort nicht geändert.")
                elif treffer:
                    gefunden.add(z["sku"])
                    wuensche[(treffer[0]["id"], z["feld"])] = z["neu"]
            if not wuensche:
                continue
            k_nach_id = {k["id"]: k for k in katalog}
            frisch = _frisch(client, {i: (k_nach_id[i]["parent"], k_nach_id[i]["name"])
                                      for i, _ in wuensche})
            eigene = self._pruefen(katalog, frisch, wuensche, fehler, warnungen, f"{name}: ")
            if eigene:
                ergebnis.append({"id": sid, "name": name, "zeilen": eigene})
        ohne = sorted({z["sku"] for z in preise} - gefunden)
        return ergebnis, ohne

    def _letzte_datei(self):
        if not self._p_artikel.exists():
            return None
        dateien = sorted(self._p_artikel.glob("sammel_*.json"))
        return dateien[-1] if dateien else None

    def _letzte_info(self) -> dict | None:
        p = self._letzte_datei()
        if p is None:
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if d.get("zurueckgenommen"):
            return None
        zeilen = [z for s in d.get("shops") or [] for z in s.get("zeilen") or []]
        if not zeilen:
            return None
        try:
            wann = datetime.fromisoformat(d.get("zeit") or "").strftime("%d.%m.%Y %H:%M")
        except ValueError:
            wann = d.get("zeit") or ""
        return {"datei": p.name, "zeit": wann, "benutzer": d.get("benutzer") or "",
                "anzahl": len(zeilen),
                "shops": [{"name": s.get("name"), "anzahl": len(s.get("zeilen") or [])}
                          for s in d.get("shops") or [] if s.get("zeilen")],
                "zeilen": [f"{s.get('name')}: {z['bez']} — {FELDER[z['feld']]} "
                           f"{_text(z['feld'], z['alt'])} → {_text(z['feld'], z['neu'])}"
                           for s in d.get("shops") or [] for z in s.get("zeilen") or []][:60]}

    def _verlauf_artikel(self, shop_name: str, zeilen: list[dict], was: str) -> None:
        je = defaultdict(list)
        for z in zeilen:
            je[z["bez"]].append(f"{FELDER[z['feld']]} {_text(z['feld'], z['alt'])} → "
                                f"{_text(z['feld'], z['neu'])}")
        texte = [f"{shop_name}: {was}, {len(je)} Artikel"]
        texte += [f"{shop_name}: {bez} — {'; '.join(t)}" for bez, t in je.items()]
        self._verlauf_schreiben(texte)
        logging.info("%s (%s)", texte[0], self._benutzer)

    # --- für die Oberfläche -------------------------------------------------
    def artikel_laden(self, shop_id: str) -> dict:
        """Artikel und Varianten eines Shops. Nur lesend."""
        try:
            self._admin_noetig("Artikel pflegen")
            shop, client = self._artikel_client(shop_id)
            try:
                zeilen = self._katalog(shop_id, client, neu=True)
            except Exception as e:  # noqa: BLE001
                raise _Fehler(f"Artikel nicht abrufbar ({w.ohne_schluessel(e)}).") from None
            self._vorschau = None
            cfg, _ = w.load_config(self._base)
            andere = [s.get("name") for s in cfg.get("shops") or []
                      if (s.get("id") or w.shop_id_aus_name(s.get("name"))) != shop_id
                      and not w.fehlende_zugangsdaten(s)]
            return {"ok": True, "shop": {"id": shop_id, "name": shop.get("name")},
                    "artikel": zeilen, "andere": andere, "letzte": self._letzte_info(),
                    "marge_warnung": MARGE_WARNUNG}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}

    def artikel_vorschau(self, shop_id: str, aenderungen, andere_shops=False) -> dict:
        """Frisch lesen, prüfen, alt/neu zeigen. Schreibt nichts.

        aenderungen: [{id, feld, wert, alt}] — alt ist der Wert, den die Tabelle
        zeigte; steht im Shop inzwischen etwas anderes, ist das ein Fehler."""
        try:
            self._admin_noetig("Artikel ändern")
            self._vorschau = None
            shop, client = self._artikel_client(shop_id)
            try:
                katalog = self._katalog(shop_id, client)
            except Exception as e:  # noqa: BLE001
                raise _Fehler(f"Artikel nicht abrufbar ({w.ohne_schluessel(e)}).") from None
            k_nach_id = {k["id"]: k for k in katalog}
            fehler, warnungen, wuensche, gezeigt = [], [], {}, {}
            for a in aenderungen or []:
                a = a or {}
                feld = a.get("feld")
                try:
                    iid = int(a.get("id"))
                except (TypeError, ValueError):
                    continue
                k = k_nach_id.get(iid)
                if feld not in FELDER or k is None:
                    fehler.append(f"Artikel #{iid}: unbekannt — bitte neu laden.")
                    continue
                try:
                    neu = _wert(feld, a.get("wert"))
                except ValueError:
                    fehler.append(f"{_bez(k)}: {FELDER[feld]} „{a.get('wert')}“ ist keine Zahl.")
                    continue
                wuensche[(iid, feld)] = neu
                if "alt" in a:
                    try:
                        gezeigt[(iid, feld)] = _wert(feld, a.get("alt"))
                    except ValueError:
                        pass
            try:
                frisch = _frisch(client, {i: (k_nach_id[i]["parent"], k_nach_id[i]["name"])
                                          for i, _ in wuensche})
            except Exception as e:  # noqa: BLE001
                raise _Fehler(f"Artikel nicht abrufbar ({w.ohne_schluessel(e)}).") from None
            for (iid, feld), alt in gezeigt.items():
                z = frisch.get(iid)
                if z is not None and not _gleich(alt, z[feld]):
                    fehler.append(f"{_bez(z)}: {FELDER[feld]} wurde inzwischen im Shop "
                                  f"geändert (jetzt {_text(feld, z[feld])}). Bitte neu laden.")
            zeilen = self._pruefen(katalog, frisch, wuensche, fehler, warnungen)
            shops = [{"id": shop_id, "name": shop.get("name"), "zeilen": zeilen}]
            ohne = []
            if andere_shops and zeilen:
                weitere, ohne = self._andere_shops(shop_id, zeilen, fehler, warnungen)
                shops += weitere
            shops = [s for s in shops if s["zeilen"]]
            anzahl = sum(len(s["zeilen"]) for s in shops)
            vid = None
            if anzahl and not fehler:
                vid = secrets.token_hex(8)
                self._vorschau = {"id": vid, "shops": shops}
            return {"ok": True, "vorschau_id": vid, "anzahl": anzahl,
                    "fehler": fehler, "warnungen": warnungen, "ohne_treffer": ohne,
                    "shops": [{"id": s["id"], "name": s["name"],
                               "zeilen": self._anzeige(s["zeilen"])} for s in shops]}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}

    def artikel_sichern(self, vorschau_id: str) -> dict:
        """Schreibt genau die zuletzt gezeigte, fehlerfreie Vorschau."""
        try:
            self._admin_noetig("Artikel ändern")
            with self._artikel_sperre:
                v = self._vorschau
                if not v or v["id"] != vorschau_id:
                    raise _Fehler("Die Vorschau ist nicht mehr aktuell. Bitte neu erstellen.")
                self._vorschau = None
                # 1. Alles frisch vergleichen, bevor irgendetwas geschrieben wird
                clients = {}
                for s in v["shops"]:
                    shop, client = self._artikel_client(s["id"])
                    clients[s["id"]] = client
                    frisch = _frisch(client, {z["id"]: (z["parent"], z["name"])
                                              for z in s["zeilen"]})
                    anders = [z for z in s["zeilen"] if z["id"] not in frisch
                              or not _gleich(frisch[z["id"]][z["feld"]], z["alt"])]
                    if anders:
                        raise _Fehler(f"{s['name']}: seit der Vorschau im Shop geändert "
                                      f"({', '.join(sorted({z['bez'] for z in anders})[:5])}). "
                                      "Nichts gesichert — bitte neu laden.")
                # 2. Rücknahme-Datei vor dem Schreiben
                stempel = datetime.fromtimestamp(self._uhr())
                self._p_artikel.mkdir(parents=True, exist_ok=True)
                datei = self._p_artikel / f"sammel_{stempel:%Y-%m-%d_%H%M%S}.json"
                n = 2
                while datei.exists():
                    datei = self._p_artikel / f"sammel_{stempel:%Y-%m-%d_%H%M%S}_{n}.json"
                    n += 1
                protokoll = {"zeit": stempel.isoformat(timespec="seconds"),
                             "benutzer": self._benutzer, "zurueckgenommen": None,
                             "shops": [{"id": s["id"], "name": s["name"], "zeilen": s["zeilen"]}
                                       for s in v["shops"]]}
                _schreibe_atomar(datei, json.dumps(protokoll, ensure_ascii=False, indent=1))
                # 3. Schreiben
                gesichert, meldungen = 0, []
                for s in protokoll["shops"]:
                    ok, unklar, fehl = _schreiben(clients[s["id"]], s["zeilen"])
                    self._kataloge.pop(s["id"], None)
                    for iid, text in fehl.items():
                        bez = next((z["bez"] for z in s["zeilen"] if z["id"] == iid), f"#{iid}")
                        meldungen.append(f"{bez}: {text}")
                    if unklar:
                        meldungen.append(f"{s['name']}: {len(unklar)} Artikel ohne Bestätigung "
                                         "vom Shop — bitte neu laden und prüfen.")
                    # Unklare bleiben in der Rücknahme: dort wird ohnehin nur
                    # zurückgeschrieben, wo noch der neue Wert steht.
                    s["zeilen"] = [z for z in s["zeilen"] if z["id"] not in fehl]
                    geschrieben = [z for z in s["zeilen"] if z["id"] in ok]
                    gesichert += len(geschrieben)
                    if geschrieben:
                        self._verlauf_artikel(s["name"], geschrieben, "Sammeländerung")
                _schreibe_atomar(datei, json.dumps(protokoll, ensure_ascii=False, indent=1))
            return {"ok": True, "gesichert": gesichert, "meldungen": meldungen,
                    "log": self._verlauf_lesen(), "letzte": self._letzte_info()}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "fehler": f"Nicht gesichert ({w.ohne_schluessel(e)})."}

    def artikel_letzte(self) -> dict:
        return {"ok": True, "letzte": self._letzte_info()}

    def artikel_zuruecknehmen(self, datei: str, bestaetigt=False) -> dict:
        """Letzte Sammeländerung zurücknehmen — nur Werte, die im Shop noch
        so stehen, wie sie damals gesichert wurden."""
        try:
            self._admin_noetig("Sammeländerung zurücknehmen")
            if bestaetigt is not True:
                raise _Fehler("Ohne ausdrückliche Bestätigung wird nichts zurückgenommen.")
            with self._artikel_sperre:
                p = self._letzte_datei()
                if p is None or p.name != datei:
                    raise _Fehler("Inzwischen gibt es eine neuere Sammeländerung. Bitte neu laden.")
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("zurueckgenommen"):
                    raise _Fehler("Diese Sammeländerung ist schon zurückgenommen.")
                zurueck_n, ausgelassen, meldungen = 0, [], []
                for s in d.get("shops") or []:
                    if not s.get("zeilen"):
                        continue
                    _, client = self._artikel_client(s["id"])
                    frisch = _frisch(client, {z["id"]: (z["parent"], z["name"])
                                              for z in s["zeilen"]})
                    zurueck = []
                    for z in s["zeilen"]:
                        jetzt = frisch.get(z["id"])
                        if jetzt is not None and _gleich(jetzt[z["feld"]], z["neu"]):
                            zurueck.append({**z, "alt": z["neu"], "neu": z["alt"]})
                        else:
                            ausgelassen.append(f"{s['name']}: {z['bez']} — {FELDER[z['feld']]} "
                                               "inzwischen anders, bleibt.")
                    ok, unklar, fehl = _schreiben(client, zurueck)
                    self._kataloge.pop(s["id"], None)
                    for iid, text in fehl.items():
                        bez = next((z["bez"] for z in zurueck if z["id"] == iid), f"#{iid}")
                        meldungen.append(f"{bez}: {text}")
                    if unklar:
                        meldungen.append(f"{s['name']}: {len(unklar)} Artikel ohne Bestätigung "
                                         "vom Shop — bitte neu laden und prüfen.")
                    geschrieben = [z for z in zurueck if z["id"] in ok]
                    zurueck_n += len(geschrieben)
                    if geschrieben:
                        self._verlauf_artikel(s["name"], geschrieben,
                                              "Sammeländerung zurückgenommen")
                d["zurueckgenommen"] = {"zeit": datetime.fromtimestamp(self._uhr())
                                        .isoformat(timespec="seconds"),
                                        "benutzer": self._benutzer, "anzahl": zurueck_n}
                _schreibe_atomar(p, json.dumps(d, ensure_ascii=False, indent=1))
            return {"ok": True, "zurueck": zurueck_n, "ausgelassen": ausgelassen,
                    "meldungen": meldungen, "log": self._verlauf_lesen(), "letzte": None}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "fehler": f"Nicht zurückgenommen ({w.ohne_schluessel(e)})."}
