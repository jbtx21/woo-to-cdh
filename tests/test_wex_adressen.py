"""Adressblöcke der WEX: Sender = Hauptkunde, Delivery = Lieferung."""
from conftest import block, build, value, wex_text


def test_sender_ist_rechnungsadresse(orders, client, tmp_path):
    xml = wex_text(build(orders["einzeln"], client), tmp_path)
    sender = block(xml, "Sender")
    assert value(sender, "Name1") == "Beispiel Fahrzeugbau GmbH"
    assert value(sender, "Street") == "Industriestraße 4"
    assert value(sender, "City") == "Herrenberg"


def test_sender_ohne_personennamen(orders, client, tmp_path):
    """Name2 im Sender würde den Ansprechpartner im CDH-Kundenstamm überschreiben."""
    xml = wex_text(build(orders["einzeln"], client), tmp_path)
    assert "<Name2 />" in block(xml, "Sender")


def test_delivery_ist_versandadresse(orders, client, tmp_path):
    xml = wex_text(build(orders["einzeln"], client), tmp_path)
    delivery = block(xml, "Delivery")
    assert value(delivery, "Street") == "Lindenweg 7"
    assert value(delivery, "City") == "Nufringen"
    assert value(delivery, "Name2") == "Laura Hess"


def test_feste_kundenadresse_bei_leerer_rechnung(orders, client, tmp_path):
    """Mitarbeitershops: Besteller geben keine Rechnungsadresse ein."""
    o = orders["mitarbeitershop"][0]
    ohne = build(o, client)
    assert ohne["street"] == ""  # genau das Problem vom 17.09.

    mit = build(o, client, sender_address={
        "name1": "Beispiel Austria GmbH", "street": "Werkplatz 1",
        "postcode": "4863", "city": "Seewalchen", "country": "AT"})
    sender = block(wex_text(mit, tmp_path), "Sender")
    assert value(sender, "Name1") == "Beispiel Austria GmbH"
    assert value(sender, "Street") == "Werkplatz 1"


def test_ek_als_buyingprice(orders, client, tmp_path):
    """PurchasePrice ignoriert CDH kommentarlos — nur BuyingPrice kommt an."""
    xml = wex_text(build(orders["einzeln"], client), tmp_path)
    assert "<BuyingPrice>12.1" in xml
    assert "PurchasePrice" not in xml


def test_bestellnummer_mit_name(orders, client, tmp_path):
    d = build(orders["einzeln"], client)
    d["order_no_wex"] = f"{d['order_no']} {d['person_name']}"
    assert value(wex_text(d, tmp_path), "OrderNo") == "402 Laura Hess"
