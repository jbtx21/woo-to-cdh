"""
TEXMA — Einstellungen als Python-Schnittstelle für die Oberfläche (Welle 5)
============================================================================

Die Oberfläche (ui/index.html, gestartet über oberflaeche.py mit pywebview)
ruft diese Klasse über `window.pywebview.api.<methode>()` auf. Hier liegt
die ganze Logik — die Oberfläche zeigt nur an:

  laden()              Einstellungen im Format des Entwurfs, Verlauf, Benutzer
  sichern(daten)       prüfen, Backup, schreiben, Änderungsverlauf
  admin_anmelden(pw)   Passwort gegen den Hash in zugang.yaml, 10 Minuten
  admin_abmelden()
  admin_status()
  versandarten(id)     Versandarten des Shops (nur lesend, für den Abgleich)

Geschützt (nur im Admin-Modus): Debitornummer, Veredelungs-Präfixe, Shop
hinzufügen/entfernen, Zugang. Die Prüfung passiert hier, nicht in der
Oberfläche — ein manipuliertes Fenster kommt daran nicht vorbei.

Zugangsdaten verlassen diese Klasse nie: laden() liefert je Shop nur, ob ein
Zugang hinterlegt ist.

Nur Namen ohne führenden Unterstrich sind für die Oberfläche sichtbar
(pywebview-Regel) — alles Interne beginnt deshalb mit "_".
"""

from __future__ import annotations

import copy
import getpass
import hashlib
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

import yaml

import woo_to_cdh as w
from migrate_config import verify_admin_password

ADMIN_MINUTEN = 10
FEHLVERSUCHE_BIS_PAUSE = 5
PAUSE_SEKUNDEN = 60

VERLAUF_NAME = "aenderungsverlauf.log"
VERLAUF_KOPF = "zeit\tbenutzer\taenderung\n"

STANDARD_STATUS = ["processing", "on-hold"]
STATUS_TEXT = {"processing": "In Bearbeitung", "on-hold": "In Wartestellung"}
BUENDELUNG_TEXT = {"einzeln": "„Einzeln“", "trenn": "„Mit Trennzeilen“",
                   "voll": "„Zusammengefasst“"}
UNKNOWN_TEXT = {"cdh": "CDH-Kundenstamm", "firma": "Rechnungsadresse aus dem Shop",
                "versand": "Versandadresse der ersten Bestellung",
                "sperren": "Import sperren"}
PREFIX_LABELS = {"004/": "Stick", "316/": "Stick", "006/": "Transferdruck",
                 "234/": "Silberreflex"}
FARBEN = ["blue", "orange", "indigo", "red", "green", "teal", "gray", "pink", "purple"]
ORT_FELDER = ("ort", "name1", "name2", "street", "plz", "city", "country")
ORT_PFLICHT = (("ort", "Lieferort"), ("name1", "Firma"), ("street", "Straße"),
               ("plz", "PLZ"), ("city", "Ort"))


# Nur für die Anzeige, werden nie gesichert und zählen nicht als Änderung.
NUR_ANZEIGE = ("methods", "color", "zugang")


def _ohne_anzeige(s: dict) -> dict:
    return {k: v for k, v in (s or {}).items() if k not in NUR_ANZEIGE}


class _Fehler(Exception):
    """Fachlicher Fehler, Text geht 1:1 an die Oberfläche."""


def _windows_benutzer() -> str:
    return os.environ.get("USERNAME") or getpass.getuser() or "?"


def _datei_hash(*pfade: Path) -> str:
    h = hashlib.sha256()
    for p in pfade:
        h.update(p.read_bytes() if p.exists() else b"-")
        h.update(b"\0")
    return h.hexdigest()


def _schreibe_atomar(pfad: Path, text: str) -> None:
    tmp = pfad.with_name(pfad.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, pfad)


def _yaml(daten) -> str:
    return yaml.safe_dump(daten, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=1000)


# ---------------------------------------------------------------------------
# Übersetzung Konfiguration ↔ Oberfläche
# ---------------------------------------------------------------------------

