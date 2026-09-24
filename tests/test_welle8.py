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
    assert '$Branch -ne "main"' in BUILD      # Produktionsstopp am 24.09. aufgehoben
    assert 'Read-Host' in deploy and '-cne "JA"' in deploy
    assert deploy.index("Backup") < deploy.index("Copy-Item -Force (Join-Path dist")
    # Oberflächen-Ordner zuerst beiseite: läuft sie noch, wird nichts ersetzt
    assert deploy.index("Move-Item $Daten") < deploy.index("Copy-Item -Force (Join-Path dist")


def test_build_oberflaeche_als_ordner_exe():
    """Ein-Datei-EXE brauchte 26,8 s zum Entpacken von V: (24.09.2026)."""
    zeile = BUILD[BUILD.index("--name WOO_to_CDH_Oberflaeche"):].splitlines()[0]
    vorher = BUILD[:BUILD.index("--name WOO_to_CDH_Oberflaeche")].splitlines()[-1]
    assert "--onedir" in vorher + zeile
    assert "--contents-directory $GuiDaten" in BUILD
    assert 'Copy-Item -Recurse -Force "$GuiOrdner\\$GuiDaten" $Target' in BUILD


def test_ui_tests_finden_chromium_auch_unter_windows():
    text = (ROOT / "tests" / "test_oberflaeche.py").read_text(encoding="utf-8")
    assert "pw.chromium.executable_path" in text and "WOO_CDH_UI_TESTS" in text


# --- Tempo (Rückmeldung 24.09.2026: Oberfläche langsam) ------------------------

def test_langsame_aufrufe_landen_im_log_ohne_argumente(caplog, monkeypatch):
    import inspect
    import logging
    import time as zeit
    import shop_api
    caplog.set_level(logging.INFO)
    uhr = iter([0.0, 2.5, 10.0, 10.1])
    monkeypatch.setattr(zeit, "perf_counter", lambda: next(uhr))
    geheim = "ck_" + "Q" * 20

    @ob._mit_zeitmessung
    class Api:
        def zugang_erneuern(self, shop_id, key, secret):
            return {"ok": True}

        def schnell(self):
            return 1

    a = Api()
    assert a.zugang_erneuern("caf", geheim, "cs_x") == {"ok": True}
    assert a.schnell() == 1
    assert "Oberfläche: zugang_erneuern dauerte 2.5 s" in caplog.text
    assert "schnell" not in caplog.text and geheim not in caplog.text
    assert list(inspect.signature(Api.zugang_erneuern).parameters) == \
        ["self", "shop_id", "key", "secret"]
    assert shop_api  # Modul geladen


def test_oberflaeche_api_behaelt_signaturen():
    import inspect
    sig = inspect.signature(ob.OberflaecheApi.zugang_erneuern)
    assert list(sig.parameters) == ["self", "shop_id", "key", "secret"]
    assert not hasattr(ob.OberflaecheApi._init_import, "__wrapped__")   # intern unberührt


def test_letzte_wex_neueste_zuerst(tmp_path, monkeypatch):
    import os
    import yaml
    import import_api as ia
    wex = tmp_path / "wex"
    wex.mkdir()
    for i in range(30):
        f = wex / f"orders-{i:02d}.wex"
        f.write_text("x", encoding="utf-8")
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))
    (wex / "notiz.txt").write_text("x", encoding="utf-8")
    (wex / "unterordner.wex").mkdir()
    (tmp_path / "einstellungen.yaml").write_text(
        yaml.safe_dump({"cdh_import_folder": str(wex), "shops": []}), encoding="utf-8")
    (tmp_path / "zugang.yaml").write_text("shops: {}\n", encoding="utf-8")
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    api = ia.ImportApi(tmp_path)
    erg = api.letzte_wex(3)
    assert [d["datei"] for d in erg["dateien"]] == ["orders-29.wex", "orders-28.wex", "orders-27.wex"]


