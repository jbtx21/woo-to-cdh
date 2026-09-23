"""Welle 6: Import-Schnittstelle der Oberfläche (import_api).

WooCommerce ist FakeWoo (conftest), CDH ein Ersatz für start_cdh_wex_import.
Alles in tmp_path; die Sperre running.lock ebenfalls.
"""
import json
import threading
from datetime import datetime

import pytest
import yaml

import import_api as ia
import woo_to_cdh as w
from conftest import FIXTURES, FakeWoo


@pytest.fixture
def cdh(monkeypatch):
    """Ersatz für CDH: protokolliert Aufrufe; Verhalten je Test einstellbar."""
    zustand = {"aufrufe": [], "exit": 0, "uebergeben": True, "warte": None}

    def start(path, cfg):
        zustand["aufrufe"].append(path.name)
        if zustand["warte"]:
            zustand["warte"].wait(5)
        w.CDH_LETZTER_EXIT = zustand["exit"] if zustand["uebergeben"] else None
        return zustand["uebergeben"]
    monkeypatch.setattr(w, "start_cdh_wex_import", start)
    return zustand


@pytest.fixture
def api(tmp_path, monkeypatch, orders, cdh):
    FakeWoo.orders_by_url = {
        "https://shop.example/einzeln/": [orders["einzeln"]],
        "https://shop.example/agrar/": orders["trenn"],
        "https://shop.example/mitarbeiter/": orders["mitarbeitershop"],
    }
    FakeWoo.puts, FakeWoo.gets, FakeWoo.zones_by_url = [], [], {}
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", FIXTURES / "lieferadressen_test.yaml")
    monkeypatch.setattr(w, "LOCK_PATH", tmp_path / "running.lock")
    shop = {"datev_no": 10000, "order_type": "AB"}
    einst = {
        "cdh_import_folder": str(tmp_path / "wex"),
        "excel_export_folder": str(tmp_path / "excel"),
        "status_after_export": "completed", "order_no_with_name": True,
        "shops": [
            {**shop, "id": "beispiel", "name": "Beispiel-Shop", "url": "https://shop.example/einzeln/"},
            {**shop, "id": "agrar", "name": "Agrar-Shop", "url": "https://shop.example/agrar/",
             "combine_by_delivery": True},
            {**shop, "id": "mitarbeiter", "name": "Mitarbeiter-Shop",
             "url": "https://shop.example/mitarbeiter/", "combine_by_delivery": True,
             "aggregate_all_positions": True, "excel_summary": True},
        ],
    }
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump(einst, sort_keys=False), encoding="utf-8")
    zugang = {"shops": {sid: {"consumer_key": "ck_TEST", "consumer_secret": "cs_TEST"}
                        for sid in ("beispiel", "agrar", "mitarbeiter")}}
    (tmp_path / "zugang.yaml").write_text(yaml.safe_dump(zugang), encoding="utf-8")
    geoeffnet = []
    a = ia.ImportApi(tmp_path, client_factory=FakeWoo, oeffnen=geoeffnet.append)
    a.geoeffnet, a.tmp = geoeffnet, tmp_path
    yield a
    a._abbruch.set()
    a._warten()


def _keys(pruef, shop_id=None):
    return [e["key"] for s in pruef["shops"] if shop_id in (None, s["id"])
            for e in s["einheiten"] if not e["gesperrt"]]


def _einheit(pruef, titel):
    return next(e for s in pruef["shops"] for e in s["einheiten"] if e["titel"] == titel)


# --- abrufen ----------------------------------------------------------------