def _praefixe_ui(eintraege) -> list:
    out = []
    for e in eintraege or []:
        if isinstance(e, dict):
            p = str(e.get("prefix") or "").strip()
            label = str(e.get("label") or PREFIX_LABELS.get(p, "")).strip()
        else:
            p = str(e).strip()
            label = PREFIX_LABELS.get(p, "")
        if p:
            out.append({"prefix": p, "label": label})
    return out


def _excel_ui(eintraege) -> list:
    out = []
    for e in eintraege or []:
        if isinstance(e, dict):
            key = str(e.get("key") or "").strip()
            out.append({"key": key, "label": str(e.get("label") or key).strip()})
        elif str(e).strip():
            out.append({"key": str(e).strip(), "label": str(e).strip()})
    return out


def _orte_ui(adressen: dict) -> list:
    return [{"ort": str(ort), "name1": str(a.get("name1") or ""),
             "name2": str(a.get("name2") or ""), "street": str(a.get("street") or ""),
             "plz": str(a.get("postcode") or ""), "city": str(a.get("city") or ""),
             "country": str(a.get("country") or "DE")}
            for ort, a in (adressen or {}).items() if isinstance(a, dict)]


def _after(shop: dict, glob: dict) -> str:
    return w._status_after_export(shop, glob)


def shop_zu_ui(shop: dict, glob: dict, adressen: dict, index: int,
               zugang_da: bool) -> dict:
    """Ein Shop aus einstellungen.yaml im Datenmodell des Entwurfs."""
    if shop.get("combine_by_delivery"):
        buendelung = "voll" if shop.get("aggregate_all_positions") else "trenn"
    else:
        buendelung = "einzeln"
    name_in_no = shop["order_no_with_name"] if "order_no_with_name" in shop \
        else glob.get("order_no_with_name", False)
    return {
        "id": shop.get("id") or w.shop_id_aus_name(shop.get("name")),
        "name": str(shop.get("name") or ""),
        "color": FARBEN[index % len(FARBEN)],
        "url": re.sub(r"^https?://", "", str(shop.get("url") or "")),
        "debitor": str(shop.get("datev_no") or ""),
        "active": bool(shop.get("enabled", True)),
        "statuses": list(shop.get("included_statuses") or STANDARD_STATUS),
        "after": _after(shop, glob),
        "days": sorted(int(d) for d in (shop.get("import_on_days") or [])),
        "nameInNo": bool(name_in_no),
        "bundling": buendelung,
        "orte": _orte_ui(adressen),
        "excel": _excel_ui(shop.get("extra_excel_meta")),
        "unknownOrt": str(shop.get("unknown_delivery") or "cdh"),
        "excelSum": bool(shop.get("excel_summary", False)),
        "excelSumBy": str(shop.get("excel_summary_by") or "ort"),
        "excelSumVed": bool(shop.get("excel_summary_veredelungen", True)),
        "methods": [],
        "zugang": zugang_da,
    }


