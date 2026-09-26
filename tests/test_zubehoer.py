"""Welle 9: Pflicht-Zubehör beim Import aus den Produktregeln ergänzen.

Plugin „CDH Required Accessories" speichert am Produkt (oder an der Variante,
die dann Vorrang hat) `_cdh_required_accessories` = [{accessory_id,
qty_per_unit}]. Mit pflicht_zubehoer im Shop legt der Import das Zubehör
selbst an; der Kunde sieht es im Shop nicht mehr. Testdaten erfunden.
"""
import copy

import pytest
import requests

import woo_to_cdh as w
from conftest import GOLDEN, PRICES, build

REGEL = {"key": "_cdh_required_accessories",
         "value": [{"accessory_id": 20, "qty_per_unit": 1}]}


class Client:
    """Produkte mit Preisen (Länge/Breite) und Zubehör-Regeln, ohne Netz."""

    def __init__(self, regeln=None, var_regeln=None, fehler=(), ohne_sku=()):
        self.regeln = regeln if regeln is not None else {10: [REGEL]}
        self.var_regeln = var_regeln or {}
        self.fehler, self.ohne_sku = set(fehler), set(ohne_sku)
        self.abrufe = []

    def _antwort(self, pid, vid, meta):
        self.abrufe.append((pid, vid))
        if (pid, vid) in self.fehler:
            raise requests.HTTPError("500 Server Error")
        ek, vk = PRICES.get((pid, vid), ("1.00", "2.00"))
        return {"id": vid or pid, "name": {20: "Stick Logo"}.get(pid, f"Artikel {pid}"),
                "sku": "" if pid in self.ohne_sku else {20: "004/STICK-LOGO"}.get(pid, f"A-{pid}"),
                "dimensions": {"length": ek, "width": vk}, "meta_data": meta}

    def get_product(self, pid):
        return self._antwort(pid, 0, self.regeln.get(pid, []))

    def get_variation(self, pid, vid):
        return self._antwort(pid, vid, self.var_regeln.get((pid, vid), []))


def _ohne_stick(order):
    o = copy.deepcopy(order)
    o["line_items"] = [it for it in o["line_items"] if it["product_id"] != 20]
    return o


def test_ergaenzt_stick_je_stueck(orders):
    o = _ohne_stick(orders["einzeln"])                   # 2× Poloshirt, kein Stick
    neu = w.zubehoer_ergaenzen(o, Client(), {})
    assert [(z["sku"], z["quantity"]) for z in neu] == [("004/STICK-LOGO", 2)]
    assert o["line_items"][-1]["_zubehoer"] and o["line_items"][-1]["total"] == "0.00"


def test_gleiche_wex_wie_bisher_mit_warenkorb_zeilen(orders, tmp_path):
    """Neu (Zubehör vom Import) ergibt dieselbe WEX wie bisher (Zubehör als
    0-€-Zeilen aus dem Warenkorb) — CDH merkt keinen Unterschied."""
    o = _ohne_stick(orders["einzeln"])
    client = Client()
    w.zubehoer_ergaenzen(o, client, {})
    d = build(o, client)                                # wie tests/test_golden.py
    d["order_no_wex"] = f"{d['order_no']} {d['person_name']}"
    out = tmp_path / "neu.wex"
    w.write_cdh_wex(out, d)
    ist = out.read_bytes().replace(b"\r\n", b"\n")
    assert ist == (GOLDEN / "einzeln.wex").read_bytes().replace(b"\r\n", b"\n")


def test_nichts_doppelt_bei_alten_bestellungen(orders):
    o = copy.deepcopy(orders["einzeln"])                # Sticks stehen schon drin
    assert w.zubehoer_ergaenzen(o, Client(), {}) == []
    assert len(o["line_items"]) == 3


def test_nur_der_rest_wird_ergaenzt(orders):
    o = copy.deepcopy(orders["einzeln"])
    o["line_items"][0]["quantity"] = 5                  # 5 Polos, 2 Sticks vorhanden
    neu = w.zubehoer_ergaenzen(o, Client(), {})
    assert [(z["sku"], z["quantity"]) for z in neu] == [("004/STICK-LOGO", 3)]


def test_zweimal_aufrufen_ergaenzt_einmal(orders):
    o = _ohne_stick(orders["einzeln"])
    c = Client()
    w.zubehoer_ergaenzen(o, c, {})
    assert w.zubehoer_ergaenzen(o, c, {}) == []
    assert sum(1 for it in o["line_items"] if it.get("_zubehoer")) == 1


def test_variante_hat_vorrang(orders):
    o = _ohne_stick(orders["einzeln"])
    var = {(10, 11): [{"key": "_cdh_required_accessories",
                       "value": [{"accessory_id": 20, "qty_per_unit": 2}]}]}
    neu = w.zubehoer_ergaenzen(o, Client(var_regeln=var), {})
    assert neu[0]["quantity"] == 4


def test_regeln_als_objekt_und_bruchteile(orders):
    """PHP-Arrays kommen je nach Speicherung als Liste oder Objekt."""
    o = _ohne_stick(orders["einzeln"])
    regeln = {10: [{"key": "_cdh_required_accessories",
                    "value": {"0": {"accessory_id": "20", "qty_per_unit": "0.5"}}}]}
    neu = w.zubehoer_ergaenzen(o, Client(regeln=regeln), {})
    assert neu[0]["quantity"] == 1                      # 2 × 0,5


