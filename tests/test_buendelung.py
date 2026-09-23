"""Sammelaufträge je Lieferort."""
import woo_to_cdh as w
from conftest import block, build, value, wex_text


def _positions(c):
    return [(p["quantity"], p.get("article_no") or f"[{p['description']} / {p.get('variant_text', '')}]")
            for p in c["positions"]]


def test_trennzeilen_zeigen_personen(orders, client):
    """Regression: nach dem Sender-Umbau standen hier die Firmennamen."""
    data = [build(o, client) for o in orders["trenn"]]
    c = w.build_combined_wex_data(data, {"datev_no": 10000}, "Bondorf")
    trenner = [p["variant_text"] for p in c["positions"] if not p.get("article_no")]
    assert trenner[:2] == ["Anna Weber", "Tim Roth"]


def test_trennzeilen_veredelungen_gesammelt_am_ende(orders, client):
    data = [build(o, client) for o in orders["trenn"]]
    c = w.build_combined_wex_data(data, {"datev_no": 10000}, "Bondorf")
    pos = _positions(c)
    assert pos[-2][1].startswith("[Veredelungen")
    assert pos[-1] == (12, "234/SRFX-LOGO")          # 7 + 5
    assert c["order_no"] == "3939+3940"


def test_sammel_ohne_feste_adresse_liefert_an_firma(orders, client, tmp_path):
    data = [build(o, client) for o in orders["trenn"]]
    c = w.build_combined_wex_data(data, {"datev_no": 10000}, "Bondorf")
    d = block(wex_text(c, tmp_path), "Delivery")
    assert value(d, "Street") == "Mühlgasse 12"      # nicht die Privatadresse
    assert value(d, "ModeOfShippment") == "Bondorf"


def test_voll_zusammengefasst_gleiche_varianten(orders, client):
    lenzing = [build(o, client) for o in orders["mitarbeitershop"][1:]]
    c = w.build_combined_wex_data(lenzing, {"datev_no": 10000, "aggregate_all_positions": True}, "Lenzing")
    pos = _positions(c)
    assert pos[0] == (1, "[Lenzing / 2 Bestellungen]")
    assert (3, "ENS-HOODY-M") in pos                  # 1 + 2
    assert (1, "ENS-HOODY-L") in pos                  # andere Größe bleibt getrennt


def test_voll_unterschiedliche_preise_bleiben_getrennt():
    pos = [
        {"quantity": 1, "article_no": "X", "variant_text": "M", "selling_price": "10.00", "buying_price": "5.00"},
        {"quantity": 1, "article_no": "X", "variant_text": "M", "selling_price": "12.00", "buying_price": "5.00"},
    ]
    assert len(w._aggregate_all_positions(pos)) == 2


def test_seewalchen_und_lenzing_getrennt_gleiche_adresse(orders, client, delivery_table, tmp_path):
    """Zwei Lieferorte, eine Anschrift — trotzdem zwei CDH-Aufträge."""
    groups = {}
    for o in orders["mitarbeitershop"]:
        d = build(o, client)
        groups.setdefault(d["mode_of_shipment"], []).append(d)
    assert set(groups) == {"Seewalchen", "Lenzing"}

    streets, modes = set(), set()
    for ort, os_ in groups.items():
        c = w.build_combined_wex_data(os_, {"datev_no": 10000, "aggregate_all_positions": True}, ort)
        assert w.apply_delivery_address(c, "Mitarbeiter-Shop", ort, delivery_table)
        d = block(wex_text(c, tmp_path, f"{ort}.wex"), "Delivery")
        streets.add(value(d, "Street"))
        modes.add(value(d, "ModeOfShippment"))
    assert streets == {"Werkplatz 1"}
    assert modes == {"Seewalchen", "Lenzing"}


def test_lieferort_schreibweise_egal(delivery_table):
    d = {"del_street": ""}
    assert w.apply_delivery_address(d, "Mitarbeiter-Shop", "  lenzing ", delivery_table)
    assert not w.apply_delivery_address(d, "Mitarbeiter-Shop", "Timbuktu", delivery_table)
