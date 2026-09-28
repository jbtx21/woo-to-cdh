"""Welle 11: wählbare Veredelungen aus PPOM (Shop Weeber).

PPOM legt Checkbox-Optionen an der Bestellposition unter dem Feldnamen ab
(Anzeigename = Feldtitel, Werte mit Komma getrennt, ggf. mit Preis), Text-
felder mit dem eingegebenen Text. Je Shop ordnet ppom_veredelung Feld und
Option einem verborgenen Veredelungsartikel zu. Testdaten erfunden.
"""
import copy

import pytest
import requests

import woo_to_cdh as w

JACKE = {"id": 1, "product_id": 152, "variation_id": 1521, "sku": "152/140MW-L",
         "name": "Soft Shell Jacket - L", "quantity": 2, "total": "150.00",
         "meta_data": [
             {"key": "pa_groesse", "display_key": "Größe", "value": "L"},
             {"key": "zusatzoptionen", "display_key": "Zusatzoptionen",
              "value": "Stick Audi (+5,34 €), Stick Logo Weeber Rücken (+17,08 €)"},
             {"key": "stick_name", "display_key": "Stick Name", "value": "Max Muster"},
             {"key": "_ppom_fields", "value": {"fields": {"stick_name": "Max Muster"}}}]}

REGELN = [
    {"feld": "Zusatzoptionen", "option": "Stick Audi", "artikel": "004/AUDI"},
    {"feld": "Zusatzoptionen", "option": "Stick Logo Weeber Rücken", "artikel": "004/WEEBER-R"},
    {"feld": "Stick Name", "option": "", "artikel": "004/NAME"},
]

ARTIKEL = {
    "004/AUDI": {"id": 401, "name": "Stick Audi", "sku": "004/AUDI",
                 "dimensions": {"length": "2.00", "width": "5.34"}, "meta_data": []},
    "004/WEEBER-R": {"id": 402, "name": "Stick Logo Weeber Rücken", "sku": "004/WEEBER-R",
                     "dimensions": {"length": "6.50", "width": "17.08"}, "meta_data": []},
    "004/NAME": {"id": 403, "name": "Stick Name", "sku": "004/NAME",
                 "dimensions": {"length": "1.80", "width": "4.96"},
                 "meta_data": [{"key": "_cdh_staffelpreise", "value": [{"ab": 10, "vk": "4.50", "ek": ""}]}]},
}


class Client:
    def __init__(self, artikel=ARTIKEL, fehler=False):
        self.artikel, self.fehler, self.abrufe = artikel, fehler, []

    def _get(self, path, params=None):
        self.abrufe.append((path, dict(params or {})))
        if self.fehler:
            raise requests.ConnectionError("weg")
        a = self.artikel.get((params or {}).get("sku"))
        return [copy.deepcopy(a)] if a else []

    def get_product(self, pid):
        a = next((a for a in self.artikel.values() if a["id"] == pid), None)
        if a is None:
            if pid in (152,):
                return {"id": pid, "dimensions": {"length": "30.00", "width": "56.02"}, "meta_data": []}
            raise KeyError(pid)
        return copy.deepcopy(a)

    def get_variation(self, pid, vid):
        return {"id": vid, "dimensions": {"length": "30.00", "width": "56.02"}, "meta_data": []}


def _bestellung(*items):
    return {"id": 9, "number": "9001", "status": "processing", "date_created": "2026-09-28T10:00:00",
            "billing": {"first_name": "Erika", "last_name": "Muster", "email": "e@example.org"},
            "shipping": {}, "shipping_lines": [], "meta_data": [],
            "line_items": [copy.deepcopy(i) for i in items]}


def test_option_als_ganzer_eintrag():
    wert = "Stick Audi (+5,34 €), Stick Logo Weeber Rücken (+17,08 €)"
    assert w._option_gewaehlt(wert, "Stick Audi")
    assert w._option_gewaehlt(wert, "stick logo weeber rücken")
    assert not w._option_gewaehlt(wert, "Stick")
    assert not w._option_gewaehlt(wert, "Stick Logo Weeber")
    assert w._option_gewaehlt("Stick Audi", "Stick Audi")
    assert w._option_gewaehlt("Stick Audi [+5,34 €]", "Stick Audi")


def test_regeln_aus_der_konfiguration():
    cfg = {"ppom_veredelung": REGELN + [{"feld": "", "artikel": "x"}, "unsinn"]}
    assert w.ppom_regeln(cfg) == REGELN
    assert w.ppom_regeln({}) == []


def test_positionen_hinter_der_jacke_und_name_an_der_jacke():
    o = _bestellung(JACKE)
    neu = w.ppom_veredelung_ergaenzen(o, Client(), {}, REGELN)
    assert [(z["sku"], z["quantity"]) for z in neu] == [("004/AUDI", 2), ("004/WEEBER-R", 2), ("004/NAME", 2)]
    assert [it["sku"] for it in o["line_items"]] == ["152/140MW-L", "004/AUDI", "004/WEEBER-R", "004/NAME"]
    assert o["line_items"][0]["_zusatztext"] == "Stick Name: Max Muster"
    # Zweimal aufrufen ergänzt nichts doppelt
    assert w.ppom_veredelung_ergaenzen(o, Client(), {}, REGELN) == []


