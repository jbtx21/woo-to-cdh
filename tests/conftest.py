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


@pytest.fixture(autouse=True)
def _versandarten_nicht_merken():
    """Jeder Test mit eigenen Versandzonen — nichts aus dem vorigen merken."""
    w.versandarten_vergessen()
    yield
    w.versandarten_vergessen()


# --- WooCommerce ohne Netz (Welle 3/4) --------------------------------------

class FakeWoo(w.WooClient):
    """WooClient ohne Netz. Bestellungen je Shop-URL, Schreibzugriffe protokolliert."""

    orders_by_url: dict = {}
    zones_by_url: dict = {}        # url → {zone_id: [methoden]}
    puts: list = []
    gets: list = []

    def _get(self, path, params=None):
        FakeWoo.gets.append(path)
        url = self.base.replace("/wp-json/wc/v3", "/")
        if path == "/shipping/zones":
            return [{"id": z} for z in FakeWoo.zones_by_url.get(url, {})]
        if path.startswith("/shipping/zones/"):
            return FakeWoo.zones_by_url[url][int(path.split("/")[3])]
        if path == "/orders":
            if params.get("page") != 1:
                return []
            return FakeWoo.orders_by_url.get(url, [])
        parts = path.strip("/").split("/")          # products/10/variations/11
        pid = int(parts[1])
        vid = int(parts[3]) if len(parts) > 3 else 0
        ek, vk = PRICES[(pid, vid)]
        return {"dimensions": {"length": ek, "width": vk}}

    def _put(self, path, data):
        FakeWoo.puts.append((path, data))
        return {}


@pytest.fixture
def umgebung(tmp_path, monkeypatch, orders):
    FakeWoo.orders_by_url = {
        "https://shop.example/einzeln/": [orders["einzeln"]],
        "https://shop.example/agrar/": orders["trenn"],
        "https://shop.example/mitarbeiter/": orders["mitarbeitershop"],
    }
    FakeWoo.puts, FakeWoo.gets, FakeWoo.zones_by_url = [], [], {}
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", FIXTURES / "lieferadressen_test.yaml")
    cdh = []
    monkeypatch.setattr(w, "start_cdh_wex_import",
                        lambda path, cfg: cdh.append(path.name) or True)
    shop = {"consumer_key": "ck_TEST", "consumer_secret": "cs_TEST",
            "datev_no": 10000, "order_type": "AB"}
    cfg = {
        "cdh_import_folder": str(tmp_path / "wex"),
        "excel_export_folder": str(tmp_path / "excel"),
        "status_after_export": "wc-completed",
        "order_no_with_name": True,
        "shops": [
            {**shop, "name": "Beispiel-Shop", "url": "https://shop.example/einzeln/"},
            {**shop, "name": "Agrar-Shop", "url": "https://shop.example/agrar/",
             "combine_by_delivery": True},
            {**shop, "name": "Mitarbeiter-Shop", "url": "https://shop.example/mitarbeiter/",
             "combine_by_delivery": True, "aggregate_all_positions": True},
        ],
    }
    return {"cfg": cfg, "tmp": tmp_path, "cdh": cdh}
