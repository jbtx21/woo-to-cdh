"""diagnose.py zieht seine Shops über woo_to_cdh.load_config() (Welle 2).

Fake-Keys bewusst nicht hex, damit der Schlüssel-Scanner nicht anschlägt.
"""
import yaml

import diagnose as d
import migrate_config as m
import woo_to_cdh as w


def _mini_config():
    return {"shops": [
        {"name": "CAF-Shop", "url": "https://shop.example/caf/",
         "consumer_key": "ck_TEST_CAF", "consumer_secret": "cs_TEST_CAF",
         "datev_no": 1, "order_type": "AB"},
        {"name": "Xond-Shop", "url": "https://shop.example/xond/",
         "consumer_key": "ck_TEST_XO", "consumer_secret": "cs_TEST_XO",
         "datev_no": 2, "order_type": "AB"},
    ]}


def _split_anlegen(tmp_path, monkeypatch):
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(_mini_config(), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z)
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", ziel_z)
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")


def test_lade_shops_nutzt_split_mit_schluesseln(tmp_path, monkeypatch):
    _split_anlegen(tmp_path, monkeypatch)
    shops, quelle = d.lade_shops()
    assert "einstellungen.yaml" in quelle
    caf = next(s for s in shops if s["name"] == "CAF-Shop")
    assert caf["consumer_key"] == "ck_TEST_CAF"   # Zugang wieder eingesetzt


def test_lade_shops_filtert_namen(tmp_path, monkeypatch):
    _split_anlegen(tmp_path, monkeypatch)
    shops, _ = d.lade_shops("Xond-Shop")
    assert [s["name"] for s in shops] == ["Xond-Shop"]


def test_lade_shops_fallback_config_yaml(tmp_path, monkeypatch):
    cfg_pfad = tmp_path / "config.yaml"
    cfg_pfad.write_text(yaml.safe_dump(_mini_config(), allow_unicode=True),
                        encoding="utf-8")
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", tmp_path / "einstellungen.yaml")
    monkeypatch.setattr(w, "ZUGANG_PATH", tmp_path / "zugang.yaml")
    monkeypatch.setattr(w, "CONFIG_PATH", cfg_pfad)
    shops, quelle = d.lade_shops()
    assert quelle == "config.yaml"
    assert len(shops) == 2
