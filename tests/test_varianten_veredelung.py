"""Variantentext und Veredelungen."""
import woo_to_cdh as w


def test_farbe_und_groesse_unabhaengig_von_reihenfolge():
    a = {"meta_data": [{"key": "pa_farbe", "display_value": "black"}, {"key": "pa_groesse", "display_value": "M"}]}
    b = {"meta_data": [{"key": "pa_groesse", "display_value": "M"}, {"key": "pa_farbe", "display_value": "black"}]}
    assert w._extract_variant_text(a) == w._extract_variant_text(b) == "black, M"
    assert w._variant_text_labeled(a) == "Farbe: black, Größe: M"


def test_nur_groesse():
    item = {"meta_data": [{"key": "groesse", "display_value": "4XL"}]}
    assert w._extract_variant_text(item) == "4XL"


def test_veredelungspraefixe():
    for sku in ("004/STICK", "316/DRUCK", "234/SRFX"):
        assert w._is_veredelung(sku), sku
    assert not w._is_veredelung("396/SHIRT-4XL")


def test_veredelung_innerhalb_bestellung_zusammengefasst(orders, client):
    from conftest import build
    d = build(orders["einzeln"], client)
    agg = w._aggregate_veredelungen(d["positions"])
    stick = [p for p in agg if p["article_no"] == "004/STICK-LOGO"]
    assert len(stick) == 1 and stick[0]["quantity"] == 2


def test_veredelung_mit_anderem_preis_bleibt_getrennt():
    pos = [
        {"article_no": "004/S", "quantity": 1, "selling_price": "6.50", "buying_price": "3.20"},
        {"article_no": "004/S", "quantity": 1, "selling_price": "9.00", "buying_price": "3.20"},
    ]
    assert len(w._aggregate_veredelungen(pos)) == 2