def test_wex_positionen_preise_und_text():
    o = _bestellung(JACKE)
    cache = {}
    w.ppom_veredelung_ergaenzen(o, Client(), cache, REGELN)
    d = w.build_wex_data(o, {"datev_no": 30000, "order_type": "AB"}, Client(), cache)
    pos = [(p["article_no"], p["quantity"], p["selling_price"], p["buying_price"], p["variant_text"],
            p.get("veredelung", False)) for p in d["positions"]]
    assert pos == [("152/140MW-L", 2, 56.02, 30.0, "L, Stick Name: Max Muster", False),
                   ("004/AUDI", 2, 5.34, 2.0, "", True),
                   ("004/WEEBER-R", 2, 17.08, 6.5, "", True),
                   ("004/NAME", 2, 4.96, 1.8, "", True)]
    assert cache["staffeln"]["004/NAME"][0]["ab"] == 10


def test_nichts_gewaehlt_nichts_ergaenzt():
    jacke = copy.deepcopy(JACKE)
    jacke["meta_data"] = [m for m in jacke["meta_data"] if m["key"] == "pa_groesse"]
    o = _bestellung(jacke)
    c = Client()
    assert w.ppom_veredelung_ergaenzen(o, c, {}, REGELN) == []
    assert c.abrufe == [] and "_zusatztext" not in o["line_items"][0]


def test_leeres_textfeld_zaehlt_nicht():
    jacke = copy.deepcopy(JACKE)
    for m in jacke["meta_data"]:
        if m["key"] == "stick_name":
            m["value"] = "  "
    neu = w.ppom_veredelung_ergaenzen(_bestellung(jacke), Client(), {}, REGELN)
    assert "004/NAME" not in [z["sku"] for z in neu]


def test_feldname_statt_titel_geht_auch():
    regeln = [{"feld": "zusatzoptionen", "option": "Stick Audi", "artikel": "004/AUDI"}]
    neu = w.ppom_veredelung_ergaenzen(_bestellung(JACKE), Client(), {}, regeln)
    assert [z["sku"] for z in neu] == ["004/AUDI"]


@pytest.mark.parametrize("client, text", [
    (Client(artikel={}), "Veredelungsartikel 004/AUDI gibt es im Shop nicht"),
    (Client(fehler=True), "Veredelungsartikel 004/AUDI nicht abrufbar"),
])
def test_fehlender_artikel_bestellung_bleibt_offen(client, text):
    with pytest.raises(w.OrderBuildError, match=text):
        w.ppom_veredelung_ergaenzen(_bestellung(JACKE), client, {}, REGELN)


def test_zusammen_mit_zubehoer_am_zubehoer():
    """Stick Logo Rücken → Transfer als Pflicht-Zubehör: kommt mit."""
    artikel = copy.deepcopy(ARTIKEL)
    artikel["004/WEEBER-R"]["meta_data"] = [{"key": "_cdh_required_accessories",
                                             "value": [{"accessory_id": 404, "qty_per_unit": 1}]}]
    artikel["005/TRANSFER"] = {"id": 404, "name": "Transfer", "sku": "005/TRANSFER",
                               "dimensions": {"length": "0.40", "width": "0.80"}, "meta_data": []}
    o = _bestellung(JACKE)
    c = Client(artikel)
    w.ppom_veredelung_ergaenzen(o, c, {}, REGELN)
    neu = w.zubehoer_ergaenzen(o, c, {})
    assert [(z["sku"], z["quantity"]) for z in neu] == [("005/TRANSFER", 2)]


def test_nicht_zugeordnete_option_wird_gemeldet():
    jacke = copy.deepcopy(JACKE)
    for m in jacke["meta_data"]:
        if m["key"] == "zusatzoptionen":
            m["value"] = "Stick Audi (+5,34 €), Stick Ärmel rechts (+6,10 €)"
    o = _bestellung(jacke)
    w.ppom_veredelung_ergaenzen(o, Client(), {}, REGELN)
    assert o["_ppom_offen"] == ["Zusatzoptionen „Stick Ärmel rechts“"]
    e = w.Einheit(shop="Weeber", art="bestellung", titel="9001", orders=[o],
                  wex_data=w.build_wex_data(o, {"datev_no": 1, "order_type": "AB"}, Client(), {}))
    erg = w.ShopErgebnis(shop="Weeber", shop_cfg={}, client=None, global_cfg={})
    erg.einheiten.append(e)
    w._pruefregeln(erg)
    assert any("„Stick Ärmel rechts“ gewählt, aber keiner Veredelung zugeordnet" in t for t in e.warnungen)


def test_alles_zugeordnet_keine_meldung():
    o = _bestellung(JACKE)
    w.ppom_veredelung_ergaenzen(o, Client(), {}, REGELN)
    assert "_ppom_offen" not in o
