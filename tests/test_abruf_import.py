"""Welle 3: abrufen() liest nur, importieren() arbeitet Einheiten ab.

Die WooCommerce-API ist durch FakeWoo ersetzt (echte WooClient-Logik für
Filter und Duplikatschutz, nur _get/_put ohne Netz). CDH wird nicht gestartet.
"""
import logging
import threading
from datetime import datetime

import pytest

import woo_to_cdh as w
from conftest import FIXTURES, GOLDEN, PRICES


class FakeWoo(w.WooClient):
    """WooClient ohne Netz. Bestellungen je Shop-URL, Schreibzugriffe protokolliert."""

    orders_by_url: dict = {}
    puts: list = []
    gets: list = []

    def _get(self, path, params=None):
        FakeWoo.gets.append(path)
        if path == "/orders":
            if params.get("page") != 1:
                return []
            url = self.base.replace("/wp-json/wc/v3", "/")
            return FakeWoo.orders_by_url.get(url, [])
        parts = path.strip("/").split("/")          # products/10/variations/11
        pid = int(parts[1])
        vid = int(parts[3]) if len(parts) > 3 else 0
        ek, vk = PRICES[(pid, vid)]
        return {"dimensions": {"length": ek, "width": vk}}

    def _put(self, path, data):
        FakeWoo.puts.append((path, data))
        return {}


@pytest.fixture
def umgebung(tmp_path, monkeypatch, orders):
    FakeWoo.orders_by_url = {
        "https://shop.example/einzeln/": [orders["einzeln"]],
        "https://shop.example/agrar/": orders["trenn"],
        "https://shop.example/mitarbeiter/": orders["mitarbeitershop"],
    }
    FakeWoo.puts, FakeWoo.gets = [], []
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", FIXTURES / "lieferadressen_test.yaml")
    cdh = []
    monkeypatch.setattr(w, "start_cdh_wex_import",
                        lambda path, cfg: cdh.append(path.name) or True)
    shop = {"consumer_key": "ck_TEST", "consumer_secret": "cs_TEST",
            "datev_no": 10000, "order_type": "AB"}
    cfg = {
        "cdh_import_folder": str(tmp_path / "wex"),
        "excel_export_folder": str(tmp_path / "excel"),
        "status_after_export": "wc-completed",
        "order_no_with_name": True,
        "shops": [
            {**shop, "name": "Beispiel-Shop", "url": "https://shop.example/einzeln/"},
            {**shop, "name": "Agrar-Shop", "url": "https://shop.example/agrar/",
             "combine_by_delivery": True},
            {**shop, "name": "Mitarbeiter-Shop", "url": "https://shop.example/mitarbeiter/",
             "combine_by_delivery": True, "aggregate_all_positions": True,
             "sender_address": {"name1": "Beispiel Austria GmbH", "street": "Werkplatz 1",
                                "postcode": "4863", "city": "Seewalchen", "country": "AT"}},
        ],
    }
    return {"cfg": cfg, "tmp": tmp_path, "cdh": cdh}


def _abrufen(u, **kw):
    return w.abrufen(u["cfg"], client_factory=FakeWoo, **kw)


def _dateien(ordner):
    return sorted(p.name for p in ordner.rglob("*") if p.is_file()) if ordner.exists() else []


# --- abrufen ----------------------------------------------------------------

def test_abrufen_liefert_einheiten(umgebung):
    pruef = _abrufen(umgebung)
    assert [(e.shop, e.art, e.titel, e.order_nos) for e in pruef.einheiten()] == [
        ("Beispiel-Shop", "bestellung", "402", ["402"]),
        ("Agrar-Shop", "lieferort", "Bondorf", ["3939", "3940"]),
        ("Mitarbeiter-Shop", "lieferort", "Seewalchen", ["2771"]),
        ("Mitarbeiter-Shop", "lieferort", "Lenzing", ["2772", "2773"]),
    ]
    assert pruef.einheiten()[0].wex_data["order_no_wex"].startswith("402 ")
    assert pruef.fehler == 0


def test_abrufen_schreibt_nichts(umgebung):
    _abrufen(umgebung)
    assert FakeWoo.puts == []
    assert _dateien(umgebung["tmp"]) == []
    assert umgebung["cdh"] == []


def test_abrufen_beachtet_exported_log(umgebung):
    w.append_to_exported_log("Beispiel-Shop", 1402, "402", "alt.wex")
    pruef = _abrufen(umgebung)
    assert "402" not in [e.titel for e in pruef.einheiten()]


def test_warnung_ohne_feste_lieferadresse(umgebung, orders):
    for o in orders["trenn"]:
        o["shipping_lines"][0]["method_title"] = "Unbekannt"
    FakeWoo.orders_by_url["https://shop.example/mitarbeiter/"] = orders["trenn"]
    pruef = _abrufen(umgebung)
    e = next(e for e in pruef.einheiten() if e.shop == "Mitarbeiter-Shop")
    assert e.warnungen and "Firmenadresse" in e.warnungen[0]


