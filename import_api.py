"""
TEXMA — Import als Python-Schnittstelle für die Oberfläche (Welle 6)
=====================================================================

Tab „Import“ in ui/index.html ruft diese Methoden über window.pywebview.api:

  abrufen(trotzdem)        nur lesend: Prüfansicht (woo_to_cdh.abrufen)
  importieren(keys)        startet den Import der ausgewählten Einheiten im
                           Hintergrund und kehrt sofort zurück
  import_status()          Fortschritt (die Oberfläche fragt regelmäßig)
  import_abbrechen()       Abbruch — greift erst zwischen zwei Einheiten
  excel_uebersicht(scope)  Excel der abgerufenen Bestellungen, ohne Import
  letzte_wex(anzahl)       zuletzt erzeugte WEX-Dateien
  erneut_uebergeben(datei) eine WEX-Datei noch einmal an CDH geben
  ordner_zeigen(welcher)   wex-archiv / excel-archiv / Übersichten öffnen

Der Import selbst ist genau der der Konsolen-EXE (woo_to_cdh.importieren):
WEX schreiben, Excel, exported.log, Markierung, Status, start_cdh_wex_import.
Genau ein CDH-Fenster zur Zeit — dafür sorgt start_cdh_wex_import. Über
Rechner hinweg hält der Import running.lock (Welle 4) für den ganzen Lauf.

Nur Namen ohne führenden Unterstrich sind für die Oberfläche sichtbar.
"""

from __future__ import annotations

import copy
import heapq
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import woo_to_cdh as w

LETZTE_WEX_ANZAHL = 15
ABGESCHLOSSEN = {"fertig", "pruefen", "nicht_uebergeben", "doppelt"}


def _oeffnen_standard(pfad: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(pfad))          # noqa: S606 — Explorer / Excel öffnen
    else:
        logging.info("Öffnen nur unter Windows: %s", pfad)


def _zahl(x):
    v = w._to_float(x)
    return None if v is None else round(v, 2)


# ---------------------------------------------------------------------------
# Prüfergebnis → JSON für die Oberfläche
# ---------------------------------------------------------------------------

def _person(o: dict) -> str:
    for block in (o.get("shipping") or {}, o.get("billing") or {}):
        n = f"{block.get('first_name', '')} {block.get('last_name', '')}".strip()
        if n:
            return n
    return ""


def _lieferort(o: dict) -> str:
    lines = o.get("shipping_lines") or []
    return (lines[0].get("method_title") or "").strip() if lines else ""


def _bestellung_json(o: dict, s: w.ShopErgebnis) -> dict:
    pos = []
    for item in o.get("line_items") or []:
        try:
            ek, vk = w.extract_ek_vk(s.client, item, s.price_cache)   # aus dem Cache
        except Exception:  # noqa: BLE001 — Anzeige, kein Abbruch
            ek, vk = None, None
        name = (item.get("name") or "").strip()
        variante = w._extract_variant_text(item)
        if variante and name.endswith(f" - {variante}"):
            name = name[: -(len(variante) + 3)]
        sku = (item.get("sku") or "").strip()
        if item.get("_zubehoer"):
            name += " (Zubehör, automatisch)"
        pos.append({"q": w._menge(item.get("quantity")), "sku": sku, "art": name,
                    "v": variante, "vk": _zahl(vk), "ek": _zahl(ek),
                    "ved": w._is_veredelung(sku)})
    return {"no": str(o.get("number") or o.get("id")), "name": _person(o),
            "ort": _lieferort(o), "pos": pos,
            "sum": round(sum((p["vk"] or 0) * p["q"] for p in pos), 2)}


def _cdh_json(e: w.Einheit, feste_orte: set) -> dict:
    """Was tatsächlich an CDH geht — aus den fertigen WEX-Daten."""
    d = e.wex_data
    if d.get("delivery_leer"):
        zeilen = []
        hinweis = "Keine Lieferanschrift — CDH liefert an die Adresse aus dem Kundenstamm."
    else:
        land_plz = "-".join(x for x in (d.get("del_country"), d.get("del_postcode")) if x)
        zeilen = [x for x in (d.get("del_name1"), d.get("del_name2"), d.get("del_street"),
                              f"{land_plz} {d.get('del_city') or ''}".strip()) if x]
        ort = (d.get("mode_of_shipment") or "").strip().lower()
        if ort and ort in feste_orte:
            hinweis = f"Feste Lieferadresse für {d.get('mode_of_shipment')}"
        elif e.art == "lieferort":
            hinweis = next((t for t in e.warnungen if "Lieferadresse" in t or
                            "Versandadresse" in t), "Lieferanschrift laut Einstellung")
        else:
            hinweis = "Versandadresse aus der Bestellung"
    positionen = [{"q": p.get("quantity"), "sku": p.get("article_no") or "",
                   "text": " · ".join(x for x in (p.get("description"), p.get("variant_text"),
                                                   "Zubehör, automatisch" if p.get("zubehoer") else "")
                                      if x),
                   "vk": _zahl(p.get("selling_price")), "trenner": not p.get("article_no")}
                  for p in w._aggregate_veredelungen(d.get("positions") or [])]
    return {"kunde": f"Kunde {d.get('datev_no')}",
            "kundeHinweis": "Anschrift aus dem CDH-Kundenstamm",
            "auftrag": d.get("order_no_wex") or d.get("order_no"),
            "lieferart": d.get("mode_of_shipment") or "",
            "lieferung": zeilen, "lieferHinweis": hinweis, "positionen": positionen}