def _ui_auf_shop(neu: dict, alt_ui: dict, shop: dict, glob: dict) -> None:
    """Überträgt nur die Felder, die sich gegenüber alt_ui geändert haben.
    Unveränderte Shops bleiben dadurch Byte für Byte gleich."""
    def geaendert(k):
        return neu.get(k) != alt_ui.get(k)

    if geaendert("active"):
        shop["enabled"] = bool(neu["active"])
    if geaendert("debitor"):
        d = neu["debitor"]
        shop["datev_no"] = int(d) if str(d).isdigit() else d
    if geaendert("statuses"):
        shop["included_statuses"] = list(neu["statuses"])
    if geaendert("days"):
        if neu["days"]:
            shop["import_on_days"] = sorted(int(x) for x in neu["days"])
        else:
            shop.pop("import_on_days", None)
    if geaendert("after"):
        shop["status_after_export"] = neu["after"] or ""
    if geaendert("nameInNo"):
        shop["order_no_with_name"] = bool(neu["nameInNo"])
    if geaendert("bundling"):
        b = neu["bundling"]
        if b == "einzeln":
            shop.pop("combine_by_delivery", None)
            shop.pop("aggregate_all_positions", None)
        else:
            shop["combine_by_delivery"] = True
            if b == "voll":
                shop["aggregate_all_positions"] = True
            else:
                shop.pop("aggregate_all_positions", None)
    if geaendert("excel"):
        if neu["excel"]:
            # Angaben, die die Oberfläche nicht zeigt (checkout_key, team_key
            # für die Personalnummer), bleiben am Feld erhalten.
            alt = {str(e.get("key") or "").strip(): e for e in shop.get("extra_excel_meta") or []
                   if isinstance(e, dict)}
            shop["extra_excel_meta"] = [
                {**{k: v for k, v in alt.get(x["key"], {}).items() if k not in ("key", "label")},
                 "key": x["key"], "label": x["label"]} for x in neu["excel"]]
        else:
            shop.pop("extra_excel_meta", None)
    if geaendert("unknownOrt"):
        if neu["unknownOrt"] == "cdh":
            shop.pop("unknown_delivery", None)
        else:
            shop["unknown_delivery"] = neu["unknownOrt"]
    if geaendert("excelSum"):
        shop["excel_summary"] = bool(neu["excelSum"])
    if geaendert("excelSumBy"):
        shop["excel_summary_by"] = neu["excelSumBy"]
    if geaendert("excelSumVed"):
        shop["excel_summary_veredelungen"] = bool(neu["excelSumVed"])


def _orte_auf_adressen(orte: list) -> dict:
    return {o["ort"].strip(): {"name1": o["name1"].strip(),
                               "name2": (o.get("name2") or "").strip(),
                               "street": o["street"].strip(),
                               "postcode": str(o["plz"]).strip(),
                               "city": o["city"].strip(),
                               "country": (o.get("country") or "DE").strip() or "DE"}
            for o in orte}


# ---------------------------------------------------------------------------
# Prüfen und Änderungen beschreiben
# ---------------------------------------------------------------------------

def _pruefe_shop(s: dict) -> list[str]:
    n = s.get("name") or s.get("id")
    f = []
    if not set(s.get("statuses") or []) or not set(s["statuses"]) <= set(STANDARD_STATUS):
        f.append(f"{n}: mindestens ein Status (In Bearbeitung / In Wartestellung).")
    if any(not isinstance(d, int) or not 1 <= d <= 31 for d in s.get("days") or []):
        f.append(f"{n}: Importtage nur 1 bis 31.")
    if s.get("bundling") not in BUENDELUNG_TEXT:
        f.append(f"{n}: unbekannte Bündelung.")
    if s.get("unknownOrt") not in UNKNOWN_TEXT:
        f.append(f"{n}: unbekannte Regel für Lieferort ohne Adresse.")
    if s.get("after") not in ("", "completed"):
        f.append(f"{n}: nach dem Import nur „Status nicht ändern“ oder „Abgeschlossen“.")
    if s.get("excelSumBy") not in ("ort", "gesamt"):
        f.append(f"{n}: Summenblatt je Lieferort oder gesamt.")
    if not re.fullmatch(r"\d{4,6}", str(s.get("debitor") or "")):
        f.append(f"{n}: Debitornummer muss 4 bis 6 Ziffern haben.")
    gesehen = set()
    for o in s.get("orte") or []:
        fehlt = [label for k, label in ORT_PFLICHT if not str(o.get(k) or "").strip()]
        if fehlt:
            f.append(f"{n}: Lieferort {o.get('ort') or '?'} — {', '.join(fehlt)} fehlt.")
        key = str(o.get("ort") or "").strip().lower()
        if key in gesehen:
            f.append(f"{n}: Lieferort {o.get('ort')} doppelt.")
        gesehen.add(key)
    for x in s.get("excel") or []:
        if not str(x.get("key") or "").strip() or not str(x.get("label") or "").strip():
            f.append(f"{n}: Zusatzfeld braucht Feld im Shop und Spalte.")
    return f