def test_tages_gate_und_trotzdem(umgebung):
    anderer_tag = datetime.now().day % 28 + 1
    umgebung["cfg"]["shops"][0]["import_on_days"] = [anderer_tag]

    pruef = _abrufen(umgebung)
    sh = pruef.shops[0]
    assert sh.uebersprungen == "Tages-Gate" and sh.einheiten == []

    pruef = _abrufen(umgebung, trotzdem={"Beispiel-Shop"})
    sh = pruef.shops[0]
    assert sh.uebersprungen == "" and [e.titel for e in sh.einheiten] == ["402"]
    assert "Trotz Stichtag" in sh.warnungen[0]


def test_tages_gate_fragt_api_nicht_ab(umgebung):
    anderer_tag = datetime.now().day % 28 + 1
    umgebung["cfg"]["shops"] = umgebung["cfg"]["shops"][:1]
    umgebung["cfg"]["shops"][0]["import_on_days"] = [anderer_tag]
    _abrufen(umgebung)
    assert FakeWoo.gets == []


def test_zugang_fehlt_ist_sperre(umgebung):
    umgebung["cfg"]["shops"][1]["consumer_secret"] = ""
    pruef = _abrufen(umgebung)
    agrar = pruef.shops[1]
    assert agrar.sperren and "Zugangsdaten fehlen" in agrar.sperren[0]
    assert "Agrar-Shop" not in {e.shop for e in pruef.einheiten()}
    assert pruef.fehler == 1


# --- importieren ------------------------------------------------------------

def test_konsole_erzeugt_golden_wex(umgebung):
    """abrufen + importieren erzeugen dieselben Dateien wie die Golden-WEX."""
    imp = w.importieren(_abrufen(umgebung).einheiten())
    assert imp.ok == 6 and imp.fehler == 0
    wex = umgebung["tmp"] / "wex"
    datum = datetime.now().strftime("%Y-%m-%d")
    paare = {
        f"orders-{datum}-402.wex": "einzeln.wex",
        f"orders-{datum}-agrar-shop-Bondorf.wex": "sammel_trennzeilen.wex",
        f"orders-{datum}-mitarbeiter-shop-Lenzing.wex": "sammel_zusammengefasst.wex",
    }
    for ist, soll in paare.items():
        a = (wex / ist).read_bytes().replace(b"\r\n", b"\n")
        b = (GOLDEN / soll).read_bytes().replace(b"\r\n", b"\n")
        assert a == b, ist


def test_importieren_reihenfolge_und_vermerke(umgebung):
    einheiten = _abrufen(umgebung).einheiten()
    imp = w.importieren(einheiten)
    datum = datetime.now().strftime("%Y-%m-%d")

    # CDH nacheinander, eine Datei je Einheit, in Auswahl-Reihenfolge
    assert umgebung["cdh"] == [f"orders-{datum}-402.wex",
                               f"orders-{datum}-agrar-shop-Bondorf.wex",
                               f"orders-{datum}-mitarbeiter-shop-Seewalchen.wex",
                               f"orders-{datum}-mitarbeiter-shop-Lenzing.wex"]
    # exported.log kennt alle Bestellungen
    assert w.load_exported_log() == {"1402|402", "3939|3939", "3940|3940",
                                     "2771|2771", "2772|2772", "2773|2773"}
    # WooCommerce: je Bestellung Markierung, dann Status (ohne "wc-")
    assert FakeWoo.puts[:2] == [("/orders/1402", {"meta_data": [
        {"key": w.EXPORT_META_KEY, "value": FakeWoo.puts[0][1]["meta_data"][0]["value"]}]}),
        ("/orders/1402", {"status": "completed"})]
    assert sum(1 for _, d in FakeWoo.puts if d.get("status") == "completed") == 6
    # Excel je Einheit
    assert len(_dateien(umgebung["tmp"] / "excel")) == 4
    assert imp.je_shop == {"Beispiel-Shop": 1, "Agrar-Shop": 2, "Mitarbeiter-Shop": 3}


def test_fortschritt_und_abbruch_zwischen_einheiten(umgebung):
    einheiten = _abrufen(umgebung).einheiten()
    stop = threading.Event()
    verlauf = []

    def fortschritt(nr, gesamt, e, phase):
        verlauf.append((nr, gesamt, e.titel, phase))
        if phase == "start":
            stop.set()          # Abbruch mitten in Einheit 1 …

    imp = w.importieren(einheiten, fortschritt, stop)
    # … Einheit 1 läuft zu Ende, danach ist Schluss
    assert verlauf == [(1, 4, "402", "start"), (1, 4, "402", "fertig")]
    assert imp.abgebrochen and imp.ok == 1
    assert [e.titel for e in imp.nicht_bearbeitet] == ["Bondorf", "Seewalchen", "Lenzing"]
    assert len(umgebung["cdh"]) == 1
    assert w.load_exported_log() == {"1402|402"}


