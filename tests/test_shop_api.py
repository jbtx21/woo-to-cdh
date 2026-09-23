"""Welle 7: Shop anlegen, Zugang erneuern, Altbestellungen (shop_api).

WooCommerce ist FakeWoo (conftest). Schlüssel werden zur Laufzeit gebaut,
damit der Schlüssel-Scanner im Repo nicht anschlägt — und in keinem
Ergebnis, Log oder Verlauf auftauchen dürfen.
"""
import json
import logging

import pytest
import requests
import yaml

import migrate_config as m
import shop_api as sa
import woo_to_cdh as w
from conftest import FakeWoo

PASSWORT = "richtig-geheim"
KEY = "ck_" + "Q" * 24
SECRET = "cs_" + "W" * 24
FALSCH = "ck_" + "Z" * 24
NEU_URL = "https://shop.example/musterbau/"

EINST = {
    "cdh_import_folder": "wex",
    "status_after_export": "completed",
    "shops": [
        {"id": "caf", "name": "CAF-Shop", "enabled": True,
         "url": "https://shop.example/caf-shop/", "datev_no": 19541, "order_type": "AB"},
        {"name": "Ensinger-Shop", "enabled": False,
         "url": "https://shop.example/ensinger-shop/", "datev_no": 14020, "order_type": "AB"},
    ],
}


class Woo(FakeWoo):
    """FakeWoo mit Schlüsselprüfung: FALSCH ergibt 401 wie beim echten Shop."""

    def _get(self, path, params=None):
        if self.consumer_key == FALSCH:
            r = requests.Response()
            r.status_code = 401
            raise requests.HTTPError("401 Client Error", response=r)
        return super()._get(path, params)

    def set_status(self, order_id, status):
        FakeWoo.puts.append((f"/orders/{order_id}", {"status": status}))


class Uhr:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def _bestellung(oid, nr):
    return {"id": oid, "number": str(nr), "status": "processing",
            "date_created": "2026-09-01T10:00:00", "line_items": [], "meta_data": []}


