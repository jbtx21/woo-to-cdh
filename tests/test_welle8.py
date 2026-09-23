"""Welle 8: Programmstand, Selbsttest der EXE, build.ps1.

PowerShell läuft hier nicht; build.ps1 wird deshalb nur auf die Regeln aus
dem Briefing geprüft (Tests Pflicht, nichts Geheimes kopieren, Deploy nur
mit Freigabe). Der echte Build mit Selbsttest ist in der Welle-8-Notiz des
Briefings beschrieben.
"""
import sys
import types
from pathlib import Path

import pytest

import launcher
import oberflaeche as ob
import woo_to_cdh as w

ROOT = Path(__file__).resolve().parent.parent
BUILD = (ROOT / "build.ps1").read_text(encoding="utf-8")


# --- Programmstand -------------------------------------------------------------

def test_programmstand_ohne_build(monkeypatch):
    monkeypatch.setitem(sys.modules, "build_info", None)     # Import schlägt fehl
    assert w.programmstand() == "Entwicklung (nicht gebaut)"


def test_programmstand_aus_build(monkeypatch):
    monkeypatch.setitem(sys.modules, "build_info",
                        types.SimpleNamespace(STAND="2026-10-05 08:00 main@abc1234"))
    assert w.programmstand() == "2026-10-05 08:00 main@abc1234"


def test_build_info_nie_im_repo():
    zeilen = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "build_info.py" in zeilen and "selbsttest.txt" in zeilen


def test_programmstand_in_der_oberflaeche(tmp_path, monkeypatch):
    import einstellungen_api as ea
    (tmp_path / "einstellungen.yaml").write_text("shops: []\n", encoding="utf-8")
    monkeypatch.setattr(w, "programmstand", lambda: "STAND-X")
    assert ea.EinstellungenApi(tmp_path, benutzer="t").laden()["stand"] == "STAND-X"


# --- Selbsttest ------------------------------------------------------------------

def test_launcher_selbsttest_startet_keinen_import(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["WOO_to_CDH.exe", "--selbsttest"])
    monkeypatch.setattr(w, "main", lambda: pytest.fail("Import gestartet"))
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("wartet auf Enter"))
    assert launcher.main() == 0
    assert "Selbsttest ok" in capsys.readouterr().out


def test_oberflaeche_selbsttest_ok(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setattr(w, "BASE_DIR", tmp_path)
    assert ob.main(["--selbsttest"]) == 0
    assert (tmp_path / "selbsttest.txt").read_text(encoding="utf-8").startswith("ok")


def test_oberflaeche_selbsttest_merkt_fehlende_ui(tmp_path, monkeypatch):
    """So sähe eine EXE aus, bei der --add-data ui vergessen wurde."""
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))
    monkeypatch.setattr(w, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ob, "RESSOURCEN", tmp_path)
    monkeypatch.setattr(ob, "UI_DATEI", tmp_path / "ui" / "index.html")
    assert ob.main(["--selbsttest"]) == 1
    text = (tmp_path / "selbsttest.txt").read_text(encoding="utf-8")
    assert "index.html fehlt" in text and "fonts fehlt" in text


def test_oberflaeche_selbsttest_ohne_pywebview(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)
    monkeypatch.setattr(w, "BASE_DIR", tmp_path)
    assert ob.main(["--selbsttest"]) == 1
    assert "pywebview fehlt" in (tmp_path / "selbsttest.txt").read_text(encoding="utf-8")


# --- build.ps1 -------------------------------------------------------------------

def test_build_tests_sind_pflicht():
    tests = BUILD.index("python -m pytest")
    assert BUILD.index('WOO_CDH_UI_TESTS = "pflicht"') < tests < BUILD.index("PyInstaller")
    assert 'throw "Tests rot' in BUILD


def test_build_drei_exe_mit_ui_und_selbsttest():
    for name in ("WOO_to_CDH", "WOO_to_CDH_Oberflaeche", "Lieferadressen"):
        assert f"--name {name}" in BUILD
    assert '--add-data "ui;ui"' in BUILD
    assert BUILD.count("--selbsttest") == 2


def test_build_kopiert_nichts_geheimes():
    kopieren = [z for z in BUILD.splitlines() if "Copy-Item" in z]
    for geheim in ("config.yaml", "zugang.yaml", "einstellungen.yaml",
                   "lieferadressen.yaml", "exported.log", ".py"):
        assert not any(geheim in z for z in kopieren), geheim


def test_build_deploy_nur_mit_freigabe():
    deploy = BUILD[BUILD.index("if ($Deploy) {"):]
    assert '$Branch -ne "main"' in BUILD and "$Produktionsstopp" in BUILD
    assert 'Read-Host' in deploy and '-cne "JA"' in deploy
    assert deploy.index("Backup") < deploy.index("Copy-Item -Force (Join-Path dist")


def test_ui_tests_finden_chromium_auch_unter_windows():
    text = (ROOT / "tests" / "test_oberflaeche.py").read_text(encoding="utf-8")
    assert "pw.chromium.executable_path" in text and "WOO_CDH_UI_TESTS" in text
