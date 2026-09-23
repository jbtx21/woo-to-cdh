"""Fehlen Zugangsdaten für einen Shop, wird er mit klarer Meldung
übersprungen — kein KeyError, die übrigen Shops laufen weiter.

Fake-Keys bewusst nicht hex, damit der Schlüssel-Scanner nicht anschlägt.
"""
import logging

import pytest
import yaml

import diagnose as d
import migrate_config as m
import woo_to_cdh as w


def _config():
    return {"shops": [
        {"name": "CAF-Shop", "url": "https://shop.example/caf/",
         "consumer_key": "ck_TEST_CAF", "consumer_secret": "cs_TEST_CAF",
         "datev_no": 1, "order_type": "AB"},
        {"name": "Xond-Shop", "url": "https://shop.example/xond/",
         "consumer_key": "ck_TEST_XO", "consumer_secret": "cs_TEST_XO",
         "datev_no": 2, "order_type": "AB"},
    ]}


@pytest.fixture
def split_ohne_xond(tmp_path, monkeypatch):
    """Geteilte Konfiguration, in zugang.yaml fehlt der Xond-Shop."""
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(_config(), ziel_einstellungen=ziel_e, ziel_zugang=ziel_z)
    zugang = yaml.safe_load(ziel_z.read_text(encoding="utf-8"))
    del zugang["shops"]["Xond-Shop"]
    ziel_z.write_text(yaml.safe_dump(zugang, allow_unicode=True), encoding="utf-8")

    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", ziel_z)
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(w, "LOCK_PATH", tmp_path / "running.lock")
    monkeypatch.setattr(w, "setup_logging", lambda level="INFO": None)
    return tmp_path


def test_fehlende_zugangsdaten():
    assert w.fehlende_zugangsdaten({"consumer_key": "a", "consumer_secret": "b"}) == []
    assert w.fehlende_zugangsdaten({"consumer_key": "a"}) == ["consumer_secret"]
    assert w.fehlende_zugangsdaten({"consumer_key": " ", "consumer_secret": None}) == \
        ["consumer_key", "consumer_secret"]


def test_main_ueberspringt_shop_ohne_zugang(split_ohne_xond, monkeypatch, caplog):
    bearbeitet = []
    monkeypatch.setattr(w, "process_shop", lambda shop, cfg: bearbeitet.append(
        shop["name"]) or {"ok": 1, "errors": 0})

    with caplog.at_level(logging.INFO):
        assert w.main() == 1                 # Fehler zählt, Lauf läuft durch

    assert bearbeitet == ["CAF-Shop"]
    fehler = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(fehler) == 1
    assert "Xond-Shop" in fehler[0] and "zugang.yaml" in fehler[0]
    assert "consumer_key" in fehler[0] and "übersprungen" in fehler[0]
    assert not (split_ohne_xond / "running.lock").exists()


def test_diagnose_ueberspringt_shop_ohne_zugang(split_ohne_xond, monkeypatch, capsys):
    geprueft = []
    monkeypatch.setattr(d, "diagnose_shop", lambda s: geprueft.append(s["name"]))
    monkeypatch.setattr(d.sys, "argv", ["diagnose.py"])
    assert d.main() == 0
    assert geprueft == ["CAF-Shop"]
    assert "Xond-Shop: Zugangsdaten fehlen" in capsys.readouterr().err
