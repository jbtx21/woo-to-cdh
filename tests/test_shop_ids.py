"""Welle 5: feste Shop-ids statt Namen zwischen einstellungen.yaml und zugang.yaml.

Fake-Keys bewusst nicht hex, damit der Schlüssel-Scanner nicht anschlägt.
"""
import logging

import pytest
import yaml

import migrate_config as m
import woo_to_cdh as w


def _dateien(tmp_path, einstellungen, zugang, monkeypatch=None):
    pe, pz = tmp_path / "einstellungen.yaml", tmp_path / "zugang.yaml"
    pe.write_text(yaml.safe_dump(einstellungen, allow_unicode=True), encoding="utf-8")
    pz.write_text(yaml.safe_dump(zugang, allow_unicode=True), encoding="utf-8")
    if monkeypatch:
        monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", pe)
        monkeypatch.setattr(w, "ZUGANG_PATH", pz)
        monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")
    return pe, pz


ALT_E = {"shops": [{"name": "CAF-Shop", "url": "https://x/caf/"},
                   {"name": "Ensinger-Shop", "url": "https://x/ens/"},
                   {"name": "Neu-Shop", "url": "https://x/neu/"}]}
ALT_Z = {"admin": {"users": []},
         "shops": {"CAF-Shop": {"consumer_key": "ck_TEST_CAF", "consumer_secret": "cs_TEST_CAF"},
                   "Ensinger-Shop": {"consumer_key": "ck_TEST_ENS", "consumer_secret": "cs_TEST_ENS"},
                   "Alt-Shop": {"consumer_key": "ck_TEST_ALT", "consumer_secret": "cs_TEST_ALT"}}}


@pytest.mark.parametrize("name, sid", [
    ("CAF-Shop", "caf"), ("Ensinger-Shop", "ensinger"), ("KVSW-Shop", "kvsw"),
    ("Allgaier-Shop", "allgaier"), ("Müller Shop", "mueller"), ("", "shop")])
def test_shop_id_aus_name(name, sid):
    assert w.shop_id_aus_name(name) == sid


def test_vergebe_ids_eindeutig_und_stabil():
    shops = [{"name": "CAF-Shop"}, {"id": "caf", "name": "Anders"}, {"name": "CAF Shop"}]
    zuordnung = m.vergebe_shop_ids(shops)
    assert [s["id"] for s in shops] == ["caf-2", "caf", "caf-3"]
    assert zuordnung == {"CAF-Shop": "caf-2", "Anders": "caf", "CAF Shop": "caf-3"}
    assert list(shops[0])[0] == "id"          # id steht vorn


def test_laden_ueber_id(tmp_path, monkeypatch):
    _dateien(tmp_path, {"shops": [{"id": "caf", "name": "Umbenannt", "url": "u"}]},
             {"shops": {"caf": {"consumer_key": "ck_A", "consumer_secret": "cs_A"}}},
             monkeypatch)
    cfg, _ = w.load_config()
    assert cfg["shops"][0]["consumer_key"] == "ck_A"   # Umbenennen kostet den Zugang nicht


def test_laden_rueckfall_name_mit_hinweis(tmp_path, monkeypatch, caplog):
    _dateien(tmp_path, ALT_E, ALT_Z, monkeypatch)
    with caplog.at_level(logging.INFO):
        cfg, _ = w.load_config()
    assert cfg["shops"][0]["consumer_key"] == "ck_TEST_CAF"
    assert "consumer_key" not in cfg["shops"][2]
    assert any("--shop-ids" in r.getMessage() for r in caplog.records)


def test_id_hat_vorrang_vor_name(tmp_path, monkeypatch):
    _dateien(tmp_path, {"shops": [{"id": "caf", "name": "CAF-Shop", "url": "u"}]},
             {"shops": {"caf": {"consumer_key": "ck_ID", "consumer_secret": "cs_ID"},
                        "CAF-Shop": {"consumer_key": "ck_NAME", "consumer_secret": "cs_NAME"}}},
             monkeypatch)
    cfg, _ = w.load_config()
    assert cfg["shops"][0]["consumer_key"] == "ck_ID"


