"""
Golden-WEX: Die erzeugten Dateien müssen Byte für Byte den gespeicherten
entsprechen. Jede Formatänderung wird damit als Diff sichtbar.

Absichtlich geändert? Neu erzeugen mit:
    set UPDATE_GOLDEN=1 && python -m pytest tests/test_golden.py      (cmd)
    $env:UPDATE_GOLDEN=1; python -m pytest tests/test_golden.py      (PowerShell)
und den Diff vor dem Commit prüfen.
"""
import os

import pytest

import woo_to_cdh as w
from conftest import GOLDEN, build

UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


def _check(name, data, tmp_path):
    out = tmp_path / name
    w.write_cdh_wex(out, data)
    # write_text schreibt auf Windows \r\n, sonst \n. Die produktive Datei
    # bleibt so; verglichen wird unabhängig vom Zeilenende.
    ist = out.read_bytes().replace(b"\r\n", b"\n")
    soll_pfad = GOLDEN / name
    if UPDATE or not soll_pfad.exists():
        soll_pfad.write_bytes(ist)
        if not UPDATE:
            pytest.skip(f"{name} neu angelegt — beim nächsten Lauf wird verglichen")
    soll = soll_pfad.read_bytes().replace(b"\r\n", b"\n")
    assert ist == soll, f"{name} weicht ab — Diff prüfen"


def test_golden_einzeln(orders, client, tmp_path):
    d = build(orders["einzeln"], client)
    d["order_no_wex"] = f"{d['order_no']} {d['person_name']}"
    _check("einzeln.wex", d, tmp_path)


def test_golden_trennzeilen(orders, client, tmp_path):
    data = [build(o, client) for o in orders["trenn"]]
    _check("sammel_trennzeilen.wex", w.build_combined_wex_data(data, {"datev_no": 10000}, "Bondorf"), tmp_path)


def test_golden_zusammengefasst(orders, client, delivery_table, tmp_path):
    sender = {"name1": "Beispiel Austria GmbH", "street": "Werkplatz 1", "postcode": "4863", "city": "Seewalchen", "country": "AT"}
    data = [build(o, client, sender_address=sender) for o in orders["mitarbeitershop"][1:]]
    c = w.build_combined_wex_data(data, {"datev_no": 10000, "aggregate_all_positions": True}, "Lenzing")
    w.apply_delivery_address(c, "Mitarbeiter-Shop", "Lenzing", delivery_table)
    _check("sammel_zusammengefasst.wex", c, tmp_path)
