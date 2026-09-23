"""Welle 2: config.yaml -> einstellungen.yaml + zugang.yaml.

Fake-Keys hier bewusst NICHT hex (kein c[ks]_[0-9a-f]{20,}), damit der
Schlüssel-Scanner diese Testdatei nicht fälschlich anschlägt.
"""
import copy

import pytest
import yaml

import migrate_config as m
import woo_to_cdh as w


def beispiel_config():
    return {
        "log_level": "INFO",
        "status_after_export": "completed",
        "veredelung_prefixes": ["004/", "234/"],
        "shops": [
            {"name": "CAF-Shop", "enabled": True,
             "url": "https://shop.example/caf/",
             "consumer_key": "ck_TEST_CAF", "consumer_secret": "cs_TEST_CAF",
             "datev_no": 19541, "order_type": "AB"},
            {"name": "Ensinger-Shop", "enabled": False,
             "url": "https://shop.example/ensinger/",
             "consumer_key": "ck_TEST_ENS", "consumer_secret": "cs_TEST_ENS",
             "datev_no": 14020, "order_type": "AB",
             "combine_by_delivery": True, "aggregate_all_positions": True,
             "sender_address": {"name1": "Ensinger GmbH", "country": "DE"}},
        ],
    }


# --- Passwort-Hash ----------------------------------------------------------

def test_hash_parameter_wie_vorgegeben():
    rec = m.hash_admin_password("geheim")
    assert rec["algo"] == "pbkdf2_sha256"
    assert rec["iterations"] >= 200_000
    assert len(bytes.fromhex(rec["salt"])) == 16      # Salt vorhanden
    assert len(bytes.fromhex(rec["hash"])) == 32      # SHA-256 = 32 Byte


def test_hash_deterministisch_bei_gleichem_salt():
    salt = b"0123456789abcdef"
    a = m.hash_admin_password("geheim", salt=salt)
    b = m.hash_admin_password("geheim", salt=salt)
    assert a == b
    c = m.hash_admin_password("geheim")               # neuer Zufalls-Salt
    assert c["hash"] != a["hash"]


def test_verify_round_trip():
    rec = m.hash_admin_password("richtig")
    assert m.verify_admin_password("richtig", rec) is True
    assert m.verify_admin_password("falsch", rec) is False
    assert m.verify_admin_password("egal", m.leerer_passwort_satz()) is False


def test_leeres_passwort_wirft():
    with pytest.raises(ValueError):
        m.hash_admin_password("")


# --- Split ------------------------------------------------------------------

def test_split_trennt_geheimnisse_ab():
    cfg = beispiel_config()
    einst, zugang = m.split_config(cfg)

    for shop in einst["shops"]:
        assert "consumer_key" not in shop
        assert "consumer_secret" not in shop

    assert zugang["shops"]["caf"]["consumer_key"] == "ck_TEST_CAF"
    assert zugang["shops"]["ensinger"]["consumer_secret"] == "cs_TEST_ENS"
    assert [s["id"] for s in einst["shops"]][:1] == ["caf"]


def test_split_behaelt_einstellungen():
    einst, _ = m.split_config(beispiel_config())
    ens = next(s for s in einst["shops"] if s["name"] == "Ensinger-Shop")
    assert ens["datev_no"] == 14020
    assert ens["aggregate_all_positions"] is True
    assert ens["sender_address"]["name1"] == "Ensinger GmbH"
    assert einst["veredelung_prefixes"] == ["004/", "234/"]


def test_split_laesst_original_unveraendert():
    cfg = beispiel_config()
    original = copy.deepcopy(cfg)
    m.split_config(cfg)
    assert cfg == original                            # deepcopy, keine Seiteneffekte


def test_kein_geheimnis_in_einstellungen():
    cfg = beispiel_config()
    einst, _ = m.split_config(cfg)
    assert m._enthaelt_geheimnis(cfg) is True         # Quelle hat Geheimnisse
    assert m._enthaelt_geheimnis(einst) is False      # Ergebnis nicht mehr


def test_split_admin_struktur_vorhanden():
    _, zugang = m.split_config(beispiel_config())
    assert zugang["admin"]["password"]["algo"] == "pbkdf2_sha256"
    assert zugang["admin"]["password"]["salt"] == ""  # leer bis gesetzt
    assert zugang["admin"]["users"] == []