def test_umstellen():
    e, z, zeilen = m.shop_ids_umstellen(ALT_E, ALT_Z)
    assert [s["id"] for s in e["shops"]] == ["caf", "ensinger", "neu"]
    assert set(z["shops"]) == {"caf", "ensinger", "Alt-Shop"}
    assert z["shops"]["caf"]["consumer_key"] == "ck_TEST_CAF"
    text = "\n".join(zeilen)
    assert "Name -> id" in text and "KEIN Zugang" in text and "bleibt stehen" in text
    # idempotent
    e2, z2, _ = m.shop_ids_umstellen(e, z)
    assert e2 == e and z2 == z


def test_migration_probelauf_schreibt_nichts(tmp_path):
    pe, pz = _dateien(tmp_path, ALT_E, ALT_Z)
    vorher = (pe.read_text(encoding="utf-8"), pz.read_text(encoding="utf-8"))
    text = m.shop_ids_migration(tmp_path, probelauf=True)
    assert (pe.read_text(encoding="utf-8"), pz.read_text(encoding="utf-8")) == vorher
    assert not (tmp_path / "Backup").exists()
    assert "Probelauf" in text and "ck_TEST" not in text and "cs_TEST" not in text


def test_migration_schreibt_mit_backup(tmp_path, monkeypatch):
    pe, pz = _dateien(tmp_path, ALT_E, ALT_Z, monkeypatch)
    text = m.shop_ids_migration(tmp_path, probelauf=False, datum="2026-10-02_080000")
    assert "ck_TEST" not in text
    assert (tmp_path / "Backup" / "zugang_2026-10-02_080000.yaml").exists()
    assert (tmp_path / "Backup" / "einstellungen_2026-10-02_080000.yaml").exists()
    z = yaml.safe_load(pz.read_text(encoding="utf-8"))
    assert "caf" in z["shops"] and "CAF-Shop" not in z["shops"]
    cfg, _ = w.load_config()
    assert cfg["shops"][1]["consumer_secret"] == "cs_TEST_ENS"


def test_cli_shop_ids(tmp_path, capsys):
    _dateien(tmp_path, ALT_E, ALT_Z)
    assert m.main(["--config", str(tmp_path / "config.yaml"), "--shop-ids", "--probelauf"]) == 0
    assert "Probelauf" in capsys.readouterr().out
    leer = tmp_path / "leer"
    leer.mkdir()
    assert m.main(["--config", str(leer / "config.yaml"), "--shop-ids"]) == 2


def test_praefixe_mit_bezeichnung_in_main(tmp_path, monkeypatch):
    """Die Oberfläche speichert Präfixe als {prefix, label} — main() muss beides lesen."""
    _dateien(tmp_path, {"veredelung_prefixes": [{"prefix": "004/", "label": "Stick"}, "006/"],
                        "shops": []}, {"shops": {}}, monkeypatch)
    monkeypatch.setattr(w, "LOCK_PATH", tmp_path / "running.lock")
    monkeypatch.setattr(w, "setup_logging", lambda level="INFO": None)
    monkeypatch.setattr(w, "VEREDELUNG_PREFIXES", w.VEREDELUNG_PREFIXES)
    assert w.main() == 0
    assert w.VEREDELUNG_PREFIXES == ("004/", "006/")


def test_status_nach_export_leer_im_shop_schaltet_global_ab():
    g = {"status_after_export": "completed"}
    assert w._status_after_export({}, g) == "completed"
    assert w._status_after_export({"status_after_export": ""}, g) == ""
    assert w._status_after_export({"status_after_export": None}, g) == ""
    assert w._status_after_export({"status_after_export": "wc-on-hold"}, g) == "on-hold"
