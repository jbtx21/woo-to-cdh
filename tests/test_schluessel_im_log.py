"""Zugangsdaten nie in Log, Fehlermeldung oder Konsole (Vorfall 21.–23.09.2026:
requests schrieb bei 401/404 die URL mit consumer_key/-secret ins Log).

Die Test-Schlüssel werden zur Laufzeit gebaut, damit der Repo-Scan
(test_keine_echten_schluessel_im_repo) sie nicht als echte meldet.
"""
import io
import logging

import pytest
import requests

import woo_to_cdh as w

KEY = "ck_" + "d5" * 20
SECRET = "cs_" + "69" * 20
URL = (f"https://shop.example/caf-shop/wp-json/wc/v3/orders?status=processing"
       f"&consumer_key={KEY}&consumer_secret={SECRET}")


def _sauber(text):
    return KEY not in text and SECRET not in text and "d5d5d5d5" not in text


def test_ohne_schluessel():
    t = w.ohne_schluessel(f"401 Client Error: Unauthorized for url: {URL}")
    assert _sauber(t)
    assert "consumer_key=***&consumer_secret=***" in t
    assert w.ohne_schluessel(f"Schlüssel {KEY} steht hier") == "Schlüssel ck_*** steht hier"
    assert w.ohne_schluessel("ck_NEU_ROTIEREN bleibt lesbar") == "ck_NEU_ROTIEREN bleibt lesbar"


class _Antwort:
    status_code = 401

    def raise_for_status(self):
        raise requests.HTTPError(f"401 Client Error: Unauthorized for url: {URL}", response=self)


def test_http_fehler_ohne_schluessel(monkeypatch):
    monkeypatch.setattr(w.requests.Session, "get", lambda *a, **k: _Antwort())
    client = w.WooClient("https://shop.example/caf-shop/", KEY, SECRET)
    with pytest.raises(requests.HTTPError) as fehler:
        client._get("/orders")
    assert _sauber(str(fehler.value))
    assert fehler.value.response.status_code == 401          # Antwort bleibt erhalten
    assert fehler.value.__cause__ is None and fehler.value.__suppress_context__


def test_verbindungsfehler_ohne_schluessel(monkeypatch):
    def kaputt(*a, **k):
        raise requests.ConnectionError(
            f"HTTPSConnectionPool(host='shop.example', port=443): Max retries exceeded "
            f"with url: /caf-shop/wp-json/wc/v3/orders?consumer_key={KEY}&consumer_secret={SECRET}")
    monkeypatch.setattr(w.requests.Session, "put", kaputt)
    client = w.WooClient("https://shop.example/caf-shop/", KEY, SECRET)
    with pytest.raises(requests.ConnectionError) as fehler:
        client._put("/orders/1", {})
    assert _sauber(str(fehler.value))


def test_log_formatter_auch_im_traceback():
    puffer = io.StringIO()
    h = logging.StreamHandler(puffer)
    h.setFormatter(w.SchluesselFormatter("%(levelname)s %(message)s"))
    log = logging.getLogger("test_schluessel")
    log.addHandler(h)
    log.propagate = False
    try:
        raise RuntimeError(f"kaputt: {URL}")
    except RuntimeError:
        log.exception("Shop CAF-Shop: API-Fehler: %s", URL)
    text = puffer.getvalue()
    assert "API-Fehler" in text and "Traceback" in text and _sauber(text)


def test_setup_logging_nutzt_formatter(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "LOG_DIR", tmp_path)
    root = logging.getLogger()
    alt = root.handlers[:]
    root.handlers = []
    try:
        w.setup_logging("INFO")
        logging.error("Shop CAF-Shop: API-Fehler: %s", URL)
        for h in root.handlers:
            h.flush()
        text = (tmp_path / "woo_to_cdh.log").read_text(encoding="utf-8")
        assert "API-Fehler" in text and _sauber(text)
    finally:
        for h in root.handlers:
            h.close()
        root.handlers = alt


def test_diagnose_verbindungsfehler_ohne_schluessel(monkeypatch):
    import diagnose as d

    def kaputt(*a, **k):
        raise requests.ConnectionError(f"Max retries exceeded with url: {URL}")
    monkeypatch.setattr(d.requests, "get", kaputt)
    status, text = d.get("https://shop.example/wp-json/wc/v3",
                         {"consumer_key": KEY, "consumer_secret": SECRET}, "/orders")
    assert status == 0 and _sauber(text)


def test_pruefergebnis_ohne_schluessel(tmp_path, monkeypatch):
    """Welle 3/6: Sperrtexte gehen in die Oberfläche — dort nie Schlüssel."""
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", tmp_path / "fehlt.yaml")
    monkeypatch.setattr(w.requests.Session, "get", lambda *a, **k: _Antwort())
    cfg = {"shops": [{"name": "CAF-Shop", "url": "https://shop.example/caf-shop/",
                      "consumer_key": KEY, "consumer_secret": SECRET,
                      "datev_no": 1, "order_type": "AB"}]}
    pruef = w.abrufen(cfg)
    assert pruef.shops[0].sperren and _sauber(" ".join(pruef.shops[0].sperren))