def _tage_text(tage: list) -> str:
    if not tage:
        return "Import bei jedem Lauf"
    return "Import nur am " + ", ".join(f"{d}." for d in tage) + " des Monats"


def _beschreibe(alt: dict, neu: dict) -> list[str]:
    """Änderungen eines Shops in Worten, wie im Entwurf-Verlauf."""
    n = re.sub(r"-Shop$", "", neu["name"])
    t = []
    if alt["active"] != neu["active"]:
        t.append(f"{n}: {'eingeschaltet' if neu['active'] else 'ausgeschaltet'}")
    if alt["debitor"] != neu["debitor"]:
        t.append(f"{n}: Debitornummer {alt['debitor']} → {neu['debitor']}")
    if alt["statuses"] != neu["statuses"]:
        t.append(f"{n}: Bestellungen „" +
                 "“ und „".join(STATUS_TEXT[x] for x in neu["statuses"]) + "“")
    if alt["days"] != neu["days"]:
        t.append(f"{n}: {_tage_text(neu['days'])}")
    if alt["after"] != neu["after"]:
        t.append(f"{n}: nach dem Import " + ("auf „Abgeschlossen“" if neu["after"]
                                              else "Status nicht ändern"))
    if alt["bundling"] != neu["bundling"]:
        t.append(f"{n}: Bündelung auf {BUENDELUNG_TEXT[neu['bundling']]}")
    if alt["nameInNo"] != neu["nameInNo"]:
        t.append(f"{n}: Name in Bestellnummer {'an' if neu['nameInNo'] else 'aus'}")
    if alt["unknownOrt"] != neu["unknownOrt"]:
        t.append(f"{n}: Lieferort ohne feste Adresse → {UNKNOWN_TEXT[neu['unknownOrt']]}")
    if (alt["excelSum"], alt["excelSumBy"], alt["excelSumVed"]) != \
            (neu["excelSum"], neu["excelSumBy"], neu["excelSumVed"]):
        if not neu["excelSum"]:
            t.append(f"{n}: Summenblatt aus")
        else:
            t.append(f"{n}: Summenblatt {'je Lieferort' if neu['excelSumBy'] == 'ort' else 'gesamt'}"
                     f", Veredelungen {'mit' if neu['excelSumVed'] else 'ohne'}")
    if alt["excel"] != neu["excel"]:
        t.append(f"{n}: Zusatzfelder in der Excel " +
                 (", ".join(x["label"] for x in neu["excel"]) or "keine"))
    a_orte = {o["ort"].strip().lower(): o for o in alt["orte"]}
    n_orte = {o["ort"].strip().lower(): o for o in neu["orte"]}
    for k, o in n_orte.items():
        if k not in a_orte:
            t.append(f"{n}: Lieferadresse {o['ort']} angelegt")
        elif {f: a_orte[k].get(f) for f in ORT_FELDER} != {f: o.get(f) for f in ORT_FELDER}:
            t.append(f"{n}: Lieferadresse {o['ort']} geändert")
    for k, o in a_orte.items():
        if k not in n_orte:
            t.append(f"{n}: Lieferadresse {o['ort']} gelöscht")
    return t


# ---------------------------------------------------------------------------
# Die Schnittstelle
# ---------------------------------------------------------------------------

