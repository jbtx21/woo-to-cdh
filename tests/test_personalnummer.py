"""Frage 6: Personalnummer an der Ensinger-Kasse.

- diagnose.py --felder listet die Meta-Felder eines Shops (nur Namen,
  Häufigkeit und Muster, keine Werte), um den Schlüssel des Checkout-Felds
  zu finden.
- extra_excel_meta mit checkout_key: Teambestellung „Ja" → PPOM-Feld an der
  Position, sonst → Checkout-Feld an der Bestellung.
Alle Werte erfunden.
"""
import copy
import json
import sys

import diagnose
import woo_to_cdh as w


def _meta(key, wert, anzeige=None):
    m = {"key": key, "value": wert}
    if anzeige:
        m["display_key"], m["display_value"] = anzeige, wert
    return m


def _bestellung(oid, bestell_meta, positions_meta):
    return {"id": oid, "number": str(oid), "meta_data": bestell_meta,
            "line_items": [{"meta_data": positions_meta}]}


BESTELLUNGEN = [
    # An der Bestellung liefert die REST-API nur key/value, kein display_key
    _bestellung(1, [_meta("_billing_personalnr", "4711"),
                    _meta("_wc_order_attribution_source_type", "typein")],
                [_meta("teambestellung", "Nein", "Teambestellung")]),
    _bestellung(2, [_meta("_wc_order_attribution_source_type", "typein")],
                [_meta("teambestellung", "Ja", "Teambestellung"),
                 _meta("personalnummer", "0815", "Personalnummer"),
                 _meta("mitarbeiterin", "Erika", "Vorname")]),
]


# --- diagnose.py --felder ------------------------------------------------------

def test_muster_ohne_werte():
    assert diagnose.muster("4711") == "####"
    assert diagnose.muster("Ja") == "xx"
    assert diagnose.muster("A-12 b") == "x-## x"
    assert diagnose.muster("") == "(leer)" and diagnose.muster(None) == "(leer)"
    assert diagnose.muster([1, 2]) == "(Liste)"
    assert diagnose.muster("1" * 30) == "#" * 16 + "…"


def test_felder_uebersicht():
    aufrufe = []

    def abruf(path, params=None):
        aufrufe.append((path, params))
        return 200, copy.deepcopy(BESTELLUNGEN)

    erg = diagnose.felder_uebersicht(abruf)
    assert aufrufe == [("/orders", {"per_page": 50, "orderby": "date", "order": "desc"})]
    assert erg["anzahl"] == 2
    b = {f["key"]: f for f in erg["bestellung"]}
    assert b["_billing_personalnr"] == {"key": "_billing_personalnr", "anzeige": "",
                                        "bestellungen": 1, "muster": ["####"], "kandidat": True}
    assert b["_wc_order_attribution_source_type"]["bestellungen"] == 2
    assert erg["bestellung"][0]["key"] == "_wc_order_attribution_source_type"   # häufigste zuerst
    p = {f["key"]: f for f in erg["position"]}
    assert p["personalnummer"]["kandidat"] and not p["mitarbeiterin"]["kandidat"]
    assert p["teambestellung"]["muster"] == ["xx", "xxxx"]
    text = json.dumps(erg, ensure_ascii=False)
    for wert in ("4711", "0815", "Erika", "typein"):
        assert wert not in text


def test_felder_uebersicht_fehler():
    assert "Status 401" in diagnose.felder_uebersicht(lambda p, q=None: (401, None))["fehler"]