def test_split_setzt_admin_passwort():
    _, zugang = m.split_config(beispiel_config(), admin_password="chef")
    assert m.verify_admin_password("chef", zugang["admin"]["password"]) is True


def test_split_behaelt_bestehenden_hash():
    bestehend = {"admin": {"password": m.hash_admin_password("alt"), "users": ["Jannik.Boekle"]}}
    _, zugang = m.split_config(beispiel_config(), vorhandener_zugang=bestehend)
    assert m.verify_admin_password("alt", zugang["admin"]["password"]) is True
    assert zugang["admin"]["users"] == ["Jannik.Boekle"]


# --- Probelauf --------------------------------------------------------------

def test_probelauf_nennt_vorhandensein_ohne_werte(tmp_path):
    cfg = beispiel_config()
    bericht = m.probelauf_bericht(
        cfg, ziel_einstellungen=tmp_path / "einstellungen.yaml",
        ziel_zugang=tmp_path / "zugang.yaml")

    assert "vorhanden" in bericht
    assert "CAF-Shop" in bericht
    # Aber niemals die Schlüsselwerte:
    assert "ck_TEST_CAF" not in bericht
    assert "cs_TEST_ENS" not in bericht


def test_probelauf_meldet_fehlenden_schluessel(tmp_path):
    cfg = beispiel_config()
    del cfg["shops"][0]["consumer_key"]
    bericht = m.probelauf_bericht(
        cfg, ziel_einstellungen=tmp_path / "e.yaml", ziel_zugang=tmp_path / "z.yaml")
    assert "FEHLT" in bericht


def test_probelauf_schreibt_nichts(tmp_path, capsys):
    cfg_pfad = tmp_path / "config.yaml"
    cfg_pfad.write_text(yaml.safe_dump(beispiel_config(), allow_unicode=True),
                        encoding="utf-8")

    rc = m.main(["--probelauf", "--config", str(cfg_pfad)])
    assert rc == 0
    assert not (tmp_path / "einstellungen.yaml").exists()
    assert not (tmp_path / "zugang.yaml").exists()

    out = capsys.readouterr().out
    assert "Probelauf" in out
    assert "ck_TEST_CAF" not in out                   # keine Werte in der Ausgabe


# --- Echtes Schreiben -------------------------------------------------------

def test_schreibe_erzeugt_beide_dateien(tmp_path):
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(beispiel_config(), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z)

    einst = yaml.safe_load(ziel_e.read_text(encoding="utf-8"))
    zugang = yaml.safe_load(ziel_z.read_text(encoding="utf-8"))
    assert not m._enthaelt_geheimnis(einst)
    assert zugang["shops"]["caf"]["consumer_key"] == "ck_TEST_CAF"


def test_schreibe_ohne_force_kein_ueberschreiben(tmp_path):
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    ziel_e.write_text("alt", encoding="utf-8")
    with pytest.raises(FileExistsError):
        m.schreibe_migration(beispiel_config(), ziel_einstellungen=ziel_e,
                             ziel_zugang=ziel_z)


def test_schreibe_mit_force_sichert_backup(tmp_path):
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    backup = tmp_path / "Backup"
    ziel_e.write_text("alter inhalt", encoding="utf-8")
    m.schreibe_migration(beispiel_config(), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z, force=True, backup_dir=backup)
    assert (backup / "einstellungen.yaml").read_text(encoding="utf-8") == "alter inhalt"


# --- Loader mit Rückfall ----------------------------------------------------

def test_load_config_nutzt_split(tmp_path, monkeypatch):
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(beispiel_config(), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z)
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", ziel_z)
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")  # existiert nicht

    cfg, quelle = w.load_config()
    assert "einstellungen.yaml" in quelle
    caf = next(s for s in cfg["shops"] if s["name"] == "CAF-Shop")
    assert caf["consumer_key"] == "ck_TEST_CAF"       # Geheimnis wieder eingesetzt


