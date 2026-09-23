"""Das Adressen-Tool zieht die Shop-Namen über load_config() (Welle 2).

tkinter wird nur zum Import gebraucht; ohne Anzeige. Fehlt es (headless CI),
wird der Test übersprungen.
"""
import pytest

pytest.importorskip("tkinter")

import adressen_gui as g   # noqa: E402
import migrate_config as m  # noqa: E402
import woo_to_cdh as w      # noqa: E402


def _mini_config():
    return {"shops": [
        {"name": "CAF-Shop", "url": "https://shop.example/caf/",
         "consumer_key": "ck_TEST_CAF", "consumer_secret": "cs_TEST_CAF",
         "datev_no": 1, "order_type": "AB"},
        {"name": "Xond-Shop", "url": "https://shop.example/xond/",
         "consumer_key": "ck_TEST_XO", "consumer_secret": "cs_TEST_XO",
         "datev_no": 2, "order_type": "AB"},
    ]}


def test_shop_namen_aus_split(tmp_path, monkeypatch):
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(_mini_config(), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z)
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", ziel_z)
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")

    assert g.load_shop_names() == ["CAF-Shop", "Xond-Shop"]


def test_shop_namen_leer_wenn_nichts_da(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", tmp_path / "einstellungen.yaml")
    monkeypatch.setattr(w, "ZUGANG_PATH", tmp_path / "zugang.yaml")
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")
    assert g.load_shop_names() == []
