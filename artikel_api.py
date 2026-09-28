"""
TEXMA — Artikel-Sammelpflege (Welle 10a)
========================================

Artikel eines Shops in einer Tabelle pflegen: EK, VK, Verkaufspreis,
Artikelnummer, Artikelname. Nichts wird sofort geschrieben:

  artikel_laden(shop)            Artikel und Varianten lesen (nur lesend), seit
                                 Welle 10b mit Kurzbeschreibung und Beschreibung,
                                 seit 10c mit Bildern (nur zuordnen: Reihenfolge,
                                 Hauptbild, entfernen, Bilder des Shops oder per
                                 öffentlicher Adresse — kein Hochladen), seit
                                 10d mit Pflicht-Zubehör am Hauptartikel
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

import difflib
import json
import logging
import re
import secrets
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser

import woo_to_cdh as w
from einstellungen_api import _Fehler, _schreibe_atomar
from shop_api import ShopApi

FELDER = {"ek": "EK", "vk": "VK", "preis": "Verkaufspreis",
          "sku": "Artikelnummer", "name": "Artikelname",
          "kurz": "Kurzbeschreibung", "text": "Beschreibung", "bilder": "Bilder",
          "zubehoer": "Pflicht-Zubehör"}
PREISFELDER = ("ek", "vk", "preis")
TEXTFELDER = ("kurz", "text")      # HTML, nur am Hauptartikel
WOO_FELD = {"preis": "regular_price", "kurz": "short_description", "text": "description"}
MAX_ZUBEHOER = 2                  # Plugin: Plätze A und B, mehr löscht der Produkt-Editor
MAX_BILDER = 20                   # je Artikel; Varianten haben genau 0 oder 1
_BILD_ADRESSE = re.compile(r"^https://[^\s]+\.(?:jpe?g|png|webp|gif)(?:\?[^\s]*)?$", re.I)
UEBERTRAGBAR = PREISFELDER        # in andere Shops mit derselben Artikelnummer
MARGE_WARNUNG = 10                # Prozent (VK − EK) / VK
SPRUNG_WARNUNG = 50               # Prozent Preisänderung: Tippfehler?
BATCH = 100                       # WooCommerce nimmt höchstens 100 je Batch
KATALOG_SEKUNDEN = 600            # so lange gilt ein gelesener Katalog für die Vorschau
PRODUKT_FELDER = ("id,type,status,name,sku,regular_price,dimensions,short_description,"
                  "description,images,meta_data")
VARIANTEN_FELDER = "id,status,sku,regular_price,dimensions,attributes,image,meta_data"
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
            # Merkmale der Variante (Farbe, Größe …) — für „Bild je Farbe“
            "merkmale": [{"name": str(a.get("name") or ""), "option": str(a.get("option") or "")}
                         for a in p.get("attributes") or []] if eltern else [],
            "ek": w._zahl_oder_none(dims.get(w.EK_FIELD)),
            "vk": w._zahl_oder_none(dims.get(w.VK_FIELD)),
            "preis": w._zahl_oder_none(p.get("regular_price")),
            "kurz": "" if eltern else str(p.get("short_description") or ""),
            "text": "" if eltern else str(p.get("description") or ""),
            "bilder": [int(b["id"]) for b in _bilder_roh(p, eltern) if b.get("id")],
            # Pflicht-Zubehör (Plugin „CDH Required Accessories“): gepflegt nur am
            # Hauptartikel; bei Varianten nur, ob sie eine eigene Regel haben
            "zubehoer": [] if eltern else [{"id": a, "menge": round(m, 4)}
                                           for a, m in w._zubehoer_regeln(p)],
            "zubehoer_eigen": bool(eltern) and bool(w._zubehoer_regeln(p))}


def _bilder_roh(p: dict, eltern: dict | None) -> list[dict]:
    """images (Artikel) bzw. image (Variante) aus der Antwort des Shops."""
    if eltern is None:
        return [b for b in p.get("images") or [] if isinstance(b, dict)]
    b = p.get("image")
    return [b] if isinstance(b, dict) and b.get("id") else []


def _vorrat_merken(p: dict, eltern: dict | None, vorrat: dict | None) -> None:
    """Bilder, die der Shop schon hat, für die Auswahl im Tool (id → src, Name)."""
    if vorrat is None:
        return
    for b in _bilder_roh(p, eltern):
        if b.get("id") and b.get("src"):
            vorrat[int(b["id"])] = {"src": str(b["src"]),
                                    "name": str(b.get("name") or "").strip()
                                    or str(b["src"]).rsplit("/", 1)[-1]}


def _bildtext(refs, vorrat: dict) -> str:
    """„keins“ oder „3: Hauptbild a.jpg, dazu b.jpg, c.jpg“."""
    if not refs:
        return "keins"
    namen = [(vorrat.get(r) or {}).get("name") or f"Bild #{r}" if isinstance(r, int)
             else str(r).rsplit("/", 1)[-1].split("?")[0] for r in refs]
    if len(namen) == 1:
        return namen[0]
    rest = ", ".join(namen[1:4]) + (f" und {len(namen) - 4} weitere" if len(namen) > 4 else "")
    return f"{len(namen)}: Hauptbild {namen[0]}, dazu {rest}"


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
    if feld in TEXTFELDER:
        return str(roh if roh is not None else "")
    if feld == "zubehoer":
        regeln = []
        for r in roh or []:
            if not isinstance(r, dict) or isinstance(r.get("id"), bool):
                raise ValueError
            acc = int(str(r.get("id")).strip())
            menge = r.get("menge")
            menge = float(str(menge).replace(",", ".").strip()) if menge not in (None, "") else 0.0
            regeln.append({"id": acc, "menge": round(menge, 4)})
        return regeln
    if feld == "bilder":
        refs = []
        for r in roh if isinstance(roh, list) else ([] if roh in (None, "") else [roh]):
            if isinstance(r, bool):
                raise ValueError
            if isinstance(r, int) or (isinstance(r, str) and r.strip().isdigit()):
                r = int(r)
                if r < 1:
                    raise ValueError
            elif isinstance(r, str) and r.strip():
                r = r.strip()
            else:
                raise ValueError
            if r not in refs:
                refs.append(r)
        return refs
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


def _ausschnitt(alt: str, neu: str, rand: int = 40, hoechstens: int = 300) -> tuple[str, str]:
    """Die geänderte Stelle eines langen Textes mit etwas Umfeld, alt und neu."""
    alt, neu = alt or "", neu or ""
    ops = [o for o in difflib.SequenceMatcher(None, alt, neu, autojunk=False).get_opcodes()
           if o[0] != "equal"]
    if not ops:
        return "", ""
    anfang = max(0, ops[0][1] - rand)       # vor der ersten Änderung sind beide gleich

    def teil(t, ende):
        ende = min(len(t), ende + rand, anfang + hoechstens)
        return ("…" if anfang else "") + t[anfang:ende] + ("…" if ende < len(t) else "")
    return teil(alt, ops[-1][2]), teil(neu, ops[-1][4])


def _aenderung(z: dict, rand: int = 15) -> str:
    """Eine Änderung als kurzer Text für Verlauf und Rücknahme."""
    feld = z["feld"]
    if "alt_text" in z:
        return f"{FELDER[feld]} {z['alt_text']} → {z['neu_text']}"
    if feld in TEXTFELDER:
        a, n = _ausschnitt(z["alt"], z["neu"], rand, 80)
        return f"{FELDER[feld]} „{a}“ → „{n}“"
    return f"{FELDER[feld]} {_text(feld, z['alt'])} → {_text(feld, z['neu'])}"


class _Tags(HTMLParser):
    LEER = {"br", "hr", "img", "input", "meta", "link", "source", "wbr", "area", "col",
            "embed", "param", "track", "base"}

    def __init__(self):
        super().__init__()
        self.offen, self.fehler = [], 0

    def handle_starttag(self, tag, attrs):
        if tag not in self.LEER:
            self.offen.append(tag)

    def handle_endtag(self, tag):
        if tag in self.LEER:
            return
        if tag in self.offen:
            while self.offen.pop() != tag:
                self.fehler += 1
        else:
            self.fehler += 1


def _html_maengel(t: str) -> int:
    """Nicht geschlossene oder überzählige Tags."""
    p = _Tags()
    p.feed(t or "")
    p.close()
    return p.fehler + len(p.offen)


_SKRIPT = re.compile(r"<\s*script|javascript:|\son\w+\s*=", re.I)


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


def katalog_lesen(client, vorrat: dict | None = None) -> list[dict]:
    """Alle Artikel, Varianten direkt unter ihrem Hauptartikel. vorrat
    sammelt nebenbei die Bilder des Shops."""
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
        _vorrat_merken(p, None, vorrat)
        for v in varianten.get(p["id"], []):
            zeilen.append(_zeile(v, p))
            _vorrat_merken(v, p, vorrat)
    return zeilen


def _frisch(client, eintraege: dict[int, tuple[int, str]],
            vorrat: dict | None = None) -> dict[int, dict]:
    """Aktueller Stand genau dieser Artikel. eintraege: id → (parent, Name
    des Hauptartikels). Fehlt eine id im Ergebnis, gibt es sie nicht mehr."""
    out = {}
    produkte = sorted(i for i, (parent, _) in eintraege.items() if not parent)
    for teil in _stuecke(produkte):
        for p in client._get("/products", {"include": ",".join(map(str, teil)),
                                           "per_page": 100, "_fields": PRODUKT_FELDER}) or []:
            out[int(p["id"])] = _zeile(p)
            _vorrat_merken(p, None, vorrat)
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
                _vorrat_merken(v, {"id": parent}, vorrat)
    return out


def _zubehoertext(regeln, namen: dict) -> str:
    """„keins“ oder „004/STICK Stick Logo × 1; …“."""
    if not regeln:
        return "keins"
    teile = []
    for r in regeln:
        menge = _zahl_schreiben(r["menge"]).rstrip("0").rstrip(".").replace(".", ",")
        teile.append(f"{namen.get(r['id']) or '#' + str(r['id'])} × {menge}")
    return "; ".join(teile)


def _bild_ref(r) -> dict:
    return {"id": r} if isinstance(r, int) else {"src": r}


def _schreiben(client, zeilen: list[dict], antworten: dict | None = None) -> tuple[set, set, dict]:
    """Änderungen als WooCommerce-Batch. Liefert (ok, unklar, fehler):
    ok = bestätigt geschrieben, unklar = ohne Antwort (Netz), fehler = id →
    Meldung des Shops. antworten sammelt id → Antwort des Shops."""
    je_artikel: dict[int, dict] = {}
    for z in zeilen:
        e = je_artikel.setdefault(z["id"], {"parent": z["parent"], "daten": {"id": z["id"]}})
        d = e["daten"]
        if z["feld"] in ("ek", "vk"):
            d.setdefault("dimensions", {})[w.EK_FIELD if z["feld"] == "ek" else w.VK_FIELD] = \
                _zahl_schreiben(z["neu"])
        elif z["feld"] == "preis":
            d["regular_price"] = _zahl_schreiben(z["neu"])
        elif z["feld"] == "bilder" and z["parent"]:
            d["image"] = _bild_ref(z["neu"][0]) if z["neu"] else {"id": 0}
        elif z["feld"] == "bilder":
            d["images"] = [_bild_ref(r) for r in z["neu"]]
        elif z["feld"] == "zubehoer":
            d.setdefault("meta_data", []).append(
                {"key": w.ZUBEHOER_META,
                 "value": [{"accessory_id": r["id"], "qty_per_unit": r["menge"]} for r in z["neu"]]})
        else:
            d[WOO_FELD.get(z["feld"], z["feld"])] = z["neu"]
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
                    if antworten is not None:
                        antworten[rid] = r
            unklar |= ids - ok - set(fehler)
    return ok, unklar, fehler


# --- Schnittstelle -----------------------------------------------------------

class ArtikelApi(ShopApi):

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._kataloge: dict[str, tuple[float, list[dict]]] = {}
        self._vorraete: dict[str, dict] = {}        # Shop → Bilder id → {src, name}
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
            vorrat = {}
            zeilen = katalog_lesen(client, vorrat)
            self._kataloge[shop_id] = (time.monotonic(), zeilen)
            self._vorraete[shop_id] = vorrat
        return zeilen

    def _pruefen(self, katalog: list[dict], frisch: dict[int, dict], wuensche: dict,
                 fehler: list, warnungen: list, vor: str = "",
                 vorrat: dict | None = None) -> list[dict]:
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
                elif feld in TEXTFELDER and z["typ"] == "variation":
                    fehler.append(f"{bez}: Texte werden am Hauptartikel gepflegt.")
                elif feld in TEXTFELDER and _SKRIPT.search(neu or ""):
                    fehler.append(f"{bez}: {FELDER[feld]} enthält Skript-Code — nicht erlaubt.")
                elif feld == "preis" and z["typ"] == "variable":
                    fehler.append(f"{bez}: Verkaufspreis wird an den Varianten gepflegt.")
                elif feld in ("sku", "name") and not neu:
                    fehler.append(f"{bez}: {FELDER[feld]} darf nicht leer sein.")
                elif feld == "bilder" and not self._bilder_ok(bez, z, neu, vorrat or {}, fehler):
                    pass
                elif feld == "zubehoer" and not self._zubehoer_ok(bez, z, neu, katalog, fehler):
                    pass
                elif not _gleich(neu, z[feld]):
                    echt[feld] = neu
            for feld, neu in echt.items():
                zeilen.append({"id": iid, "parent": z["parent"], "sku": z["sku"],
                               "name": z["name"], "variante": z["variante"], "bez": bez,
                               "feld": feld, "alt": z[feld], "neu": neu})
                if feld == "zubehoer":
                    namen = {k["id"]: _bez(k) for k in katalog}
                    zeilen[-1]["alt_text"] = _zubehoertext(z[feld], namen)
                    zeilen[-1]["neu_text"] = _zubehoertext(neu, namen)
                    eigene = [k["variante"] or k["sku"] for k in katalog
                              if k["parent"] == iid and k.get("zubehoer_eigen")]
                    if eigene:
                        warnungen.append(f"{bez}: Varianten mit eigener Regel ({', '.join(eigene)}) "
                                         "— dort gilt weiter deren Regel.")
                if feld == "bilder":
                    zeilen[-1]["alt_text"] = _bildtext(z[feld], vorrat or {})
                    zeilen[-1]["neu_text"] = _bildtext(neu, vorrat or {})
                    if not neu and z["typ"] != "variation":
                        warnungen.append(f"{bez}: danach ohne Bild.")
                alt = z[feld]
                if feld in PREISFELDER and alt and neu and \
                        abs(neu - alt) / alt * 100 >= SPRUNG_WARNUNG:
                    warnungen.append(f"{bez}: {FELDER[feld]} ändert sich um "
                                     f"{(neu - alt) / alt * 100:+.0f} % — Tippfehler?")
            for feld in TEXTFELDER:
                if feld in echt and _html_maengel(echt[feld]) > _html_maengel(z[feld]):
                    warnungen.append(f"{bez}: {FELDER[feld]} — HTML-Tags nicht sauber "
                                     "geschlossen, im Shop prüfen.")
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
    def _zubehoer_ok(bez: str, z: dict, neu: list, katalog: list[dict], fehler: list) -> bool:
        """Die Prüfungen des Plugins nachgebaut — über die Schnittstelle greifen sie nicht."""
        vorher = len(fehler)
        if z["typ"] == "variation":
            fehler.append(f"{bez}: Pflicht-Zubehör wird am Hauptartikel gepflegt.")
            return False
        if len(neu) > MAX_ZUBEHOER:
            fehler.append(f"{bez}: Höchstens {MAX_ZUBEHOER} Zubehörartikel (Plätze A und B im "
                          "Plugin) — mehr würde der Produkt-Editor beim nächsten Speichern löschen.")
        nach_id = {k["id"]: k for k in katalog}
        gesehen = set()
        for r in neu:
            acc = nach_id.get(r["id"])
            if r["id"] in gesehen:
                fehler.append(f"{bez}: Zubehör #{r['id']} doppelt.")
            gesehen.add(r["id"])
            if r["id"] == z["id"]:
                fehler.append(f"{bez}: Ein Artikel kann nicht sein eigenes Zubehör sein.")
            elif acc is None:
                fehler.append(f"{bez}: Zubehör #{r['id']} gibt es im Shop nicht — bitte neu laden.")
            elif acc["typ"] != "simple":
                fehler.append(f"{bez}: Zubehör {_bez(acc)} muss ein einfacher Artikel sein "
                              "(keine Varianten).")
            elif acc["status"] != "publish":
                fehler.append(f"{bez}: Zubehör {_bez(acc)} ist nicht veröffentlicht.")
            elif not acc["sku"]:
                fehler.append(f"{bez}: Zubehör {_bez(acc)} hat keine Artikelnummer — der Import "
                              "könnte es nicht an CDH geben.")
            if not r["menge"] > 0:
                fehler.append(f"{bez}: Menge je Stück muss größer als 0 sein.")
        return len(fehler) == vorher

    @staticmethod
    def _bilder_ok(bez: str, z: dict, neu: list, vorrat: dict, fehler: list) -> bool:
        vorher = len(fehler)
        if z["typ"] == "variation" and len(neu) > 1:
            fehler.append(f"{bez}: Eine Variante hat höchstens ein Bild.")
        if len(neu) > MAX_BILDER:
            fehler.append(f"{bez}: Höchstens {MAX_BILDER} Bilder.")
        for r in neu:
            if isinstance(r, int) and r not in vorrat and r not in z["bilder"]:
                fehler.append(f"{bez}: Bild #{r} ist im Shop nicht bekannt — bitte neu laden.")
            elif isinstance(r, str) and not _BILD_ADRESSE.match(r):
                fehler.append(f"{bez}: Bildadresse „{r}“ — muss mit https:// beginnen und "
                              "auf .jpg, .png, .webp oder .gif enden.")
        return len(fehler) == vorher

    @staticmethod
    def _anzeige(zeilen: list[dict]) -> list[dict]:
        out = []
        for z in zeilen:
            if "alt_text" in z:
                alt, neu = z["alt_text"], z["neu_text"]
            elif z["feld"] in TEXTFELDER:
                alt, neu = _ausschnitt(z["alt"], z["neu"])
                alt, neu = alt or "leer", neu or "leer"
            else:
                alt, neu = _text(z["feld"], z["alt"]), _text(z["feld"], z["neu"])
            out.append({"id": z["id"], "bez": z["bez"], "feld": z["feld"],
                        "feldname": FELDER[z["feld"]], "alt": alt, "neu": neu,
                        "text": z["feld"] in TEXTFELDER})
        return out

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
                "zeilen": [f"{s.get('name')}: {z['bez']} — {_aenderung(z)}"
                           for s in d.get("shops") or [] for z in s.get("zeilen") or []][:60]}

    def _verlauf_artikel(self, shop_name: str, zeilen: list[dict], was: str) -> None:
        je = defaultdict(list)
        for z in zeilen:
            je[z["bez"]].append(_aenderung(z))
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
                    "marge_warnung": MARGE_WARNUNG,
                    "bildvorrat": self._vorraete.get(shop_id, {})}
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
                    fehler.append(f"{_bez(k)}: {FELDER[feld]} „{a.get('wert')}“ ist "
                                  f"{'keine Zahl' if feld in PREISFELDER else 'ungültig'}.")
                    continue
                wuensche[(iid, feld)] = neu
                if "alt" in a:
                    try:
                        gezeigt[(iid, feld)] = _wert(feld, a.get("alt"))
                    except ValueError:
                        pass
            vorrat = self._vorraete.setdefault(shop_id, {})
            try:
                frisch = _frisch(client, {i: (k_nach_id[i]["parent"], k_nach_id[i]["name"])
                                          for i, _ in wuensche}, vorrat)
            except Exception as e:  # noqa: BLE001
                raise _Fehler(f"Artikel nicht abrufbar ({w.ohne_schluessel(e)}).") from None
            for (iid, feld), alt in gezeigt.items():
                z = frisch.get(iid)
                if z is not None and not _gleich(alt, z[feld]):
                    jetzt = _bildtext(z[feld], vorrat) if feld == "bilder" else _text(feld, z[feld])
                    fehler.append(f"{_bez(z)}: {FELDER[feld]} wurde inzwischen im Shop "
                                  f"geändert (jetzt {jetzt}). Bitte neu laden.")
            zeilen = self._pruefen(katalog, frisch, wuensche, fehler, warnungen, vorrat=vorrat)
            if any(z["feld"] == "zubehoer" for z in zeilen) and not shop.get("pflicht_zubehoer"):
                warnungen.append(f"Im Tool ist „Pflicht-Zubehör ergänzen“ für {shop.get('name')} "
                                 "aus — der Import ergänzt das Zubehör erst, wenn es an ist "
                                 "(Einstellungen → Shop).")
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
                self._vorschau = {"id": vid, "shop": shop_id, "shops": shops}
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
                gesichert, meldungen, schluessel = 0, [], []
                for s in protokoll["shops"]:
                    antworten = {}
                    ok, unklar, fehl = _schreiben(clients[s["id"]], s["zeilen"], antworten)
                    self._kataloge.pop(s["id"], None)
                    # Bilder per Adresse bekommen im Shop erst jetzt eine id —
                    # die Rücknahme vergleicht mit dem, was wirklich dort steht.
                    for z in s["zeilen"]:
                        if z["feld"] == "bilder" and z["id"] in antworten:
                            z["neu"] = _zeile(antworten[z["id"]],
                                              {"id": z["parent"]} if z["parent"] else None)["bilder"]
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
                    if s["id"] == v["shop"]:
                        schluessel += [f"{z['id']}:{z['feld']}" for z in geschrieben]
                    gesichert += len(geschrieben)
                    if geschrieben:
                        self._verlauf_artikel(s["name"], geschrieben, "Sammeländerung")
                _schreibe_atomar(datei, json.dumps(protokoll, ensure_ascii=False, indent=1))
            # gesichert_keys: was die Tabelle aus dem Entwurf nehmen darf —
            # Abgelehntes bleibt dort als ungesicherte Änderung stehen.
            return {"ok": True, "gesichert": gesichert, "meldungen": meldungen,
                    "gesichert_keys": schluessel,
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
                            if "alt_text" in z:
                                zurueck[-1].update(alt_text=z["neu_text"], neu_text=z["alt_text"])
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