def test_load_config_faellt_auf_config_yaml_zurueck(tmp_path, monkeypatch):
    cfg_pfad = tmp_path / "config.yaml"
    cfg_pfad.write_text(yaml.safe_dump(beispiel_config(), allow_unicode=True),
                        encoding="utf-8")
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", tmp_path / "einstellungen.yaml")
    monkeypatch.setattr(w, "ZUGANG_PATH", tmp_path / "zugang.yaml")
    monkeypatch.setattr(w, "CONFIG_PATH", cfg_pfad)

    cfg, quelle = w.load_config()
    assert quelle == "config.yaml"
    caf = next(s for s in cfg["shops"] if s["name"] == "CAF-Shop")
    assert caf["consumer_key"] == "ck_TEST_CAF"


def test_load_config_nur_eine_neue_datei_faellt_zurueck(tmp_path, monkeypatch):
    # einstellungen.yaml da, zugang.yaml fehlt -> Rückfall auf config.yaml
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_e.write_text("shops: []", encoding="utf-8")
    cfg_pfad = tmp_path / "config.yaml"
    cfg_pfad.write_text(yaml.safe_dump(beispiel_config(), allow_unicode=True),
                        encoding="utf-8")
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", tmp_path / "zugang.yaml")
    monkeypatch.setattr(w, "CONFIG_PATH", cfg_pfad)

    cfg, quelle = w.load_config()
    assert quelle == "config.yaml"


def test_load_config_ohne_alles_wirft(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", tmp_path / "einstellungen.yaml")
    monkeypatch.setattr(w, "ZUGANG_PATH", tmp_path / "zugang.yaml")
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")
    with pytest.raises(FileNotFoundError):
        w.load_config()


def test_roundtrip_migration_dann_laden(tmp_path, monkeypatch):
    """split -> schreiben -> load_config ergibt wieder die Ausgangs-Config."""
    original = beispiel_config()
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(copy.deepcopy(original), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z)
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", ziel_z)
    monkeypatch.setattr(w, "CONFIG_PATH", tmp_path / "config.yaml")

    cfg, _ = w.load_config()
    for s in cfg["shops"]:
        assert s.pop("id")                  # feste Shop-id kommt dazu
    assert cfg == original


# --- config.yaml nach Backup verschieben ------------------------------------

def test_schreibe_verschiebt_config_nach_backup(tmp_path):
    cfg_pfad = tmp_path / "config.yaml"
    cfg_pfad.write_text(yaml.safe_dump(beispiel_config(), allow_unicode=True),
                        encoding="utf-8")
    backup = tmp_path / "Backup"

    ziel = m.schreibe_migration(
        beispiel_config(), ziel_einstellungen=tmp_path / "einstellungen.yaml",
        ziel_zugang=tmp_path / "zugang.yaml", backup_dir=backup,
        quelle_config=cfg_pfad, datum="2026-09-23")

    assert not cfg_pfad.exists()                        # alte Datei ist weg
    assert ziel == backup / "config_2026-09-23.yaml"
    assert ziel.exists()


def test_verschieben_kollidiert_nicht(tmp_path):
    backup = tmp_path / "Backup"
    backup.mkdir()
    (backup / "config_2026-09-23.yaml").write_text("belegt", encoding="utf-8")
    cfg_pfad = tmp_path / "config.yaml"
    cfg_pfad.write_text("shops: []", encoding="utf-8")

    ziel = m.verschiebe_config_nach_backup(cfg_pfad, backup, datum="2026-09-23")
    assert ziel.name != "config_2026-09-23.yaml"        # weicht aus
    assert (backup / "config_2026-09-23.yaml").read_text(encoding="utf-8") == "belegt"


def test_load_config_warnt_bei_koexistenz(tmp_path, monkeypatch, caplog):
    import logging
    ziel_e = tmp_path / "einstellungen.yaml"
    ziel_z = tmp_path / "zugang.yaml"
    m.schreibe_migration(beispiel_config(), ziel_einstellungen=ziel_e,
                         ziel_zugang=ziel_z)
    cfg_pfad = tmp_path / "config.yaml"           # alt liegt daneben
    cfg_pfad.write_text("shops: []", encoding="utf-8")
    monkeypatch.setattr(w, "EINSTELLUNGEN_PATH", ziel_e)
    monkeypatch.setattr(w, "ZUGANG_PATH", ziel_z)
    monkeypatch.setattr(w, "CONFIG_PATH", cfg_pfad)

    with caplog.at_level(logging.WARNING):
        cfg, quelle = w.load_config()

    assert "einstellungen.yaml" in quelle              # neue gelten
    assert "config.yaml" in caplog.text                # aber Warnung steht im Log