class EinstellungenApi:
    def __init__(self, base_dir: Path | str | None = None, benutzer: str | None = None,
                 uhr=time.time, client_factory=None):
        self._base = Path(base_dir) if base_dir else w.BASE_DIR
        self._benutzer = benutzer or _windows_benutzer()
        self._uhr = uhr
        self._client_factory = client_factory
        self._admin_bis = 0.0
        self._fehlversuche = 0
        self._pause_bis = 0.0

    # --- Pfade ------------------------------------------------------------
    @property
    def _p_einst(self): return self._base / "einstellungen.yaml"
    @property
    def _p_zugang(self): return self._base / "zugang.yaml"
    @property
    def _p_adressen(self): return self._base / "lieferadressen.yaml"
    @property
    def _p_verlauf(self): return self._base / VERLAUF_NAME
    @property
    def _p_backup(self): return self._base / "Backup"

    def _lies(self, p: Path) -> dict:
        if not p.exists():
            return {}
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    def _token(self) -> str:
        return _datei_hash(self._p_einst, self._p_adressen)

    def _ist_admin(self) -> bool:
        return self._uhr() < self._admin_bis

    def _admin_info(self) -> dict:
        rest = max(0, int(self._admin_bis - self._uhr()))
        return {"aktiv": rest > 0, "rest_sekunden": rest,
                "rest_minuten": (rest + 59) // 60}

    # --- Verlauf ----------------------------------------------------------
    def _verlauf_schreiben(self, zeilen: list[str]) -> None:
        neu = not self._p_verlauf.exists()
        stempel = datetime.fromtimestamp(self._uhr()).isoformat(timespec="seconds")
        with self._p_verlauf.open("a", encoding="utf-8", newline="\n") as f:
            if neu:
                f.write(VERLAUF_KOPF)
            for z in zeilen:
                f.write(f"{stempel}\t{self._benutzer}\t{z.replace(chr(9), ' ')}\n")

    def _verlauf_lesen(self, anzahl: int = 200) -> list[dict]:
        if not self._p_verlauf.exists():
            return []
        eintraege = []
        for zeile in self._p_verlauf.read_text(encoding="utf-8").splitlines()[1:]:
            teile = zeile.split("\t", 2)
            if len(teile) != 3:
                continue
            try:
                wann = datetime.fromisoformat(teile[0]).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                wann = teile[0]
            eintraege.append({"when": wann, "who": teile[1], "what": teile[2]})
        return list(reversed(eintraege))[:anzahl]

    # --- für die Oberfläche -------------------------------------------------
    def laden(self) -> dict:
        """Alles, was die Einstellungsseite braucht. Keine Zugangsdaten."""
        if not self._p_einst.exists():
            return {"ok": False, "fehler":
                    "einstellungen.yaml fehlt. Erst die Konfiguration aufteilen: "
                    "python migrate_config.py --probelauf"}
        try:
            cfg = self._lies(self._p_einst)
            zugang = self._lies(self._p_zugang)
            adressen = self._lies(self._p_adressen)
        except yaml.YAMLError as e:
            return {"ok": False, "fehler": f"Datei fehlerhaft: {e}"}
        z_shops = zugang.get("shops") or {}
        shops = []
        for i, s in enumerate(cfg.get("shops") or []):
            sid = s.get("id") or w.shop_id_aus_name(s.get("name"))
            creds = z_shops.get(sid) or z_shops.get(s.get("name")) or {}
            da = bool(creds.get("consumer_key") and creds.get("consumer_secret"))
            shops.append(shop_zu_ui(s, cfg, adressen.get(s.get("name")) or {}, i, da))
        pw = ((zugang.get("admin") or {}).get("password") or {})
        return {
            "ok": True,
            "shops": shops,
            "global": {"prefixes": _praefixe_ui(cfg.get("veredelung_prefixes"))},
            "log": self._verlauf_lesen(),
            "user": self._benutzer,
            "stand": w.programmstand(),
            "admin": self._admin_info(),
            "adminPasswortGesetzt": bool(pw.get("hash")),
            "token": self._token(),
        }

    def admin_status(self) -> dict:
        return self._admin_info()

    def admin_abmelden(self) -> dict:
        self._admin_bis = 0.0
        return self._admin_info()

    def admin_anmelden(self, passwort: str) -> dict:
        jetzt = self._uhr()
        if jetzt < self._pause_bis:
            return {"ok": False, "fehler":
                    f"Zu viele Fehlversuche. Noch {int(self._pause_bis - jetzt) + 1} s warten."}
        admin = (self._lies(self._p_zugang).get("admin") or {})
        satz = admin.get("password") or {}
        if not satz.get("hash"):
            return {"ok": False, "fehler": "Kein Admin-Passwort hinterlegt "
                    "(python migrate_config.py --admin-password …)."}
        erlaubt = [str(u).lower() for u in admin.get("users") or []]
        if erlaubt and self._benutzer.lower() not in erlaubt:
            logging.warning("Admin-Anmeldung abgelehnt: %s steht nicht in "
                            "admin.users.", self._benutzer)
            return {"ok": False, "fehler":
                    f"{self._benutzer} ist nicht als Admin eingetragen."}
        if verify_admin_password(passwort or "", satz):
            self._fehlversuche = 0
            self._admin_bis = jetzt + ADMIN_MINUTEN * 60
            logging.info("Admin-Modus für %s bis %s.", self._benutzer,
                         datetime.fromtimestamp(self._admin_bis).strftime("%H:%M"))
            return {"ok": True, **self._admin_info()}
        self._fehlversuche += 1
        logging.warning("Admin-Anmeldung: falsches Passwort (%s, Versuch %d).",
                        self._benutzer, self._fehlversuche)
        if self._fehlversuche >= FEHLVERSUCHE_BIS_PAUSE:
            self._fehlversuche = 0
            self._pause_bis = jetzt + PAUSE_SEKUNDEN
            return {"ok": False, "fehler": "Falsches Passwort. 1 Minute Pause."}
        hinweis = " Nach 5 Versuchen 1 Minute Pause." if self._fehlversuche >= 3 else ""
        return {"ok": False, "fehler": "Falsches Passwort." + hinweis}

    def sichern(self, daten: dict) -> dict:
        try:
            gesichert = self._sichern(daten or {})
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}
        stand = self.laden()
        stand["gesichert"] = gesichert
        return stand

    def versandarten(self, shop_id: str) -> dict:
        """Versandarten des Shops für den Abgleich in der Oberfläche. Nur lesend."""
        try:
            cfg, _ = w.load_config(self._base)
            shop = next((s for s in cfg.get("shops") or []
                         if (s.get("id") or w.shop_id_aus_name(s.get("name"))) == shop_id), None)
            if shop is None:
                return {"ok": False, "fehler": "Shop nicht gefunden."}
            if w.fehlende_zugangsdaten(shop):
                return {"ok": False, "fehler": "Kein Zugang hinterlegt."}
            client = (self._client_factory or w.WooClient)(
                shop["url"], shop["consumer_key"], shop["consumer_secret"])
            return {"ok": True, "methoden": client.get_shipping_methods()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "fehler": f"Versandarten nicht abrufbar ({type(e).__name__})."}

    # --- Sichern ------------------------------------------------------------
    def _sichern(self, daten: dict) -> list[str]:
        if not self._p_einst.exists():
            raise _Fehler("einstellungen.yaml fehlt.")
        if daten.get("token") != self._token():
            raise _Fehler("Die Einstellungen wurden inzwischen an einem anderen "
                          "Rechner geändert. Bitte neu laden — deine Änderungen "
                          "sind nicht gesichert.")
        cfg = self._lies(self._p_einst)
        adressen = self._lies(self._p_adressen)
        zugang = self._lies(self._p_zugang)
        alt_stand = self.laden()
        alt_ui = {s["id"]: s for s in alt_stand["shops"]}
        neu_shops = daten.get("shops") or []
        neu_ids = [s.get("id") for s in neu_shops]

        if sorted(neu_ids) != sorted(alt_ui):
            raise _Fehler("Shops hinzufügen oder entfernen geht über den "
                          "Shop-Assistenten (Admin).")

        # Nur geänderte Shops prüfen — ein Altwert in einem anderen Shop soll
        # das Sichern nicht blockieren.
        fehler = [f for s in neu_shops
                  if _ohne_anzeige(s) != _ohne_anzeige(alt_ui.get(s.get("id")))
                  for f in _pruefe_shop(s)]
        neu_prefixe = _praefixe_ui((daten.get("global") or {}).get("prefixes"))
        if len({p["prefix"] for p in neu_prefixe}) != len(neu_prefixe):
            fehler.append("Veredelungen: Präfix doppelt.")
        if fehler:
            raise _Fehler("Nicht gesichert:\n" + "\n".join(fehler))

        geschuetzt = [f"{s['name']}: Debitornummer" for s in neu_shops
                      if s["debitor"] != alt_ui[s["id"]]["debitor"]]
        if neu_prefixe != alt_stand["global"]["prefixes"]:
            geschuetzt.append("Veredelungen")
        if geschuetzt and not self._ist_admin():
            raise _Fehler("Admin-Modus nötig für: " + ", ".join(geschuetzt) +
                          ". Nichts gesichert.")

        aenderungen: list[str] = []
        neu_cfg = copy.deepcopy(cfg)
        neu_adressen = copy.deepcopy(adressen)
        ids_ergaenzt = False
        for shop in neu_cfg.get("shops") or []:
            sid = shop.get("id") or w.shop_id_aus_name(shop.get("name"))
            if not shop.get("id"):
                ids_ergaenzt = True
                neu = {"id": sid, **shop}
                shop.clear()
                shop.update(neu)
            neu_ui = next(s for s in neu_shops if s["id"] == sid)
            _ui_auf_shop(neu_ui, alt_ui[sid], shop, cfg)
            beschr = _beschreibe(alt_ui[sid], neu_ui)
            aenderungen += beschr
            if neu_ui["orte"] != alt_ui[sid]["orte"]:
                orte = _orte_auf_adressen(neu_ui["orte"])
                if orte:
                    neu_adressen[shop["name"]] = orte
                else:
                    neu_adressen.pop(shop["name"], None)

        if neu_prefixe != alt_stand["global"]["prefixes"]:
            neu_cfg["veredelung_prefixes"] = [dict(p) for p in neu_prefixe]
            alt_p = {p["prefix"]: p["label"] for p in alt_stand["global"]["prefixes"]}
            neu_p = {p["prefix"]: p["label"] for p in neu_prefixe}
            for p, label in neu_p.items():
                if p not in alt_p:
                    aenderungen.append(f"Veredelungen: {p} {label} ergänzt")
                elif alt_p[p] != label:
                    aenderungen.append(f"Veredelungen: {p} heißt jetzt {label}")
            for p, label in alt_p.items():
                if p not in neu_p:
                    aenderungen.append(f"Veredelungen: {p} {label} entfernt")

        if not aenderungen:
            return []

        # Zugang muss auch nach dem Ergänzen der ids gefunden werden: Der
        # Lader fällt auf den Namen zurück, solange zugang.yaml alt ist.
        if ids_ergaenzt:
            aenderungen.append("Feste Shop-ids ergänzt")
            if any(sid not in (zugang.get("shops") or {}) for sid in alt_ui):
                logging.info("zugang.yaml noch nach Namen — python migrate_config.py "
                             "--shop-ids")

        stempel = datetime.fromtimestamp(self._uhr()).strftime("%Y-%m-%d_%H%M%S")
        self._p_backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._p_einst, self._p_backup / f"einstellungen_{stempel}.yaml")
        if neu_adressen != adressen and self._p_adressen.exists():
            shutil.copy2(self._p_adressen, self._p_backup / f"lieferadressen_{stempel}.yaml")

        kopf = (f"# Einstellungen WooCommerce → CDH — gesichert über die Oberfläche\n"
                f"# {datetime.fromtimestamp(self._uhr()):%d.%m.%Y %H:%M} von {self._benutzer}."
                f" Vorherige Fassung in Backup\\.\n\n")
        _schreibe_atomar(self._p_einst, kopf + _yaml(neu_cfg))
        if neu_adressen != adressen:
            akopf = ("# Feste Lieferadressen je Shop und Lieferort\n"
                     "# Gepflegt über die Oberfläche bzw. das Adressen-Tool. "
                     "Enthält keine Zugangsdaten.\n\n")
            _schreibe_atomar(self._p_adressen, akopf + _yaml(neu_adressen))
        self._verlauf_schreiben(aenderungen)
        logging.info("Einstellungen gesichert von %s: %s", self._benutzer,
                     "; ".join(aenderungen))
        return aenderungen