def test_abbruch_als_funktion(umgebung):
    imp = w.importieren(_abrufen(umgebung).einheiten(), abbruch_flag=lambda: True)
    assert imp.abgebrochen and imp.ok == 0 and umgebung["cdh"] == []


def test_schreibfehler_zaehlt_und_naechste_laeuft(umgebung, monkeypatch):
    echt = w.write_cdh_wex

    def kaputt(path, data):
        if "Bondorf" in path.name:
            raise OSError("Laufwerk weg")
        echt(path, data)

    monkeypatch.setattr(w, "write_cdh_wex", kaputt)
    verlauf = []
    imp = w.importieren(_abrufen(umgebung).einheiten(),
                        lambda nr, g, e, phase: verlauf.append((e.titel, phase)))
    assert ("Bondorf", "fehler") in verlauf
    assert imp.fehler == 2 and imp.ok == 4
    assert "3939|3939" not in w.load_exported_log()
    assert not any("/orders/3939" == p for p, _ in FakeWoo.puts)


def test_inzwischen_importiert_wird_uebersprungen(umgebung):
    einheiten = _abrufen(umgebung).einheiten()
    w.append_to_exported_log("Agrar-Shop", 3940, "3940", "anderer-rechner.wex")
    imp = w.importieren(einheiten)
    assert imp.fehler == 2
    assert not any("Bondorf" in n for n in umgebung["cdh"])


# --- Frage 8: Anzahl je Shop im Log -----------------------------------------

def test_bereits_exportiert_je_shop(umgebung, caplog):
    w.append_to_exported_log("Beispiel-Shop", 1, "1", "a.wex")
    w.append_to_exported_log("Beispiel-Shop", 2, "2", "b.wex")
    w.append_to_exported_log("Agrar-Shop", 3, "3", "c.wex")
    assert w.count_exported_by_shop() == {"Beispiel-Shop": 2, "Agrar-Shop": 1}

    with caplog.at_level(logging.INFO):
        _abrufen(umgebung)
    zeilen = [r.getMessage() for r in caplog.records if "exported.log" in r.getMessage()]
    assert "[Beispiel-Shop] 2 bereits exportierte Bestellung(en) dieses Shops in exported.log." in zeilen
    assert "[Agrar-Shop] 1 bereits exportierte Bestellung(en) dieses Shops in exported.log." in zeilen
    assert not any("Mitarbeiter-Shop" in z for z in zeilen)


def test_main_meldet_anzahl_je_shop(umgebung, monkeypatch, caplog):
    monkeypatch.setattr(w, "WooClient", FakeWoo)
    monkeypatch.setattr(w, "config_vorhanden", lambda: True)
    monkeypatch.setattr(w, "load_config", lambda: (umgebung["cfg"], "test"))
    monkeypatch.setattr(w, "LOCK_PATH", umgebung["tmp"] / "running.lock")
    monkeypatch.setattr(w, "setup_logging", lambda level="INFO": None)
    with caplog.at_level(logging.INFO):
        assert w.main() == 0
    msgs = [r.getMessage() for r in caplog.records]
    assert "[Beispiel-Shop] 1 Bestellung(en) exportiert." in msgs
    assert "[Agrar-Shop] 2 Bestellung(en) exportiert." in msgs
    assert "[Mitarbeiter-Shop] 3 Bestellung(en) exportiert." in msgs
    assert "=== Lauf beendet: 6 exportiert, 0 Fehler ===" in msgs


# --- Frage 1: E-Mail im Sammelauftrag ---------------------------------------

def test_sammel_email_leer(orders, client):
    from conftest import build
    data = [build(o, client) for o in orders["trenn"]]
    c = w.build_combined_wex_data(data, {"datev_no": 10000}, "Bondorf")
    assert c["email"] == ""
    c = w.build_combined_wex_data(data, {"datev_no": 10000, "aggregate_all_positions": True}, "Bondorf")
    assert c["email"] == ""


def test_sammel_email_aus_sender_address(orders, client):
    from conftest import build
    sender = {"name1": "Agrar GmbH", "email": "auftrag@agrar.test"}
    data = [build(o, client, sender_address=sender) for o in orders["trenn"]]
    for agg in (False, True):
        c = w.build_combined_wex_data(
            data, {"datev_no": 10000, "sender_address": sender,
                   "aggregate_all_positions": agg}, "Bondorf")
        assert c["email"] == "auftrag@agrar.test"


def test_einzelauftrag_email_unveraendert(orders, client):
    from conftest import build
    assert build(orders["einzeln"], client)["email"] == "einkauf@beispiel.test"