def test_cli_felder_statt_diagnose(monkeypatch, capsys):
    shop = {"name": "Ensinger-Shop", "url": "https://shop.example/e/",
            "consumer_key": "ck_TEST", "consumer_secret": "cs_TEST"}
    gesehen = []
    monkeypatch.setattr(w, "config_vorhanden", lambda: True)
    monkeypatch.setattr(diagnose, "lade_shops", lambda nur: (gesehen.append(nur) or [shop], "test"))
    monkeypatch.setattr(diagnose, "diagnose_shop", lambda s: gesehen.append("diagnose"))
    monkeypatch.setattr(diagnose, "get", lambda *a, **k: (200, copy.deepcopy(BESTELLUNGEN)))
    monkeypatch.setattr(sys, "argv", ["diagnose.py", "Ensinger-Shop", "--felder"])
    assert diagnose.main() == 0
    assert gesehen == ["Ensinger-Shop"]
    out = capsys.readouterr().out
    assert "_billing_personalnr  ·  in 1 Bestellungen  ·  ####  ← Personalnummer?" in out
    assert "4711" not in out and "ck_TEST" not in out


# --- Personalnummer in der Excel ----------------------------------------------------

SHOP = {"extra_excel_meta": [
    {"key": "teambestellung", "label": "Teambestellung"},
    {"key": "personalnummer", "label": "Personalnummer", "checkout_key": "_billing_personalnr"},
]}


def _wert(bestellung, shop=SHOP):
    regeln = w._zusatzfeld_regeln(shop)
    return w._zusatzfeld_wert(bestellung, bestellung["line_items"][0], "personalnummer", regeln)


def test_regeln_aus_der_konfiguration():
    assert w._zusatzfeld_regeln(SHOP) == {"personalnummer": ("_billing_personalnr", "teambestellung")}
    assert w._zusatzfeld_regeln({"extra_excel_meta": ["personalnummer"]}) == {}


def test_selbstbestellung_nimmt_checkout_feld():
    assert _wert(BESTELLUNGEN[0]) == "4711"


def test_teambestellung_nimmt_ppom_feld():
    b = copy.deepcopy(BESTELLUNGEN[1])
    b["meta_data"].append(_meta("_billing_personalnr", "9999"))   # eigene Nummer der Bestellerin
    assert _wert(b) == "0815"


def test_ohne_checkout_wert_bleibt_ppom():
    b = _bestellung(3, [], [_meta("personalnummer", "1234")])
    assert _wert(b) == "1234"


def test_ohne_regel_wie_bisher():
    shop = {"extra_excel_meta": ["personalnummer"]}
    assert _wert(BESTELLUNGEN[0], shop) == ""                     # Checkout-Feld unbekannt
    assert _wert(BESTELLUNGEN[1], shop) == "0815"


def test_excel_zeile_nutzt_regel(orders, client):
    o = orders["einzeln"]
    o["meta_data"] = [_meta("_billing_personalnr", "4711")]
    item = o["line_items"][0]
    item["meta_data"] = [_meta("teambestellung", "Nein")]
    zeile = w._row_from_order_position(o, item, {**SHOP, "datev_no": 1}, client, {})
    assert zeile[-2:] == ["Nein", "4711"]


def test_oberflaeche_behaelt_checkout_key(tmp_path):
    """Excel-Felder in der Oberfläche ändern darf checkout_key nicht löschen."""
    import yaml
    import einstellungen_api as ea
    einst = {"shops": [{"id": "ensinger", "name": "Ensinger-Shop", "url": "https://shop.example/e/",
                        "datev_no": 14020, "extra_excel_meta": copy.deepcopy(SHOP["extra_excel_meta"])}]}
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump(einst), encoding="utf-8")
    api = ea.EinstellungenApi(tmp_path, benutzer="t")
    st = api.laden()
    st["shops"][0]["excel"][1]["label"] = "Personal-Nr."
    st["shops"][0]["excel"].append({"key": "mitarbeiterin", "label": "Vorname"})
    assert api.sichern({"shops": st["shops"], "global": st["global"], "token": st["token"]})["ok"]
    neu = yaml.safe_load((tmp_path / "einstellungen.yaml").read_text(encoding="utf-8"))
    assert neu["shops"][0]["extra_excel_meta"] == [
        {"key": "teambestellung", "label": "Teambestellung"},
        {"checkout_key": "_billing_personalnr", "key": "personalnummer", "label": "Personal-Nr."},
        {"key": "mitarbeiterin", "label": "Vorname"}]
