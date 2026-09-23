"""
xlsx_to_wex.py — Konvertiert eine XLSX aus dem Advanced-Order-Export-Plugin
in eine oder mehrere WEX-Dateien für den CDH-Import.

Dient als Einmal-Werkzeug, wenn Bestellungen NICHT über die API-Pipeline
verarbeitet werden konnten (z.B. weil der Shop noch nicht angeschlossen war
oder eine Bestellung im WooCommerce nach dem API-Lauf noch geändert wurde).

Aufruf:
    python xlsx_to_wex.py <xlsx-datei> <datev_no>

Beispiel:
    python xlsx_to_wex.py orders-allgaier.xlsx 10698

Erzeugt für jede Bestellnummer in der XLSX eine WEX-Datei im aktuellen
Ordner. Die WEX kann direkt per Doppelklick in CDH importiert werden.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import openpyxl


# Spalten aus dem Advanced-Order-Export-Plugin — 0-indexed
COL_ORDER_NO      = 0   # Bestellnummer
COL_STATUS        = 1   # Bestellstatus
COL_ORDER_DATE    = 2   # Auftragsdatum
COL_CUST_NOTE     = 3   # Kundenhinweis
COL_COMPANY       = 4   # Firma (Fakturierung)
COL_STREET        = 5   # Adresse 1 & 2 (Abrechnung)
COL_ZIP           = 6   # Postleitzahl (Abrechnung)
COL_CITY          = 7   # Stadt (Abrechnung)
COL_EMAIL         = 8   # E-Mail (Besteller)
COL_FIRST_NAME    = 9   # Vorname (Empfänger)
COL_LAST_NAME     = 10  # Nachname (Empfänger)
COL_DELIVERY_LOC  = 11  # Lieferort
COL_QUANTITY      = 12  # Anzahl
COL_SKU           = 13  # Artikelnummer
COL_ARTICLE_NAME  = 14  # Artikelname
COL_ARTICLE_TXT1  = 15  # Artikeltext 1 (Material o.ä.)
COL_ARTICLE_TXT2  = 16  # Artikeltext 2 (Größe o.ä.)
COL_VK_TOTAL      = 17  # VK Gesamt (was der Kunde zahlt, nicht verwendet)
COL_VK_ITEM       = 18  # VK Artikel (Einzel-VK, das wollen wir)
COL_EK_ITEM       = 19  # EK Artikel


def fmt_price(value) -> str:
    """Preis wie in bisherigen WEX-Dateien: mit Punkt, ohne Tausendertrenner."""
    if value is None or value == "":
        return "0.0"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.0"


def parse_date(value) -> str:
    """Wandelt das Auftragsdatum in TT.MM.JJJJ."""
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    if isinstance(value, str):
        # Format aus XLSX: '2026-09-07 08:55:34'
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            pass
    return datetime.now().strftime("%d.%m.%Y")


def build_wex(order_no: str, rows: list, datev_no: str) -> str:
    """
    Baut eine WEX-XML-Zeichenkette für eine Bestellung.
    rows enthält alle Zeilen aus der XLSX für diese Bestellung.
    """
    first = rows[0]
    company = escape(str(first[COL_COMPANY] or ""))
    contact = escape(f"{first[COL_FIRST_NAME] or ''} {first[COL_LAST_NAME] or ''}".strip())
    street  = escape(str(first[COL_STREET] or ""))
    zip_    = escape(str(first[COL_ZIP] or ""))
    city    = escape(str(first[COL_CITY] or ""))
    email   = escape(str(first[COL_EMAIL] or ""))
    date_   = parse_date(first[COL_ORDER_DATE])

    lines = []
    lines.append('<?xml version="1.0" encoding="utf-8" standalone="yes"?>')
    lines.append("<!--CDH-dietronic-WEX-Werbemittel-Exchange-XML-File-->")
    lines.append("<!--WEXVersion=1.0-->")
    lines.append('<PurchaseOrder XmlStandard="1" SystemId="CDH">')
    lines.append("  <OrderHeader>")

    # Sender = Kunde (Rechnungsadresse)
    lines.append("    <Sender>")
    lines.append(f"      <DatevNo>{datev_no}</DatevNo>")
    lines.append("      <NameAddress>")
    lines.append(f"        <Name1>{company}</Name1>")
    if contact:
        lines.append(f"        <Name2>{contact}</Name2>")
    lines.append(f"        <Street>{street}</Street>")
    lines.append(f"        <PostalCodeCity>{zip_}</PostalCodeCity>")
    lines.append(f"        <City>{city}</City>")
    lines.append("        <Country>DE</Country>")
    if email:
        lines.append(f"        <Email>{email}</Email>")
    lines.append("      </NameAddress>")
    lines.append("    </Sender>")

    # Delivery = Lieferadresse (bei diesem Export ist das dieselbe Firma)
    lines.append("    <Delivery>")
    lines.append("      <ModeOfShippment>Versand pro Paket</ModeOfShippment>")
    lines.append("      <NameAddress>")
    lines.append(f"        <Name1>{company}</Name1>")
    if contact:
        lines.append(f"        <Name2>{contact}</Name2>")
    lines.append(f"        <Street>{street}</Street>")
    lines.append(f"        <PostalCodeCity>{zip_}</PostalCodeCity>")
    lines.append(f"        <City>{city}</City>")
    lines.append("        <Country>DE</Country>")
    if email:
        lines.append(f"        <Email>{email}</Email>")
    lines.append("      </NameAddress>")
    lines.append("    </Delivery>")

    lines.append(f"    <OrderNo>{escape(str(order_no))}</OrderNo>")
    lines.append(f"    <OrderDate>{date_}</OrderDate>")
    lines.append("    <OrderId>AB</OrderId>")
    lines.append("  </OrderHeader>")

    # Positionen
    lines.append("  <ListOfOrderDetails>")
    for row in rows:
        qty  = int(row[COL_QUANTITY] or 1)
        sku  = escape(str(row[COL_SKU] or ""))
        name = escape(str(row[COL_ARTICLE_NAME] or ""))
        txt2 = row[COL_ARTICLE_TXT2] or ""
        vk   = fmt_price(row[COL_VK_ITEM])
        ek   = fmt_price(row[COL_EK_ITEM])

        lines.append("    <OrderDetails>")
        lines.append(f"      <Quantity>{qty}</Quantity>")
        lines.append(f"      <ArticleNo1>{sku}</ArticleNo1>")
        lines.append(f"      <Description1>{name}</Description1>")
        if txt2:
            lines.append(f"      <DescriptionText2>{escape(str(txt2))}</DescriptionText2>")
        lines.append(f"      <SellingPrice>{vk}</SellingPrice>")
        lines.append(f"      <BuyingPrice>{ek}</BuyingPrice>")
        lines.append("    </OrderDetails>")
    lines.append("  </ListOfOrderDetails>")
    lines.append("</PurchaseOrder>")

    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) < 3:
        print("Aufruf: python xlsx_to_wex.py <xlsx-datei> <datev_no>")
        return 2

    xlsx_path = Path(sys.argv[1])
    datev_no  = sys.argv[2]

    if not xlsx_path.exists():
        print(f"Datei nicht gefunden: {xlsx_path}")
        return 2

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    # Zeilen nach Bestellnummer gruppieren
    orders: dict[str, list] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # Header überspringen
        if not row[COL_ORDER_NO]:
            continue
        order_no = str(row[COL_ORDER_NO]).strip()
        orders.setdefault(order_no, []).append(row)

    if not orders:
        print("Keine Bestellungen in der XLSX gefunden.")
        return 1

    print(f"{len(orders)} Bestellung(en) gefunden. DatevNo: {datev_no}")

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = xlsx_path.parent
    for order_no, rows in orders.items():
        wex_xml = build_wex(order_no, rows, datev_no)
        out_file = out_dir / f"orders-{date_str}-{order_no}.wex"
        # BOM voranstellen, wie CDH es erwartet
        with out_file.open("w", encoding="utf-8-sig", newline="") as f:
            f.write(wex_xml)
        print(f"  → {out_file.name} ({len(rows)} Positionen)")

    print(f"\nAlle Dateien liegen in: {out_dir}")
    print("Zum Import: WEX-Datei doppelklicken (oder CDH_WEX.EXE aufrufen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
