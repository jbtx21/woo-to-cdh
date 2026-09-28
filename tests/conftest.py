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
def _kein_netz(monkeypatch):
    """Tests gehen nie ins Netz (28.09.2026: ein Test ohne Test-Shop hatte echte
    Shops angefragt — der Proxy hat es geblockt). Jede Anfrage über requests
    an einen anderen Rechner als diesen lässt den Test sofort scheitern."""
    import requests.adapters
    from urllib.parse import urlsplit

    echt = requests.adapters.HTTPAdapter.send

    def send(self, request, *a, **kw):
        host = urlsplit(request.url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"Test wollte ins Netz: {host} — Test-Shop (client_factory) fehlt")
        return echt(self, request, *a, **kw)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)


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


# --- Artikel ohne Netz (Welle 10) -------------------------------------------

class ArtikelWoo(FakeWoo):
    """Artikel und Varianten je Shop-URL, Batch-Schreiben wie WooCommerce.

    katalog[url] = {"produkte": {id: produkt}, "varianten": {parent: {id: variante}},
                    "medien": {id: bild}}   # Mediathek, für images/image per id
    abgelehnt: ids, die der Shop im Batch mit Fehler beantwortet."""

    katalog: dict = {}
    posts: list = []
    abgelehnt: set = set()
    plugin_version: str | None = "2.7.0"      # CDH Required Accessories im Systemstatus

    def _shop(self):
        return ArtikelWoo.katalog[self.base.replace("/wp-json/wc/v3", "/")]

    def _bild(self, ref):
        """Wie WooCommerce: id → vorhandenes Bild, src → neu in die Mediathek."""
        medien = self._shop().setdefault("medien", {})
        if ref.get("id"):
            return copy.deepcopy(medien[ref["id"]])
        neu = {"id": 9000 + len(medien), "src": ref["src"],
               "name": ref["src"].rsplit("/", 1)[-1].rsplit(".", 1)[0]}
        medien[neu["id"]] = neu
        return copy.deepcopy(neu)

    @staticmethod
    def _auswahl(eintraege, params):
        params = params or {}
        if params.get("include"):
            ids = {int(i) for i in str(params["include"]).split(",")}
            return [copy.deepcopy(e) for i, e in eintraege.items() if i in ids]
        if params.get("page", 1) != 1:
            return []
        return [copy.deepcopy(e) for e in eintraege.values()]

    def _get(self, path, params=None):
        teile = path.strip("/").split("/")
        if path == "/system_status":
            plugins = [{"plugin": "woocommerce/woocommerce.php", "version": "9.3.0"}]
            if ArtikelWoo.plugin_version:
                plugins.append({"plugin": "cdh-required-accessories/cdh-required-accessories.php",
                                "version": ArtikelWoo.plugin_version})
            return {"active_plugins": plugins}
        if teile[0] == "products" and len(teile) == 1:
            return self._auswahl(self._shop()["produkte"], params)
        if teile[0] == "products" and len(teile) == 3 and teile[2] == "variations":
            return self._auswahl(self._shop()["varianten"].get(int(teile[1]), {}), params)
        return super()._get(path, params)

    def _post(self, path, data):
        ArtikelWoo.posts.append((path, copy.deepcopy(data)))
        teile = path.strip("/").split("/")
        shop = self._shop()
        ziel = shop["produkte"] if teile[1] == "batch" else shop["varianten"][int(teile[1])]
        antwort = []
        for d in data.get("update", []):
            if d["id"] in ArtikelWoo.abgelehnt:
                antwort.append({"id": d["id"], "error": {
                    "code": "product_invalid_sku",
                    "message": "Ungültige oder doppelte Artikelnummer."}})
                continue
            e = ziel[d["id"]]
            for k, v in d.items():
                if k == "dimensions":
                    e.setdefault("dimensions", {}).update(v)
                elif k == "images":
                    e["images"] = [self._bild(b) for b in v]
                elif k == "meta_data":                 # je Schlüssel ersetzen, Rest bleibt
                    alt = [m for m in e.get("meta_data") or []
                           if m["key"] not in {x["key"] for x in v}]
                    e["meta_data"] = alt + copy.deepcopy(v)
                elif k == "image":
                    e["image"] = self._bild(v) if v.get("id") or v.get("src") else None
                elif k != "id":
                    e[k] = v
            antwort.append(copy.deepcopy(e))
        return {"update": antwort}