@pytest.fixture
def ordner(tmp_path, orders, monkeypatch):
    (tmp_path / "einstellungen.yaml").write_text(
        yaml.safe_dump(EINST, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zugang = {"admin": {"password": m.hash_admin_password(PASSWORT, iterations=1000),
                        "users": []},
              "shops": {"caf": {"consumer_key": "ck_ALT_CAF", "consumer_secret": "cs_ALT_CAF"},
                        "Ensinger-Shop": {"consumer_key": "ck_ALT_ENS",
                                          "consumer_secret": "cs_ALT_ENS"}}}
    (tmp_path / "zugang.yaml").write_text(yaml.safe_dump(zugang), encoding="utf-8")
    FakeWoo.orders_by_url = {
        NEU_URL: [orders["einzeln"], _bestellung(501, 1001), _bestellung(502, 1002)],
        "https://shop.example/ensinger-shop/": [_bestellung(601, 2001), _bestellung(602, 2002)],
        "https://shop.example/caf-shop/": [_bestellung(701, 3001)],
    }
    FakeWoo.puts, FakeWoo.gets, FakeWoo.zones_by_url = [], [], {}
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", tmp_path / "lieferadressen.yaml")
    return tmp_path


@pytest.fixture
def api(ordner):
    a = sa.ShopApi(ordner, benutzer="m.mueller", uhr=Uhr(), client_factory=Woo)
    assert a.admin_anmelden(PASSWORT)["ok"]
    return a


def _daten(**kw):
    return {"name": "Musterbau", "url": NEU_URL, "debitor": "12345",
            "key": KEY, "secret": SECRET, **kw}


def _zugang(ordner):
    return yaml.safe_load((ordner / "zugang.yaml").read_text(encoding="utf-8"))


def _einst(ordner):
    return yaml.safe_load((ordner / "einstellungen.yaml").read_text(encoding="utf-8"))


def _ohne_schluessel(*objekte):
    for o in objekte:
        text = o if isinstance(o, str) else json.dumps(o, ensure_ascii=False)
        assert KEY not in text and SECRET not in text


# --- Admin -------------------------------------------------------------------

def test_alles_nur_im_admin_modus(ordner):
    a = sa.ShopApi(ordner, benutzer="m.mueller", uhr=Uhr(), client_factory=Woo)
    for erg in (a.shop_pruefen(_daten()), a.shop_anlegen({**_daten(), "token": a.laden()["token"]}),
                a.zugang_erneuern("caf", KEY, SECRET), a.altbestellungen("ensinger"),
                a.altbestellungen_abschliessen("ensinger", [601], True)):
        assert erg["ok"] is False and "Admin" in erg["fehler"]
    assert FakeWoo.gets == [] and FakeWoo.puts == []


# --- Prüfen ------------------------------------------------------------------

@pytest.mark.parametrize("aenderung, text", [
    ({"name": ""}, "Name"),
    ({"url": "https://shop.example/"}, "Shop-Adresse"),
    ({"url": "http://shop.example/neu/"}, "Shop-Adresse"),
    ({"url": "https://shop.example/neu"}, "Shop-Adresse"),
    ({"debitor": "12a"}, "Debitornummer"),
    ({"debitor": "19541"}, "gehört schon zu CAF-Shop"),
    ({"name": "caf-shop"}, "gibt es schon"),
    ({"url": "https://shop.example/CAF-Shop"}, "Shop-Adresse"),
    ({"url": "https://shop.example/caf-shop/"}, "gehört schon zu CAF-Shop"),
    ({"key": "QQQ"}, "ck_"),
    ({"secret": "ck_" + "W" * 24}, "cs_"),
])
def test_pruefen_grunddaten(api, aenderung, text):
    erg = api.shop_pruefen(_daten(**aenderung))
    assert erg["ok"] is False and text in erg["fehler"]
    assert FakeWoo.gets == []


def test_pruefen_ok_liest_nur(api, ordner):
    vorher = {p.name: p.read_bytes() for p in ordner.iterdir() if p.is_file()}
    erg = api.shop_pruefen(_daten())
    assert erg["ok"] and erg["bestanden"] and erg["id"] == "musterbau"
    assert [p["titel"] for p in erg["punkte"]] == [
        "Verbindung und Zugang", "Bestellungen lesbar", "Preise gepflegt",
        "Varianten erkannt", "Versandarten"]
    assert erg["punkte"][0]["stufe"] == "ok"
    assert erg["offen"] == {"anzahl": 3, "nummern": ["402", "1001", "1002"]}
    assert FakeWoo.puts == []
    assert {p.name: p.read_bytes() for p in ordner.iterdir() if p.is_file()} == vorher
    _ohne_schluessel(erg)


def test_pruefen_falscher_schluessel(api):
    erg = api.shop_pruefen(_daten(key=FALSCH))
    assert erg["ok"] and erg["bestanden"] is False
    assert erg["punkte"][0]["stufe"] == "fehler" and "Schlüssel" in erg["punkte"][0]["text"]
    assert len(erg["punkte"]) == 1


# --- Anlegen -----------------------------------------------------------------

def test_anlegen_nur_nach_pruefung(api, ordner):
    token = api.laden()["token"]
    erg = api.shop_anlegen({**_daten(), "token": token})
    assert erg["ok"] is False and "Erst prüfen" in erg["fehler"]
    assert api.shop_pruefen(_daten())["bestanden"]
    anderes = "cs_" + "V" * 24
    erg = api.shop_anlegen({**_daten(secret=anderes), "token": token})
    assert erg["ok"] is False and "Erst prüfen" in erg["fehler"]
    assert len(_einst(ordner)["shops"]) == 2


def test_anlegen_nach_fehlgeschlagener_pruefung(api, ordner):
    api.shop_pruefen(_daten(key=FALSCH))
    erg = api.shop_anlegen({**_daten(key=FALSCH), "token": api.laden()["token"]})
    assert erg["ok"] is False and "Erst prüfen" in erg["fehler"]


def test_anlegen_ausgeschaltet(api, ordner, caplog):
    caplog.set_level(logging.DEBUG)
    api.shop_pruefen(_daten())
    erg = api.shop_anlegen({**_daten(), "token": api.laden()["token"], "old": "close"})
    assert erg["ok"] and erg["neu_id"] == "musterbau"
    neu = _einst(ordner)["shops"][-1]
    assert neu == {"id": "musterbau", "name": "Musterbau", "enabled": False,
                   "url": NEU_URL, "datev_no": 12345, "order_type": "AB"}
    z = _zugang(ordner)
    assert z["shops"]["musterbau"] == {"consumer_key": KEY, "consumer_secret": SECRET}
    assert z["admin"]["password"]["hash"]                  # Admin bleibt erhalten
    assert z["shops"]["caf"]["consumer_key"] == "ck_ALT_CAF"
    ui = next(s for s in erg["shops"] if s["id"] == "musterbau")
    assert ui["active"] is False and ui["zugang"] is True and ui["debitor"] == "12345"
    assert erg["log"][0]["what"] == "Musterbau: Shop angelegt (Debitor 12345), ausgeschaltet"
    assert list((ordner / "Backup").glob("einstellungen_*.yaml"))
    assert not list((ordner / "Backup").glob("zugang*"))
    assert FakeWoo.puts == []                              # Altbestellungen gesondert
    _ohne_schluessel(erg, caplog.text, (ordner / "aenderungsverlauf.log").read_text("utf-8"),
                     (ordner / "einstellungen.yaml").read_text("utf-8"))
    # Nur einmal: zweites Anlegen ohne neue Prüfung geht nicht
    erg = api.shop_anlegen({**_daten(name="Musterbau 2", debitor="12346"),
                            "token": api.laden()["token"]})
    assert erg["ok"] is False


def test_anlegen_konflikt_mit_anderem_rechner(api, ordner):
    api.shop_pruefen(_daten())
    token = api.laden()["token"]
    p = ordner / "einstellungen.yaml"
    p.write_text(p.read_text("utf-8") + "\n# woanders gesichert\n", encoding="utf-8")
    erg = api.shop_anlegen({**_daten(), "token": token})
    assert erg["ok"] is False and "neu laden" in erg["fehler"]
    assert "musterbau" not in _zugang(ordner)["shops"]


def test_anlegen_id_eindeutig(api, ordner):
    FakeWoo.orders_by_url["https://shop.example/caf/"] = []
    d = _daten(name="CAF", url="https://shop.example/caf/")
    erg = api.shop_pruefen(d)
    assert erg["id"] == "caf-2"
    erg = api.shop_anlegen({**d, "token": api.laden()["token"]})
    assert erg["neu_id"] == "caf-2" and "caf-2" in _zugang(ordner)["shops"]


def test_anlegen_ohne_admin_nach_ablauf(api):
    api.shop_pruefen(_daten())
    api._uhr.t += 11 * 60
    erg = api.shop_anlegen({**_daten(), "token": api.laden()["token"]})
    assert erg["ok"] is False and "Admin" in erg["fehler"]


# --- Zugang erneuern -------------------------------------------------------

def test_zugang_erneuern_falsch_bleibt_alt(api, ordner):
    erg = api.zugang_erneuern("caf", FALSCH, SECRET)
    assert erg["ok"] is False and "Schlüssel" in erg["fehler"]
    assert _zugang(ordner)["shops"]["caf"]["consumer_key"] == "ck_ALT_CAF"
    assert not (ordner / "aenderungsverlauf.log").exists()


def test_zugang_erneuern_prefix(api):
    erg = api.zugang_erneuern("caf", "QQ", SECRET)
    assert erg["ok"] is False and "ck_" in erg["fehler"] and FakeWoo.gets == []


def test_zugang_erneuern_ok(api, ordner, caplog):
    caplog.set_level(logging.DEBUG)
    erg = api.zugang_erneuern("caf", KEY, SECRET)
    assert erg["ok"] and "widerrufen" in erg["hinweis"]
    assert _zugang(ordner)["shops"]["caf"] == {"consumer_key": KEY, "consumer_secret": SECRET}
    assert erg["log"][0]["what"] == "CAF-Shop: Zugang erneuert"
    assert FakeWoo.puts == []
    _ohne_schluessel(erg, caplog.text, (ordner / "aenderungsverlauf.log").read_text("utf-8"))
    cfg, _ = w.load_config(ordner)
    assert cfg["shops"][0]["consumer_key"] == KEY


def test_zugang_erneuern_shop_ohne_id(api, ordner, caplog):
    """Ensinger hat noch keine feste id: Zugang bleibt unter dem Namen,
    sonst fände der Import ihn nicht mehr."""
    caplog.set_level(logging.DEBUG)
    erg = api.zugang_erneuern("ensinger", KEY, SECRET)
    assert erg["ok"]
    z = _zugang(ordner)["shops"]
    assert z["Ensinger-Shop"] == {"consumer_key": KEY, "consumer_secret": SECRET}
    assert "ensinger" not in z
    assert erg["log"][0]["what"] == "Ensinger-Shop: Zugang erneuert"
    assert FakeWoo.puts == []
    _ohne_schluessel(erg, caplog.text, (ordner / "aenderungsverlauf.log").read_text("utf-8"))
    # Der Import findet den neuen Zugang
    cfg, _ = w.load_config(ordner)
    assert next(s for s in cfg["shops"] if s["name"] == "Ensinger-Shop")["consumer_key"] == KEY


def test_zugang_erneuern_unbekannter_shop(api):
    assert api.zugang_erneuern("gibtsnicht", KEY, SECRET)["ok"] is False


# --- Altbestellungen -----------------------------------------------------------

def test_altbestellungen_nur_ausgeschaltet(api):
    erg = api.altbestellungen("caf")
    assert erg["ok"] is False and "eingeschaltet" in erg["fehler"]
    erg = api.altbestellungen_abschliessen("caf", [701], True)
    assert erg["ok"] is False and FakeWoo.puts == []


def test_altbestellungen_liste(api):
    erg = api.altbestellungen("ensinger")
    assert erg == {"ok": True, "anzahl": 2, "bestellungen": [
        {"id": 601, "nummer": "2001", "datum": "2026-09-01"},
        {"id": 602, "nummer": "2002", "datum": "2026-09-01"}]}
    assert FakeWoo.puts == []


@pytest.mark.parametrize("bestaetigt", [False, None, "ja", 1])
def test_abschliessen_braucht_bestaetigung(api, bestaetigt):
    erg = api.altbestellungen_abschliessen("ensinger", [601, 602], bestaetigt)
    assert erg["ok"] is False and "Bestätigung" in erg["fehler"]
    assert FakeWoo.puts == []


def test_abschliessen_nur_angezeigte(api, ordner):
    # Seit der Anzeige kam 603 dazu, 602 ist schon woanders erledigt
    FakeWoo.orders_by_url["https://shop.example/ensinger-shop/"] = [
        _bestellung(601, 2001), _bestellung(603, 2003)]
    erg = api.altbestellungen_abschliessen("ensinger", [601, 602], True)
    assert erg["ok"] and erg["abgeschlossen"] == ["2001"]
    assert erg["nicht_mehr_offen"] == 1 and erg["neu_seitdem"] == ["2003"]
    assert FakeWoo.puts == [("/orders/601", {"status": "completed"})]
    assert api.laden()["log"][0]["what"] == \
        "Ensinger-Shop: 1 Altbestellung abgeschlossen (Nr. 2001)"


def test_assistent_komplett(api, ordner):
    """Prüfen → Anlegen → Altbestellungen abschließen, wie die Oberfläche es macht."""
    pr = api.shop_pruefen(_daten())
    st = api.shop_anlegen({**_daten(), "token": api.laden()["token"]})
    liste = api.altbestellungen(st["neu_id"])
    assert [b["nummer"] for b in liste["bestellungen"]] == pr["offen"]["nummern"]
    erg = api.altbestellungen_abschliessen(st["neu_id"],
                                           [b["id"] for b in liste["bestellungen"]], True)
    assert erg["abgeschlossen"] == ["402", "1001", "1002"] and erg["fehler_nummern"] == []
    assert [p for p, _ in FakeWoo.puts] == ["/orders/1402", "/orders/501", "/orders/502"]
