"""Excel-Kontrollliste: eine Zeile je Position, PPOM-Felder, keine Zusammenfassung."""
import openpyxl

import woo_to_cdh as w


def test_ppom_felder_an_der_position():
    order = {"meta_data": []}
    item = {"meta_data": [{"key": "mitarbeiterin", "value": "Jonas", "display_key": "Vorname"}]}
    assert w._read_order_meta(order, "mitarbeiterin", item) == "Jonas"


def test_meta_varianten():
    order = {"meta_data": [{"key": "_personalnummer", "value": "P-9"}]}
    assert w._read_order_meta(order, "personalnummer", {"meta_data": []}) == "P-9"          # Unterstrich
    item = {"meta_data": [{"key": "Personalnummer", "value": "P-1"}]}
    assert w._read_order_meta(order, "personalnummer", item) == "P-1"                       # Position gewinnt
    item = {"meta_data": [{"key": "x", "display_key": "personalnummer", "value": "P-2"}]}
    assert w._read_order_meta({"meta_data": []}, "personalnummer", item) == "P-2"           # display_key
    item = {"meta_data": [{"key": "teambestellung", "value": "1", "display_value": "Ja"}]}
    assert w._read_order_meta({"meta_data": []}, "teambestellung", item) == "Ja"            # lesbare Fassung


def test_excel_eine_zeile_je_position(orders, client, tmp_path):
    cfg = {"datev_no": 10000, "extra_excel_meta": [
        {"key": "teambestellung", "label": "Teambestellung"},
        {"key": "personalnummer", "label": "Personalnummer"}]}
    out = tmp_path / "t.xlsx"
    w.write_excel_export(out, orders["mitarbeitershop"], cfg, client, {})
    rows = list(openpyxl.load_workbook(out).active.iter_rows(values_only=True))
    head, body = rows[0], rows[1:]
    assert len(head) == 21 + 2
    assert len(body) == 4                     # 1 + 1 + 2 Positionen, nichts addiert
    by = {r[head.index("Bestellnummer")]: r for r in body}
    assert by["2771"][head.index("Personalnummer")] == "P-1001"
    assert by["2771"][head.index("Artikeltext 2")] == "Farbe: mindful blue, Größe: M"
