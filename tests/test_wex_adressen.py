"""Adressblöcke der WEX: Sender = nur DatevNo, Delivery = Lieferung."""
from conftest import block, build, value, wex_text

SENDER_LEER = """
      <DatevNo>10000</DatevNo>
      <NameAddress>
        <Name1 />
        <Name2 />
        <Street />
        <PostalCodeCity />
        <City />
        <Country />
        <Email />
      </NameAddress>
    """


def test_sender_nur_datevno(orders, client, tmp_path):
    """CDH-Test 23.09.2026: leerer Sender → Kopf aus dem Kundenstamm."""
    xml = wex_text(build(orders["einzeln"], client), tmp_path)
    assert block(xml, "Sender").replace("\r\n", "\n") == SENDER_LEER


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


def test_sender_address_wird_ignoriert(orders, client, tmp_path):
    """sender_address ist abgeschafft — auch gesetzt landet nichts im Sender."""
    o = orders["mitarbeitershop"][0]
    mit = build(o, client, sender_address={
        "name1": "Beispiel Austria GmbH", "street": "Werkplatz 1",
        "postcode": "4863", "city": "Seewalchen", "country": "AT",
        "email": "auftrag@beispiel.test"})
    assert block(wex_text(mit, tmp_path), "Sender").replace("\r\n", "\n") == SENDER_LEER


def test_ek_als_buyingprice(orders, client, tmp_path):
    """PurchasePrice ignoriert CDH kommentarlos — nur BuyingPrice kommt an."""
    xml = wex_text(build(orders["einzeln"], client), tmp_path)
    assert "<BuyingPrice>12.1" in xml
    assert "PurchasePrice" not in xml


def test_bestellnummer_mit_name(orders, client, tmp_path):
    d = build(orders["einzeln"], client)
    d["order_no_wex"] = f"{d['order_no']} {d['person_name']}"
    assert value(wex_text(d, tmp_path), "OrderNo") == "402 Laura Hess"