def test_abrufen_pruefansicht(api):
    p = api.abrufen()
    assert p["ok"] and p["sperre"] == ""
    assert [s["id"] for s in p["shops"]] == ["beispiel", "agrar", "mitarbeiter"]
    assert [e["titel"] for e in p["shops"][2]["einheiten"]] == ["Seewalchen", "Lenzing"]
    einzel = _einheit(p, "402")
    o = einzel["orders"][0]
    assert o["no"] == "402" and o["name"] and o["pos"][0]["vk"] == 24.9 and o["pos"][0]["ek"] == 12.1
    assert einzel["cdh"]["kunde"] == "Kunde 10000"
    assert einzel["cdh"]["lieferHinweis"] == "Versandadresse aus der Bestellung"
    assert einzel["cdh"]["auftrag"].startswith("402 ")
    bondorf = _einheit(p, "Bondorf")
    assert bondorf["cdh"]["lieferung"] == [] and "Kundenstamm" in bondorf["cdh"]["lieferHinweis"]
    assert any(x["trenner"] for x in bondorf["cdh"]["positionen"])
    lenzing = _einheit(p, "Lenzing")
    assert lenzing["cdh"]["lieferHinweis"] == "Feste Lieferadresse für Lenzing"
    assert "Werkplatz 1" in lenzing["cdh"]["lieferung"]
    assert not (api.tmp / "wex").exists() and FakeWoo.puts == []


def test_abrufen_ohne_schluessel(api):
    assert "ck_TEST" not in json.dumps(api.abrufen())


def test_stichtag_und_trotzdem(api):
    einst = yaml.safe_load((api.tmp / "einstellungen.yaml").read_text(encoding="utf-8"))
    einst["shops"][0]["import_on_days"] = [datetime.now().day % 28 + 1]
    (api.tmp / "einstellungen.yaml").write_text(yaml.safe_dump(einst), encoding="utf-8")
    p = api.abrufen()
    b = p["shops"][0]
    assert b["uebersprungen"] and b["stichtag"].startswith("Import nur am") and b["einheiten"] == []
    p = api.abrufen(["beispiel"])
    assert [e["titel"] for e in p["shops"][0]["einheiten"]] == ["402"]


# --- importieren --------------------------------------------------------------

def test_importieren_im_hintergrund(api, cdh):
    p = api.abrufen()
    keys = _keys(p, "agrar") + _keys(p, "beispiel")
    assert api.importieren(keys) == {"ok": True}
    api._warten()
    st = api.import_status()
    assert not st["laeuft"] and st["bestellungen"] == 3 and st["fehler_anzahl"] == 0
    assert [x["status"] for x in st["einheiten"]] == ["fertig", "fertig"]
    datum = datetime.now().strftime("%Y-%m-%d")
    assert cdh["aufrufe"] == [f"orders-{datum}-agrar-shop-Bondorf.wex", f"orders-{datum}-402.wex"]
    assert w.load_exported_log() == {"3939|3939", "3940|3940", "1402|402"}
    assert not w.LOCK_PATH.exists()                            # Sperre wieder frei
    assert "passt nicht mehr" in api.importieren(keys)["fehler"]  # nicht doppelt


def test_status_waehrend_des_laufs(api, cdh):
    cdh["warte"] = threading.Event()
    p = api.abrufen()
    api.importieren(_keys(p, "mitarbeiter"))
    for _ in range(200):
        st = api.import_status()
        if st["einheiten"][0]["status"] == "laeuft":
            break
        threading.Event().wait(0.01)
    assert st["laeuft"] and st["nr"] == 1 and st["gesamt"] == 2
    assert w.LOCK_PATH.exists()                                # Sperre über den ganzen Lauf
    assert "läuft schon" in api.importieren(_keys(p, "beispiel"))["fehler"]
    assert "läuft noch" in api.abrufen()["fehler"]
    cdh["warte"].set()
    api._warten()
    assert [x["status"] for x in api.import_status()["einheiten"]] == ["fertig", "fertig"]


def test_abbruch_zwischen_zwei_auftraegen(api, cdh):
    cdh["warte"] = threading.Event()
    p = api.abrufen()
    api.importieren(_keys(p))
    while not cdh["aufrufe"]:
        threading.Event().wait(0.01)
    assert api.import_abbrechen()["ok"]
    cdh["warte"].set()
    api._warten()
    st = api.import_status()
    assert st["abgebrochen"]
    assert [x["status"] for x in st["einheiten"]] == ["fertig"] + ["wartet"] * 3 or \
        [x["status"] for x in st["einheiten"]] == ["fertig"] + ["abgebrochen"] * 3
    assert len(cdh["aufrufe"]) == 1
    # Nicht bearbeitete bleiben importierbar
    assert api.importieren(_keys(p)[1:2])["ok"]


