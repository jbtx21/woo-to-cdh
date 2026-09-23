"""Gemeinsame Hilfen für die Tests."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import woo_to_cdh as w  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"

# EK/VK je (product_id, variation_id) — so, wie sie im Shop in Länge/Breite stehen
PRICES = {
    (10, 11): ("12.10", "24.90"),
    (20, 0): ("3.20", "6.50"),
    (30, 31): ("33.64", "53.59"),
    (40, 0): ("4.07", "7.65"),
    (50, 51): ("18.45", "31.73"),
    (50, 52): ("18.45", "31.73"),
}


class FakeClient:
    """Liefert Preise wie die WooCommerce-API, ohne Netz."""

    def get_variation(self, product_id, variation_id):
        ek, vk = PRICES[(product_id, variation_id)]
        return {"dimensions": {"length": ek, "width": vk}}

    def get_product(self, product_id):
        ek, vk = PRICES[(product_id, 0)]
        return {"dimensions": {"length": ek, "width": vk}}


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def orders():
    data = json.loads((FIXTURES / "orders.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


@pytest.fixture
def delivery_table(monkeypatch):
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", FIXTURES / "lieferadressen_test.yaml")
    return w.load_delivery_addresses()


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    p = tmp_path / "exported.log"
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", p)
    return p


def build(order, client, **cfg):
    shop_cfg = {"datev_no": 10000, "order_type": "AB", **cfg}
    return w.build_wex_data(order, shop_cfg, client, {})


def wex_text(data, tmp_path, name="t.wex"):
    p = tmp_path / name
    w.write_cdh_wex(p, data)
    return p.read_text(encoding="utf-8-sig")


def block(xml, tag):
    """Inhalt eines Blocks wie <Sender>…</Sender>."""
    return xml.split(f"<{tag}>")[1].split(f"</{tag}>")[0]


def value(xml, tag):
    if f"<{tag}>" not in xml:
        return ""
    return xml.split(f"<{tag}>")[1].split(f"</{tag}>")[0]