# ---------------------------------------------------------------------------
# Die Schnittstelle
# ---------------------------------------------------------------------------

class ImportApi:
    def __init__(self, base_dir: Path | str | None = None, client_factory=None,
                 oeffnen=None):
        self._base = Path(base_dir) if base_dir else w.BASE_DIR
        self._client_factory = client_factory
        self._init_import(oeffnen)

    def _init_import(self, oeffnen=None) -> None:
        self._oeffnen = oeffnen or _oeffnen_standard
        self._cfg: dict = {}
        self._pruef: w.Pruefergebnis | None = None
        self._einheiten: dict[str, w.Einheit] = {}
        self._job: dict | None = None
        self._abbruch = threading.Event()
        self._thread: threading.Thread | None = None
        self._sperre = threading.Lock()

    # --- Hilfen -------------------------------------------------------------
    def _laeuft(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _ordner(self, welcher: str) -> Path:
        cfg = self._cfg or w.load_config(self._base)[0]
        wex = Path(cfg.get("cdh_import_folder") or "./output")
        excel = Path(cfg.get("excel_export_folder") or wex.parent / "excel-archiv")
        return {"wex": wex, "excel": excel, "uebersicht": excel / "uebersicht"}[welcher]

    def _shop_id(self, shop_cfg: dict) -> str:
        return shop_cfg.get("id") or w.shop_id_aus_name(shop_cfg.get("name"))

    # --- Abrufen ------------------------------------------------------------
    def abrufen(self, trotzdem=None) -> dict:
        """Prüfansicht aller eingeschalteten Shops. Schreibt nichts."""
        if self._laeuft():
            return {"ok": False, "fehler": "Ein Import läuft noch."}
        try:
            cfg, _ = w.load_config(self._base)
        except FileNotFoundError:
            return {"ok": False, "fehler": "Keine Konfiguration gefunden."}
        w.praefixe_uebernehmen(cfg)
        trotzdem = set(trotzdem or [])
        namen = {s.get("name") for s in cfg.get("shops") or [] if self._shop_id(s) in trotzdem}
        pruef = w.abrufen(cfg, trotzdem=namen, client_factory=self._client_factory)
        feste = {shop: set(orte) for shop, orte in w.load_delivery_addresses().items()}

        shops_json = []
        einheiten: dict[str, w.Einheit] = {}
        for s in pruef.shops:
            gate = s.warnungen[0] if s.uebersprungen else ""
            shop = {"id": self._shop_id(s.shop_cfg), "name": s.shop,
                    "uebersprungen": bool(s.uebersprungen), "stichtag": gate,
                    "sperren": [w.ohne_schluessel(t) for t in s.sperren],
                    "warnungen": [w.ohne_schluessel(t) for t in s.warnungen if t != gate],
                    "fehler": s.fehler, "einheiten": []}
            for e in s.einheiten:
                einheiten[e.key] = e
                shop["einheiten"].append({
                    "key": e.key, "art": e.art, "titel": e.titel,
                    "datei": e.dateiname() + ".wex",
                    "warnungen": list(e.warnungen), "sperren": list(e.sperren),
                    "gesperrt": bool(e.sperren or s.sperren),
                    "orders": [_bestellung_json(o, s) for o in e.orders],
                    "cdh": _cdh_json(e, feste.get(s.shop, set())),
                })
            shops_json.append(shop)

        with self._sperre:
            self._cfg, self._pruef, self._einheiten = cfg, pruef, einheiten
            self._job = None
        lock = w.lock_info()
        return {"ok": True, "zeit": pruef.zeit.isoformat(timespec="seconds"),
                "shops": shops_json,
                "sperre": w.lock_text(lock) if lock else ""}

    # --- Importieren --------------------------------------------------------
    def importieren(self, keys) -> dict:
        with self._sperre:
            if self._laeuft():
                return {"ok": False, "fehler": "Ein Import läuft schon."}
            if not keys:
                return {"ok": False, "fehler": "Nichts ausgewählt."}
            fehlend = [k for k in keys if k not in self._einheiten]
            if fehlend:
                return {"ok": False, "fehler": "Die Auswahl passt nicht mehr zum "
                        "Abruf. Bitte neu abrufen."}
            auswahl = [self._einheiten[k] for k in keys]
            gesperrt = [e.titel for e in auswahl if e.sperren or e.shop_ergebnis.sperren]
            if gesperrt:
                return {"ok": False, "fehler": "Gesperrt: " + ", ".join(gesperrt)}
            lock = w.lock_info()
            if lock:
                return {"ok": False, "fehler": w.lock_text(lock) +
                        " — bitte warten, bis er fertig ist."}
            self._abbruch = threading.Event()
            self._job = {
                "art": "import", "laeuft": True, "nr": 0, "gesamt": len(auswahl),
                "abgebrochen": False, "fehler": "", "ende": "",
                "einheiten": [{"key": e.key, "shop": e.shop, "titel": e.titel,
                               "art": e.art, "anzahl": len(e.orders),
                               "datei": e.dateiname() + ".wex", "status": "wartet",
                               "meldung": "", "exit": None} for e in auswahl],
            }
            self._thread = threading.Thread(target=self._lauf_import, args=(auswahl,),
                                            daemon=True, name="import")
            self._thread.start()
        return {"ok": True}

    def _eintrag(self, key: str) -> dict:
        return next(x for x in self._job["einheiten"] if x["key"] == key)

    def _lauf_import(self, auswahl: list) -> None:
        job = self._job
        if not w.acquire_lock():
            lock = w.lock_info()
            job.update(laeuft=False, fehler=(w.lock_text(lock) if lock else
                       "Importsperre konnte nicht gesetzt werden."))
            return
        imp = w.Importergebnis()

        def fortschritt(nr, gesamt, e, phase):
            job["nr"] = nr
            eintrag = self._eintrag(e.key)
            if phase == "start":
                eintrag["status"] = "laeuft"
                return
            p = next((x for x in reversed(imp.protokoll) if x["key"] == e.key), None)
            if p:
                eintrag.update(status=p["status"], meldung=p["meldung"], exit=p["exit"],
                               datei=p["datei"] or eintrag["datei"])
            else:
                eintrag["status"] = "fehler"
            if eintrag["status"] in ABGESCHLOSSEN:
                self._einheiten.pop(e.key, None)       # nicht noch einmal anbieten

        try:
            w.importieren(auswahl, fortschritt, self._abbruch, ergebnis=imp)
        except Exception as ex:  # noqa: BLE001
            logging.exception("Import aus der Oberfläche abgebrochen: %s", ex)
            job["fehler"] = w.ohne_schluessel(ex)
        finally:
            w.release_lock()
            for x in job["einheiten"]:
                if x["status"] in ("wartet", "laeuft"):
                    x["status"] = "abgebrochen" if imp.abgebrochen else x["status"]
            job.update(laeuft=False, abgebrochen=imp.abgebrochen,
                       ende=datetime.now().isoformat(timespec="seconds"),
                       bestellungen=imp.ok, fehler_anzahl=imp.fehler)
            logging.info("Import aus der Oberfläche: %d Bestellung(en), %d Fehler%s.",
                         imp.ok, imp.fehler, ", abgebrochen" if imp.abgebrochen else "")

    def import_status(self) -> dict:
        if not self._job:
            return {"laeuft": False, "art": ""}
        return copy.deepcopy(self._job)

    def import_abbrechen(self) -> dict:
        if self._laeuft():
            self._abbruch.set()
            return {"ok": True, "hinweis": "Wird nach dem laufenden Auftrag beendet."}
        return {"ok": False}

    # --- Excel-Übersicht ------------------------------------------------------
    def excel_uebersicht(self, scope: str = "__all") -> dict:
        if not self._pruef:
            return {"ok": False, "fehler": "Erst abrufen."}
        shops = [s for s in self._pruef.shops if s.einheiten]
        if scope != "__all":
            shops = [s for s in shops if self._shop_id(s.shop_cfg) == scope]
        if not shops:
            return {"ok": False, "fehler": "Keine Bestellungen für die Übersicht."}
        stempel = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        teil = "alle-Shops" if scope == "__all" else w._sanitize_for_filename(shops[0].shop)
        pfad = self._ordner("uebersicht") / f"Uebersicht-{teil}-{stempel}.xlsx"
        if w.write_excel_uebersicht(pfad, shops) is None:
            return {"ok": False, "fehler": "Excel nicht verfügbar (openpyxl fehlt)."}
        blaetter = [s.shop for s in shops] + [f"Summe {s.shop}" for s in shops
                                              if s.shop_cfg.get("excel_summary")]
        orders = [o for s in shops for e in s.einheiten for o in e.orders]
        return {"ok": True, "datei": pfad.name, "bestellungen": len(orders),
                "zeilen": sum(len(o.get("line_items") or []) for o in orders),
                "blaetter": blaetter}

    def ordner_zeigen(self, welcher: str) -> dict:
        try:
            pfad = self._ordner(welcher)
        except (KeyError, FileNotFoundError):
            return {"ok": False, "fehler": "Unbekannter Ordner."}
        if not pfad.exists():
            return {"ok": False, "fehler": f"Ordner fehlt: {pfad}"}
        self._oeffnen(pfad)
        return {"ok": True}

    # --- Letzte WEX-Dateien ---------------------------------------------------
    def _exportlog_je_datei(self) -> dict:
        je: dict = {}
        p = w.EXPORTED_LOG_PATH
        if not p.exists():
            return je
        for zeile in p.read_text(encoding="utf-8").splitlines()[1:]:
            teile = zeile.split("\t")
            if len(teile) >= 5:
                eintrag = je.setdefault(teile[4].strip(), {"shop": teile[1], "orders": []})
                eintrag["orders"].append(teile[3])
        return je

    def letzte_wex(self, anzahl: int = LETZTE_WEX_ANZAHL) -> dict:
        try:
            ordner = self._ordner("wex")
        except FileNotFoundError:
            return {"ok": False, "fehler": "Keine Konfiguration gefunden."}
        if not ordner.exists():
            return {"ok": True, "dateien": []}
        # os.scandir statt glob + stat: Unter Windows liefert das Verzeichnis-
        # Listing Größe und Zeit gleich mit — auf V: ein Netzaufruf für den
        # ganzen Ordner statt mehrerer je Datei (das wex-archiv ist groß).
        with os.scandir(ordner) as it:
            eintraege = [(e.stat().st_mtime, e.name) for e in it
                         if e.name.lower().endswith(".wex") and e.is_file()]
        dateien = heapq.nlargest(max(1, int(anzahl)), eintraege)
        info = self._exportlog_je_datei()
        return {"ok": True, "dateien": [
            {"datei": name,
             "zeit": datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M"),
             "shop": info.get(name, {}).get("shop", ""),
             "orders": info.get(name, {}).get("orders", [])}
            for mtime, name in dateien]}

    def erneut_uebergeben(self, datei: str) -> dict:
        """Eine WEX-Datei aus dem Archiv noch einmal an CDH geben — z. B. wenn
        das CDH-Fenster ohne „Ende“ geschlossen wurde. Ändert nichts im Shop."""
        with self._sperre:
            if self._laeuft():
                return {"ok": False, "fehler": "Ein Import läuft gerade."}
            if not re.fullmatch(r"[^/\\:]+\.wex", str(datei or ""), re.I):
                return {"ok": False, "fehler": "Ungültiger Dateiname."}
            try:
                pfad = self._ordner("wex") / datei
            except FileNotFoundError:
                return {"ok": False, "fehler": "Keine Konfiguration gefunden."}
            if not pfad.is_file():
                return {"ok": False, "fehler": f"{datei} liegt nicht im wex-archiv."}
            self._job = {"art": "erneut", "laeuft": True, "nr": 1, "gesamt": 1,
                         "abgebrochen": False, "fehler": "", "ende": "",
                         "einheiten": [{"key": datei, "shop": "", "titel": datei,
                                        "art": "datei", "anzahl": 0, "datei": datei,
                                        "status": "laeuft", "meldung": "", "exit": None}]}
            cfg = self._cfg or w.load_config(self._base)[0]
            self._thread = threading.Thread(target=self._lauf_erneut, args=(pfad, cfg),
                                            daemon=True, name="erneut")
            self._thread.start()
        return {"ok": True}

    def _lauf_erneut(self, pfad: Path, cfg: dict) -> None:
        job = self._job
        eintrag = job["einheiten"][0]
        try:
            logging.info("Erneut an CDH übergeben (Oberfläche): %s", pfad.name)
            ok = w.start_cdh_wex_import(pfad, cfg)
            code = w.CDH_LETZTER_EXIT if ok else None
            if not ok:
                eintrag.update(status="nicht_uebergeben",
                               meldung="CDH nicht gestartet — siehe Log.")
            elif code not in (0, None):
                eintrag.update(status="pruefen", exit=code,
                               meldung=f"CDH meldet Exit {code} — bitte in CDH prüfen.")
            else:
                eintrag.update(status="fertig", exit=code)
        except Exception as ex:  # noqa: BLE001
            eintrag.update(status="fehler", meldung=w.ohne_schluessel(ex))
        finally:
            job.update(laeuft=False, ende=datetime.now().isoformat(timespec="seconds"))

    def _warten(self, sekunden: float = 10.0) -> None:
        """Nur für Tests: bis der Hintergrundlauf fertig ist."""
        ende = time.time() + sekunden
        while self._laeuft() and time.time() < ende:
            time.sleep(0.01)