def test_exit_code_und_nicht_uebergeben(api, cdh):
    p = api.abrufen()
    cdh["exit"] = 3
    api.importieren(_keys(p, "beispiel"))
    api._warten()
    x = api.import_status()["einheiten"][0]
    assert x["status"] == "pruefen" and x["exit"] == 3 and "Exit 3" in x["meldung"]

    cdh["uebergeben"] = False
    api.importieren(_keys(p, "agrar"))
    api._warten()
    x = api.import_status()["einheiten"][0]
    assert x["status"] == "nicht_uebergeben" and "erneut" in x["meldung"]
    assert x["datei"].endswith("Bondorf.wex")


def test_sperre_anderer_rechner(api):
    w.LOCK_PATH.write_text(json.dumps({"rechner": "PC-LAGER", "benutzer": "m.mueller",
                                       "seit": "2026-10-01T09:14:00"}), encoding="utf-8")
    p = api.abrufen()
    assert p["sperre"] == "Import läuft an PC-LAGER (m.mueller) seit 09:14"
    erg = api.importieren(_keys(p, "beispiel"))
    assert not erg["ok"] and "PC-LAGER" in erg["fehler"]


def test_gesperrte_einheit(api):
    einst = yaml.safe_load((api.tmp / "einstellungen.yaml").read_text(encoding="utf-8"))
    einst["shops"][1]["unknown_delivery"] = "sperren"
    (api.tmp / "einstellungen.yaml").write_text(yaml.safe_dump(einst), encoding="utf-8")
    p = api.abrufen()
    e = _einheit(p, "Bondorf")
    assert e["gesperrt"] and e["sperren"]
    assert "Gesperrt: Bondorf" in api.importieren([e["key"]])["fehler"]


# --- Excel, letzte WEX, erneut übergeben -------------------------------------

def test_excel_uebersicht(api):
    assert not api.excel_uebersicht()["ok"]                     # vor dem Abruf
    api.abrufen()
    erg = api.excel_uebersicht()
    assert erg["ok"] and erg["bestellungen"] == 6
    assert erg["blaetter"] == ["Beispiel-Shop", "Agrar-Shop", "Mitarbeiter-Shop",
                               "Summe Mitarbeiter-Shop"]
    assert (api.tmp / "excel" / "uebersicht" / erg["datei"]).exists()
    erg = api.excel_uebersicht("agrar")
    assert erg["blaetter"] == ["Agrar-Shop"] and "Agrar-Shop" in erg["datei"]
    assert not w.EXPORTED_LOG_PATH.exists()                     # nichts importiert
    assert api.ordner_zeigen("uebersicht")["ok"]
    assert api.geoeffnet == [api.tmp / "excel" / "uebersicht"]
    assert not api.ordner_zeigen("irgendwo")["ok"]


def test_letzte_wex_und_erneut_uebergeben(api, cdh):
    p = api.abrufen()
    api.importieren(_keys(p, "agrar") + _keys(p, "beispiel"))
    api._warten()
    liste = api.letzte_wex()["dateien"]
    assert {d["datei"] for d in liste} == set(cdh["aufrufe"])
    bondorf = next(d for d in liste if "Bondorf" in d["datei"])
    assert bondorf["shop"] == "Agrar-Shop" and bondorf["orders"] == ["3939", "3940"]

    for boese in ("../einstellungen.yaml", "..\\x.wex", "C:x.wex", "gibtsnicht.wex"):
        assert not api.erneut_uebergeben(boese)["ok"], boese
    cdh["aufrufe"].clear()
    assert api.erneut_uebergeben(bondorf["datei"])["ok"]
    api._warten()
    st = api.import_status()
    assert st["art"] == "erneut" and st["einheiten"][0]["status"] == "fertig"
    assert cdh["aufrufe"] == [bondorf["datei"]]
    assert len(w.load_exported_log()) == 3                      # nichts doppelt vermerkt


def test_oeffentliche_methoden():
    namen = {n for n in dir(ia.ImportApi) if not n.startswith("_")}
    assert namen == {"abrufen", "importieren", "import_status", "import_abbrechen",
                     "excel_uebersicht", "ordner_zeigen", "letzte_wex", "erneut_uebergeben"}