def test_abrufen_parallel_in_konfig_reihenfolge(monkeypatch, tmp_path):
    """Sieben Shops dauerten nacheinander 21,5 s; jetzt gleichzeitig, die
    Ergebnisse bleiben in der Reihenfolge der Konfiguration."""
    import threading
    import time as zeit
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", tmp_path / "fehlt.yaml")
    gleichzeitig, aktiv, sperre = [0], [0], threading.Lock()

    def langsam(shop_cfg, *a, **k):
        with sperre:
            aktiv[0] += 1
            gleichzeitig[0] = max(gleichzeitig[0], aktiv[0])
        zeit.sleep(0.2)
        with sperre:
            aktiv[0] -= 1
        return w.ShopErgebnis(shop=shop_cfg["name"], shop_cfg=shop_cfg, global_cfg={})
    monkeypatch.setattr(w, "_shop_abrufen", langsam)
    shops = [{"name": f"S{i}", "url": f"https://shop.example/{i}/", "consumer_key": "ck_T",
              "consumer_secret": "cs_T"} for i in range(6)]
    shops.insert(2, {"name": "Aus", "url": "https://shop.example/aus/", "enabled": False})
    t0 = zeit.perf_counter()
    erg = w.abrufen({"shops": shops})
    assert [s.shop for s in erg.shops] == [f"S{i}" for i in range(6)]
    assert gleichzeitig[0] > 1 and zeit.perf_counter() - t0 < 1.0


def test_client_nutzt_eine_verbindung(monkeypatch):
    aufrufe = []

    class Antwort:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    def get(self, *a, **k):
        aufrufe.append(id(self))
        return Antwort()
    monkeypatch.setattr(w.requests.Session, "get", get)
    c = w.WooClient("https://shop.example/x/", "ck_T", "cs_T")
    c._get("/orders")
    c._get("/products/1")
    assert len(aufrufe) == 2 and len(set(aufrufe)) == 1


def test_versandarten_werden_gemerkt(monkeypatch):
    aufrufe = []

    class C(w.WooClient):
        def _get(self, path, params=None):
            aufrufe.append(path)
            return [{"id": 1}] if path == "/shipping/zones" else [{"title": "Cham"}]
    uhr = [1000.0]
    monkeypatch.setattr(w.time, "monotonic", lambda: uhr[0])
    c = C("https://shop.example/x/", "ck_T", "cs_T")
    assert c.get_shipping_methods() == ["Cham"]
    assert C("https://shop.example/x/", "ck_T", "cs_T").get_shipping_methods() == ["Cham"]
    assert len(aufrufe) == 2                                  # zweites Mal aus dem Speicher
    uhr[0] += w.VERSANDARTEN_MERKEN_SEKUNDEN + 1
    c.get_shipping_methods()
    assert len(aufrufe) == 4                                  # nach 30 min neu


def test_zonen_und_bestellungen_gleichzeitig(monkeypatch, tmp_path):
    import threading
    import time as zeit
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", tmp_path / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", tmp_path / "fehlt.yaml")
    laufend, gleichzeitig, sperre = set(), [False], threading.Lock()

    class C(w.WooClient):
        def _get(self, path, params=None):
            art = "zonen" if path.startswith("/shipping") else "orders"
            with sperre:
                laufend.add(art)
                if len(laufend) == 2:
                    gleichzeitig[0] = True
            zeit.sleep(0.15)
            with sperre:
                laufend.discard(art)
            return [] if art == "orders" else ([{"id": 1}] if path == "/shipping/zones" else [])
    cfg = {"shops": [{"name": "Sammel", "url": "https://shop.example/s/", "consumer_key": "ck_T",
                      "consumer_secret": "cs_T", "combine_by_delivery": True}]}
    erg = w.abrufen(cfg, client_factory=C)
    assert gleichzeitig[0]
    assert erg.shops[0].warnungen == ["Keine aktive Versandart im Shop gefunden — Versandzonen prüfen."]