def test_ohne_regel_nichts(orders):
    o = _ohne_stick(orders["einzeln"])
    assert w.zubehoer_ergaenzen(o, Client(regeln={}), {}) == []


def test_regel_nicht_lesbar_bestellung_bleibt_offen(orders):
    o = _ohne_stick(orders["einzeln"])
    with pytest.raises(w.OrderBuildError, match="Zubehör-Regeln nicht abrufbar"):
        w.zubehoer_ergaenzen(o, Client(fehler={(10, 11)}), {})


def test_zubehoer_ohne_artikelnummer(orders):
    o = _ohne_stick(orders["einzeln"])
    with pytest.raises(w.OrderBuildError, match="keine Artikelnummer"):
        w.zubehoer_ergaenzen(o, Client(ohne_sku={20}), {})


def test_cache_spart_abrufe(orders):
    o1, o2 = _ohne_stick(orders["einzeln"]), _ohne_stick(orders["einzeln"])
    c, cache = Client(), {}
    w.zubehoer_ergaenzen(o1, c, cache)
    n = len(c.abrufe)
    w.zubehoer_ergaenzen(o2, c, cache)
    assert len(c.abrufe) == n


def test_summenblatt_zaehlt_zubehoer(orders):
    o = _ohne_stick(orders["einzeln"])
    w.zubehoer_ergaenzen(o, Client(), {})
    _, zeilen = w.summen_zeilen([o], by="gesamt")
    assert ["004/STICK-LOGO", "Stick Logo", "", 2] in zeilen


def test_abrufen_nur_mit_schalter(orders, tmp_path, monkeypatch):
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", tmp_path / "fehlt.yaml")
    bestellung = _ohne_stick(orders["einzeln"])

    class Woo(w.WooClient):
        def _get(self, path, params=None):
            if path == "/orders":
                return [copy.deepcopy(bestellung)] if params.get("page") == 1 else []
            teile = path.strip("/").split("/")
            pid = int(teile[1])
            vid = int(teile[3]) if len(teile) > 3 else 0
            return Client()._antwort(pid, vid, Client().regeln.get(pid, []) if not vid else [])

    shop = {"name": "Beispiel-Shop", "url": "https://shop.example/x/", "consumer_key": "ck_T",
            "consumer_secret": "cs_T", "datev_no": 19541, "order_type": "AB"}
    ohne = w.abrufen({"shops": [shop]}, client_factory=Woo)
    mit = w.abrufen({"shops": [{**shop, "pflicht_zubehoer": True}]}, client_factory=Woo)
    sku = lambda erg: [p["article_no"] for p in erg.shops[0].einheiten[0].wex_data["positions"]]
    assert sku(ohne) == ["042/POLO-M"]
    assert sku(mit) == ["042/POLO-M", "004/STICK-LOGO"]
    assert mit.shops[0].einheiten[0].wex_data["positions"][1]["zubehoer"] is True


def test_schalter_in_den_einstellungen(tmp_path):
    import yaml
    import einstellungen_api as ea
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump({"shops": [
        {"id": "caf", "name": "CAF-Shop", "url": "https://shop.example/caf/", "datev_no": 19541}]}),
        encoding="utf-8")
    api = ea.EinstellungenApi(tmp_path, benutzer="t")
    st = api.laden()
    assert st["shops"][0]["zubehoer"] is False
    st["shops"][0]["zubehoer"] = True
    erg = api.sichern({"shops": st["shops"], "global": st["global"], "token": st["token"]})
    assert erg["ok"] and erg["log"][0]["what"] == "CAF: Pflicht-Zubehör ergänzen an"
    cfg = yaml.safe_load((tmp_path / "einstellungen.yaml").read_text(encoding="utf-8"))
    assert cfg["shops"][0]["pflicht_zubehoer"] is True
    erg["shops"][0]["zubehoer"] = False
    api.sichern({"shops": erg["shops"], "global": erg["global"], "token": erg["token"]})
    cfg = yaml.safe_load((tmp_path / "einstellungen.yaml").read_text(encoding="utf-8"))
    assert "pflicht_zubehoer" not in cfg["shops"][0]


def test_diagnose_zeigt_regeln():
    import diagnose
    produkte = [
        {"id": 10, "sku": "042/POLO", "name": "Poloshirt", "type": "variable", "status": "publish",
         "meta_data": [REGEL]},
        {"id": 20, "sku": "004/STICK-LOGO", "name": "Stick Logo", "type": "simple",
         "status": "publish", "catalog_visibility": "visible", "meta_data": []},
        {"id": 30, "sku": "042/SHIRT", "name": "Shirt", "type": "simple", "status": "publish",
         "meta_data": [{"key": "_cdh_required_accessories",
                        "value": [{"accessory_id": 99, "qty_per_unit": 1}]}]},
    ]

    def abruf(path, params=None):
        return (200, produkte) if params.get("page") == 1 else (200, [])
    erg = diagnose.zubehoer_uebersicht(abruf)
    assert erg["produkte"] == 3 and len(erg["regeln"]) == 2
    polo = erg["regeln"][0]
    assert polo["sku"] == "042/POLO" and polo["zubehoer"] == [("004/STICK-LOGO", 1.0, "")]
    assert erg["regeln"][1]["zubehoer"] == [("?", 1.0, "Zubehör 99 nicht im Shop gefunden")]
    assert erg["sichtbar"] == ["004/STICK-LOGO"]            # im Katalog noch sichtbar
