"""Welle 5: Oberfläche (ui/index.html) gegen die echte EinstellungenApi.

pywebview selbst läuft hier nicht (kein Bildschirm). Stattdessen lädt
Chromium die Seite über einen kleinen HTTP-Server, und window.pywebview.api
wird per Init-Skript auf diesen Server umgeleitet — dahinter steht die echte
EinstellungenApi auf Testdateien. Geprüft wird also HTML + JS + Python
zusammen, nur die Brücke ist ersetzt.

Übersprungen, wenn Playwright oder Chromium fehlen — außer beim Build:
build.ps1 setzt WOO_CDH_UI_TESTS=pflicht, dann ist das ein Fehler
(Einrichten: pip install playwright; python -m playwright install chromium).
"""
import glob
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

import migrate_config as m
import oberflaeche as ob
import woo_to_cdh as w
from conftest import FIXTURES, FakeWoo

PFLICHT = os.environ.get("WOO_CDH_UI_TESTS") == "pflicht"
if PFLICHT:
    from playwright import sync_api
else:
    sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent


def _chromium() -> str | None:
    """Chromium dieser Umgebung (Cloud) oder das von Playwright installierte
    (Entwicklungsrechner: python -m playwright install chromium)."""
    pfade = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    if pfade:
        return pfade[-1]
    try:
        with sync_api.sync_playwright() as pw:
            pfad = pw.chromium.executable_path
    except Exception:  # noqa: BLE001
        return None
    return pfad if pfad and Path(pfad).exists() else None


CHROMIUM = _chromium()
if not CHROMIUM:
    if PFLICHT:
        raise RuntimeError("Chromium für die UI-Tests fehlt: "
                           "python -m playwright install chromium")
    pytest.skip("Chromium nicht vorhanden", allow_module_level=True)

PASSWORT = "richtig-geheim"
EINST = {
    "cdh_import_folder": "wex", "excel_export_folder": "excel",
    "veredelung_prefixes": ["004/", "316/", "006/", "234/"],
    "order_no_with_name": True, "status_after_export": "completed",
    "shops": [
        {"id": "caf", "name": "CAF-Shop", "enabled": True, "url": "https://shop.example/caf-shop/",
         "datev_no": 19541, "order_type": "AB"},
        {"id": "ensinger", "name": "Ensinger-Shop", "enabled": True,
         "url": "https://shop.example/ensinger-shop/", "datev_no": 14020, "order_type": "AB",
         "import_on_days": [1], "combine_by_delivery": True, "aggregate_all_positions": True},
        {"id": "agrar", "name": "Agrar-Shop", "enabled": True, "url": "https://shop.example/agrar/",
         "datev_no": 10698, "order_type": "AB", "combine_by_delivery": True},
    ],
}
BRIDGE = """
window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) =>
  fetch('/api/' + name, { method: 'POST', body: JSON.stringify(args) }).then((r) => r.json()) }) };
"""


class Handler(SimpleHTTPRequestHandler):
    api = None

    def log_message(self, *a):
        pass

    def do_POST(self):
        name = self.path.rsplit("/", 1)[-1]
        args = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or "[]")
        if name.startswith("_") or not hasattr(self.api, name):
            self.send_error(404)
            return
        body = json.dumps(getattr(self.api, name)(*args)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def ordner(tmp_path):
    einst = {**EINST, "cdh_import_folder": str(tmp_path / "wex"),
             "excel_export_folder": str(tmp_path / "excel")}
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump(einst, sort_keys=False), encoding="utf-8")
    (tmp_path / "lieferadressen.yaml").write_text(yaml.safe_dump({"Ensinger-Shop": {
        "Cham": {"name1": "Beispiel GmbH", "street": "Werkweg 1", "postcode": "93413",
                 "city": "Cham", "country": "DE"}}}), encoding="utf-8")
    (tmp_path / "zugang.yaml").write_text(yaml.safe_dump({
        "admin": {"password": m.hash_admin_password(PASSWORT, iterations=1000), "users": []},
        "shops": {"caf": {"consumer_key": "ck_TEST", "consumer_secret": "cs_TEST"},
                  "agrar": {"consumer_key": "ck_TEST", "consumer_secret": "cs_TEST"}}}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def cdh(monkeypatch):
    """Ersatz für CDH_WEX.EXE; mit warte=Event bleibt „das CDH-Fenster offen“."""
    zustand = {"aufrufe": [], "exit": 0, "warte": None}

    def start(path, cfg):
        zustand["aufrufe"].append(path.name)
        if zustand["warte"]:
            zustand["warte"].wait(10)
        w.CDH_LETZTER_EXIT = zustand["exit"]
        return True
    monkeypatch.setattr(w, "start_cdh_wex_import", start)
    return zustand


@pytest.fixture
def importseite(ordner, orders, cdh, monkeypatch):
    """Oberfläche auf dem Import-Tab (Standard beim Start)."""
    FakeWoo.orders_by_url = {"https://shop.example/caf-shop/": [orders["einzeln"]],
                             "https://shop.example/agrar/": orders["trenn"],
                             "https://shop.example/ensinger-shop/": orders["mitarbeitershop"]}
    FakeWoo.puts, FakeWoo.gets = [], []
    FakeWoo.zones_by_url = {"https://shop.example/caf-shop/": {0: [{"title": "Standardversand"}]}}
    monkeypatch.setattr(w, "EXPORTED_LOG_PATH", ordner / "exported.log")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", ordner / "lieferadressen.yaml")
    monkeypatch.setattr(w, "LOCK_PATH", ordner / "running.lock")
    api = ob.OberflaecheApi(ordner, benutzer="m.mueller", client_factory=FakeWoo,
                            oeffnen=lambda p: None)
    yield from _browser(api, ordner, cdh)
    api._abbruch.set()
    if cdh["warte"]:
        cdh["warte"].set()
    api._warten()


@pytest.fixture
def seite(importseite):
    """Oberfläche auf dem Einstellungs-Tab (Tests aus Welle 5)."""
    importseite.click("[data-a=tab][data-tab=settings]")
    importseite.wait_for_selector("text=Angemeldet als m.mueller")
    return importseite


def _browser(api, ordner, cdh):
    handler = type("H", (Handler,), {"api": api})
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(ROOT / "ui")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        fehler, extern = [], []
        page.on("pageerror", lambda e: fehler.append(str(e)))
        page.on("console", lambda msg: msg.type == "error" and fehler.append(msg.text))
        page.route("**/*", lambda route: (
            route.continue_() if route.request.url.startswith(f"http://127.0.0.1:{port}")
            else (extern.append(route.request.url), route.abort())))
        page.add_init_script(BRIDGE)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_function("typeof geladen !== 'undefined' && geladen")
        page.fehler, page.extern, page.ordner, page.cdh = fehler, extern, ordner, cdh
        page.api = api
        yield page
        browser.close()
    server.shutdown()


def _cfg(ordner):
    return yaml.safe_load((ordner / "einstellungen.yaml").read_text(encoding="utf-8"))


def _nav(page, sid):
    page.click(f"#list [data-a=nav][data-id='{sid}']")


def test_laedt_ohne_fehler_und_ohne_netz(seite):
    assert seite.locator("#list .row-title", has_text="CAF-Shop").count() == 1
    assert seite.locator("#detail h1").inner_text() == "CAF-Shop"
    # Inter kommt lokal aus ui/fonts/ und ist tatsächlich geladen
    assert seite.evaluate("document.fonts.ready.then(() => [...document.fonts]"
                          ".some(f => f.family.includes('Inter') && f.status === 'loaded'))")
    assert seite.evaluate("getComputedStyle(document.body).fontFamily").startswith("Inter")
    assert seite.extern == [] and seite.fehler == []


def test_kundenadresse_nur_hinweis_und_cdh_option(seite):
    _nav(seite, "ensinger")
    assert seite.locator("text=Aus dem CDH-Kundenstamm").is_visible()
    assert seite.locator("[data-a=unknown][data-v=cdh][aria-checked=true]").count() == 1
    assert seite.locator("[data-a=edit-sender]").count() == 0


def test_aendern_und_sichern(seite):
    _nav(seite, "caf")
    seite.click("label:has(input[data-c=alldays]) .switch")       # Bei jedem Lauf aus → Tag 1
    seite.click("[data-a=day][data-d='15']")
    assert seite.locator(".tab-badge").inner_text() == "1"
    seite.click("#detail [data-a=save]")
    seite.wait_for_selector("#hud.show:has-text('Gesichert: 1 Änderung')")
    assert _cfg(seite.ordner)["shops"][0]["import_on_days"] == [1, 15]
    assert seite.locator(".tab-badge").count() == 0
    _nav(seite, "__log")
    assert seite.locator("#detail .row-title").first.inner_text() == \
        "CAF: Import nur am 1., 15. des Monats"
    assert "m.mueller" in seite.locator("#detail .row-sub").first.inner_text()
    assert seite.fehler == []


def test_verwerfen(seite):
    _nav(seite, "caf")
    seite.click("[data-a=after][data-v='']")
    seite.click("#detail [data-a=discard]")
    assert seite.locator("[data-a=after][data-v=completed][aria-checked=true]").count() == 1
    assert seite.locator(".tab-badge").count() == 0


def test_debitornummer_mit_admin_passwort(seite):
    _nav(seite, "caf")
    seite.click("[data-a=unlock]")
    seite.fill("#pw", "falsch")
    seite.click("[data-a=pw-ok]")
    seite.wait_for_selector("#pw-err:has-text('Falsches Passwort')")
    seite.fill("#pw", PASSWORT)
    seite.click("[data-a=pw-ok]")
    seite.wait_for_selector("input[data-c=debitor]")
    seite.fill("input[data-c=debitor]", "19542")
    assert seite.locator("text=Admin-Modus").count() >= 1
    seite.click("#detail [data-a=save]")
    seite.wait_for_selector("#hud.show:has-text('Gesichert')")
    assert _cfg(seite.ordner)["shops"][0]["datev_no"] == 19542
    seite.click("[data-a=admin-end]")
    seite.wait_for_selector("[data-a=unlock]")


def test_debitor_ohne_admin_lehnt_python_ab(seite):
    """Manipulierte Oberfläche: Python lehnt trotzdem ab."""
    seite.evaluate("shops[0].debitor = '11111'; render()")
    seite.click("#detail [data-a=save]")
    seite.wait_for_selector(".alert-box:has-text('Admin-Modus nötig')")
    assert _cfg(seite.ordner)["shops"][0]["datev_no"] == 19541


def test_lieferadresse_anlegen(seite):
    _nav(seite, "ensinger")
    seite.click("#detail .row.action:has-text('Lieferort hinzufügen')")
    seite.wait_for_timeout(150)      # Formular setzt nach 60 ms den Fokus selbst
    for k, v in {"ort": "Garbsen", "name1": "Beispiel GmbH", "street": "Uniweg 2",
                 "plz": "30823", "city": "Garbsen"}.items():
        seite.fill(f"#ff-{k}", v)
    seite.click("[data-a=sheet-done]")
    seite.click("#detail [data-a=save]")
    seite.wait_for_selector("#hud.show:has-text('Gesichert')")
    adr = yaml.safe_load((seite.ordner / "lieferadressen.yaml").read_text(encoding="utf-8"))
    assert adr["Ensinger-Shop"]["Garbsen"]["postcode"] == "30823"
    assert adr["Ensinger-Shop"]["Cham"]["street"] == "Werkweg 1"


def test_veredelungen_brauchen_admin(seite):
    _nav(seite, "__global")
    assert seite.locator("#detail .row-value", has_text="006/").count() == 1
    seite.click("[data-a=add-prefix]")
    assert seite.locator("#pw").is_visible()


def test_versandarten_abgleich_und_zugang(seite):
    _nav(seite, "caf")
    seite.wait_for_selector("text=Versandart im Shop ohne feste Adresse")
    assert seite.locator(".tab-badge").count() == 0          # Versandarten sind keine Änderung
    _nav(seite, "ensinger")
    assert seite.locator("#detail .row-value .warn-text", has_text="Fehlt").count() == 1


def test_konflikt_mit_anderem_rechner(seite):
    _nav(seite, "caf")
    p = seite.ordner / "einstellungen.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\n# anderer Rechner\n", encoding="utf-8")
    seite.click("[data-a=after][data-v='']")
    seite.click("#detail [data-a=save]")
    seite.wait_for_selector(".alert-box:has-text('anderen Rechner')")


def test_import_ist_start_tab(importseite):
    assert importseite.locator("#importPane .big-btn", has_text="Abrufen").is_visible()
    assert importseite.fehler == []


# --- Welle 6: Import-Tab ------------------------------------------------------

def _abrufen(page):
    page.click("#importPane [data-a=fetch]")
    page.wait_for_selector("#importPane .subtitle:has-text('Abgerufen um')")


def _nur(page, titel_teil):
    """Auswahl auf die Einheit(en) beschränken, deren Zeile titel_teil enthält."""
    page.click("#importPane [data-a=select-all]")          # alle → keine
    if page.locator("#importPane .toolbar .mid").inner_text() != "Nichts ausgewählt":
        page.click("#importPane [data-a=select-all]")
    page.click(f"#importPane .row[role=checkbox]:has-text('{titel_teil}') [data-a=toggle]")


def test_abrufen_pruefansicht(importseite):
    p = importseite
    _abrufen(p)
    pane = p.locator("#importPane")
    assert pane.locator(".row-title", has_text="#402").count() == 1
    assert pane.locator(".row-title", has_text="Bondorf").count() == 1
    assert pane.locator(".row.indent").count() == 2                    # 3939, 3940
    assert "Zugangsdaten fehlen" in pane.inner_text()                   # Ensinger gesperrt
    assert pane.locator(".toolbar .mid").inner_text() == "3 Bestellungen, 2 Aufträge"
    assert p.fehler == [] and p.extern == []


def _ende(p):
    """Warten, bis der ganze Lauf fertig ist — nicht nur die Zeile: Eine
    Einheit steht schon auf „fertig“, während der Lauf noch Excel und Sperre
    abschließt. Erst dann gibt es „Fertig“ und das Blatt lässt sich schließen."""
    p.wait_for_selector("#overlay .nav-r [data-a=close]")


def test_bestelldetail_so_geht_es_an_cdh(importseite):
    p = importseite
    _abrufen(p)
    p.click("#importPane [data-a=order][data-no='402']")
    ov = p.locator("#overlay")
    ov.locator("text=So geht es an CDH").wait_for()
    text = ov.inner_text()
    assert "Kunde 19541" in text and "Anschrift aus dem CDH-Kundenstamm" in text
    assert "Versandadresse aus der Bestellung" in text and "Auftrag in CDH" in text
    p.click("#overlay [data-a=close] >> nth=-1")
    p.click("#importPane [data-a=order][data-no='3939']")
    text = p.locator("#overlay").inner_text()
    assert "Keine Lieferanschrift" in text and "Gemeinsamer Auftrag für Bondorf" in text
    assert "Veredelungen" in text                                       # Trennzeile im CDH-Auftrag


def test_import_mit_fortschritt(importseite):
    p = importseite
    p.cdh["warte"] = threading.Event()
    _abrufen(p)
    _nur(p, "#402")
    assert p.locator("#importPane .toolbar .mid").inner_text() == "1 Bestellung, 1 Auftrag"
    p.click("#importPane [data-a=import]")
    p.wait_for_selector("#overlay .row[data-status=laeuft]:has-text('Im CDH-Fenster auf „Ende“ klicken')")
    assert p.locator("#overlay [data-a=cancel-import]").is_enabled()
    assert p.evaluate("1 + 1") == 2                                      # Seite reagiert
    assert (p.ordner / "running.lock").exists()                          # Sperre während des Laufs
    p.cdh["warte"].set()
    p.wait_for_selector("#overlay .row[data-status=fertig]")
    _ende(p)
    assert "1 Bestellung in 1 Auftrag verarbeitet" in p.locator("#overlay").inner_text()
    p.click("#overlay [data-a=close] >> nth=-1")
    assert p.locator("#importPane .row-title", has_text="#402").count() == 0
    assert "1402|402" in (p.ordner / "exported.log").read_text(encoding="utf-8").replace("\t", "|")
    p.wait_for_selector("#importPane .row-title:has-text('-402.wex')")      # Letzte WEX-Dateien
    assert not (p.ordner / "running.lock").exists()
    assert p.fehler == []


def test_import_abbrechen(importseite):
    p = importseite
    p.cdh["warte"] = threading.Event()
    _abrufen(p)
    p.click("#importPane [data-a=import]")                               # beide Aufträge
    p.wait_for_selector("#overlay .row[data-status=laeuft]")
    p.click("#overlay [data-a=cancel-import]")
    p.wait_for_selector("#overlay [data-a=cancel-import]:has-text('Wird beendet')")
    p.cdh["warte"].set()
    p.wait_for_selector("#overlay .row[data-status=abgebrochen]")
    _ende(p)
    assert "Abgebrochen nach 1 von 2" in p.locator("#overlay").inner_text()
    assert len(p.cdh["aufrufe"]) == 1
    p.click("#overlay [data-a=close] >> nth=-1")
    assert p.locator("#importPane .toolbar .mid").inner_text().endswith("1 Auftrag")


def test_exit_code_bitte_pruefen(importseite):
    p = importseite
    p.cdh["exit"] = 3
    _abrufen(p)
    _nur(p, "#402")
    p.click("#importPane [data-a=import]")
    p.wait_for_selector("#overlay .row[data-status=pruefen]:has-text('CDH meldet Exit 3')")
    _ende(p)
    assert "braucht einen Blick" in p.locator("#overlay").inner_text()


def test_stichtag_trotzdem(importseite):
    p = importseite
    pe = p.ordner / "einstellungen.yaml"
    cfg = yaml.safe_load(pe.read_text(encoding="utf-8"))
    from datetime import datetime
    cfg["shops"][2]["import_on_days"] = [datetime.now().day % 28 + 1]
    pe.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    _abrufen(p)
    assert p.locator("#importPane .row-title", has_text="Heute nicht dran").count() == 1
    p.click("#importPane [data-a=override][data-id=agrar]")
    p.wait_for_selector("#importPane .row-title:has-text('Bondorf')")


def test_excel_uebersicht(importseite):
    p = importseite
    _abrufen(p)
    p.click("#importPane [data-a=excel-menu]")
    p.click("#overlay [data-a=excel-make][data-scope=__all]")
    p.wait_for_selector("#overlay :text('Excel erstellt')")
    text = p.locator("#overlay").inner_text()
    assert "CAF-Shop" in text and "Agrar-Shop" in text and "3 Bestellungen" in text
    assert list((p.ordner / "excel" / "uebersicht").glob("Uebersicht-alle-Shops-*.xlsx"))
    assert not (p.ordner / "exported.log").exists()


def test_erneut_an_cdh(importseite):
    p = importseite
    _abrufen(p)
    _nur(p, "#402")
    p.click("#importPane [data-a=import]")
    p.wait_for_selector("#overlay .row[data-status=fertig]")
    _ende(p)
    p.click("#overlay [data-a=close] >> nth=-1")
    p.wait_for_selector("#importPane [data-a=erneut]")
    p.click("#importPane [data-a=erneut] >> nth=0")
    p.wait_for_selector("#overlay .alert-box:has-text('Nur, wenn der Auftrag in CDH fehlt')")
    p.click("#overlay [data-a=erneut-ok]")
    p.wait_for_selector("#overlay .row[data-status=fertig]")
    _ende(p)
    assert len(p.cdh["aufrufe"]) == 2 and p.cdh["aufrufe"][0] == p.cdh["aufrufe"][1]
    zeilen = (p.ordner / "exported.log").read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 2                                  # Kopf + 1 — nichts doppelt vermerkt


def test_sperre_anderer_rechner(importseite):
    p = importseite
    (p.ordner / "running.lock").write_text(json.dumps(
        {"rechner": "PC-LAGER", "benutzer": "m.mueller", "seit": "2026-10-01T09:14:00"}), encoding="utf-8")
    _abrufen(p)
    assert "Import läuft an PC-LAGER (m.mueller) seit 09:14" in p.locator("#importPane").inner_text()
    assert p.locator("#importPane [data-a=import]").is_disabled()


def test_hinweis_ungesicherte_einstellungen(importseite):
    p = importseite
    p.click("[data-a=tab][data-tab=settings]")
    _nav(p, "caf")
    p.click("[data-a=after][data-v='']")
    p.click("[data-a=tab][data-tab=import]")
    assert "Der Import arbeitet mit dem gesicherten Stand" in p.locator("#importPane").inner_text()


# --- Welle 7: Shop anlegen, Zugang erneuern -----------------------------------

KEY = "ck_" + "Q" * 24            # zur Laufzeit gebaut (Schlüssel-Scanner)
SECRET = "cs_" + "W" * 24
FALSCH = "ck_" + "Z" * 24
NEU_URL = "https://shop.example/musterbau/"


class Woo401(FakeWoo):
    def _get(self, path, params=None):
        if self.consumer_key == FALSCH:
            import requests
            r = requests.Response()
            r.status_code = 401
            raise requests.HTTPError("401 Client Error", response=r)
        return super()._get(path, params)


def _admin(page):
    page.fill("#pw", PASSWORT)
    page.click("[data-a=pw-ok]")


def _alt(oid, nr):
    return {"id": oid, "number": str(nr), "status": "processing",
            "date_created": "2026-09-01T10:00:00", "line_items": [], "meta_data": []}


def _assistent_bis_pruefen(seite, key=KEY):
    seite.click("[data-a=wizard]")
    _admin(seite)
    seite.wait_for_selector("#wz-name")
    seite.wait_for_timeout(150)
    seite.fill("#wz-name", "Musterbau")
    seite.fill("#wz-url", NEU_URL)
    seite.fill("#wz-debitor", "12345")
    seite.click("[data-a=wiz-next]")
    seite.wait_for_selector("#wz-key")
    seite.wait_for_timeout(150)
    seite.fill("#wz-key", key)
    seite.fill("#wz-secret", SECRET)
    seite.click("[data-a=wiz-next]")


def test_shop_assistent_komplett(seite):
    FakeWoo.orders_by_url[NEU_URL] = [_alt(501, 1001), _alt(502, 1002)]
    seite.api._client_factory = Woo401
    _assistent_bis_pruefen(seite)
    seite.wait_for_selector("text=Der Shop kann angebunden werden.")
    assert seite.locator("#wiz-checks .row").count() == 5
    assert FakeWoo.puts == []
    seite.click("[data-a=wiz-next]")
    seite.wait_for_selector("text=Im Shop liegen schon 2 offene Bestellungen.")
    assert seite.locator("#wiz-nummern").inner_text() == "1001, 1002"
    seite.click("[data-a=wiz-finish]")                       # ohne Bestätigung
    seite.wait_for_selector("#hud.show:has-text('bestätigen')")
    assert "musterbau" not in yaml.safe_dump(_cfg(seite.ordner))
    seite.click("[data-a=wiz-confirm]")
    assert "2 abschließen" in seite.locator("[data-a=wiz-confirm]").inner_text()
    seite.click("[data-a=wiz-finish]")
    seite.wait_for_selector("text=Musterbau ist angelegt")
    assert seite.locator("#wiz-alt").inner_text() == "2 abgeschlossen"
    assert FakeWoo.puts == [("/orders/501", {"status": "completed"}),
                            ("/orders/502", {"status": "completed"})]
    neu = _cfg(seite.ordner)["shops"][-1]
    assert neu["id"] == "musterbau" and neu["enabled"] is False and neu["datev_no"] == 12345
    zugang = yaml.safe_load((seite.ordner / "zugang.yaml").read_text(encoding="utf-8"))
    assert zugang["shops"]["musterbau"]["consumer_key"] == KEY
    # Schlüssel sind aus der Seite verschwunden
    assert seite.evaluate("JSON.stringify(overlay)").count(KEY) == 0
    assert KEY not in seite.content()
    seite.click("[data-a=wiz-open]")
    seite.wait_for_selector("#detail h1:has-text('Musterbau')")
    assert seite.locator("#detail .row-value", has_text="Hinterlegt").count() == 1
    _nav(seite, "__log")
    titel = seite.locator("#detail .row-title").all_inner_texts()
    assert titel[:2] == ["Musterbau: 2 Altbestellungen abgeschlossen (Nr. 1001, 1002)",
                         "Musterbau: Shop angelegt (Debitor 12345), ausgeschaltet"]
    assert seite.fehler == []


def test_shop_assistent_mitnehmen(seite):
    FakeWoo.orders_by_url[NEU_URL] = [_alt(501, 1001)]
    _assistent_bis_pruefen(seite)
    seite.wait_for_selector("text=Der Shop kann angebunden werden.")
    seite.click("[data-a=wiz-next]")
    seite.click("[data-a=wiz-old][data-v=keep]")
    assert seite.locator("#wiz-nummern").count() == 0
    seite.click("[data-a=wiz-finish]")
    seite.wait_for_selector("text=Musterbau ist angelegt")
    assert seite.locator("#wiz-alt").inner_text() == "1 beim ersten Import"
    assert FakeWoo.puts == []


def test_shop_assistent_falscher_schluessel(seite):
    seite.api._client_factory = Woo401
    _assistent_bis_pruefen(seite, key=FALSCH)
    seite.wait_for_selector("text=Da passt etwas noch nicht.")
    assert seite.locator("#wiz-checks .row[data-stufe=fehler]").count() == 1
    assert seite.locator("[data-a=wiz-next]").count() == 0
    seite.click("[data-a=wiz-back]")
    seite.wait_for_selector("#wz-key")
    assert len(_cfg(seite.ordner)["shops"]) == 3


def test_shop_assistent_debitor_doppelt(seite):
    seite.click("[data-a=wizard]")
    _admin(seite)
    seite.wait_for_selector("#wz-name")
    seite.wait_for_timeout(150)
    seite.fill("#wz-name", "Musterbau")
    seite.fill("#wz-url", NEU_URL)
    seite.fill("#wz-debitor", "19541")
    seite.click("[data-a=wiz-next]")
    seite.wait_for_selector("#hud.show:has-text('gehört schon zu CAF-Shop')")


def test_shop_assistent_nicht_mit_ungesicherten_aenderungen(seite):
    _nav(seite, "caf")
    seite.click("[data-a=after][data-v='']")
    seite.click("[data-a=wizard]")
    _admin(seite)
    seite.wait_for_selector(".alert-box:has-text('ungesicherte Änderungen')")
    assert seite.locator("#wz-name").count() == 0


def test_zugang_erneuern(seite):
    seite.api._client_factory = Woo401
    _nav(seite, "ensinger")
    seite.click("[data-a=after][data-v='']")                 # ungesicherte Änderung bleibt
    seite.click("#detail [data-a=access]")
    _admin(seite)
    seite.wait_for_selector("#ac-key")
    seite.wait_for_timeout(150)
    seite.fill("#ac-key", FALSCH)
    seite.fill("#ac-secret", SECRET)
    seite.click("[data-a=access-save]")
    seite.wait_for_selector(".alert-box:has-text('Zugang nicht geändert')")
    assert "ensinger" not in yaml.safe_load(
        (seite.ordner / "zugang.yaml").read_text(encoding="utf-8"))["shops"]
    seite.click(".alert-box [data-a=close]")
    seite.click("#detail [data-a=access]")
    seite.wait_for_selector("#ac-key")
    seite.wait_for_timeout(150)
    seite.fill("#ac-key", KEY)
    seite.fill("#ac-secret", SECRET)
    seite.click("[data-a=access-save]")
    seite.wait_for_selector(".alert-box:has-text('alten Schlüssel jetzt im Shop widerrufen')")
    z = yaml.safe_load((seite.ordner / "zugang.yaml").read_text(encoding="utf-8"))["shops"]
    assert z["ensinger"] == {"consumer_key": KEY, "consumer_secret": SECRET}
    seite.click(".alert-box [data-a=close]")
    assert seite.locator("#detail .row-value", has_text="Hinterlegt").count() == 1
    assert seite.locator(".tab-badge").inner_text() == "1"
    assert KEY not in seite.content()
    assert seite.fehler == []



def test_seite_meldet_ungesicherte_aenderungen(seite):
    """Für die Rückfrage beim Schließen (ohne das Fenster abzufragen)."""
    _nav(seite, "caf")
    seite.click("[data-a=after][data-v='']")
    seite.wait_for_function("gemeldet === 1")
    for _ in range(20):
        if seite.api._ungesichert == 1:
            break
        seite.wait_for_timeout(50)
    assert seite.api._ungesichert == 1
    seite.click("#detail [data-a=discard]")
    seite.wait_for_function("gemeldet === 0")
    for _ in range(20):
        if seite.api._ungesichert == 0:
            break
        seite.wait_for_timeout(50)
    assert seite.api._ungesichert == 0


def test_pflicht_zubehoer_schalter(seite):
    _nav(seite, "caf")
    seite.wait_for_selector("text=Zubehör aus den Artikelregeln ergänzen")
    seite.click("label:has(input[data-c=zubehoer]) .switch")
    seite.wait_for_selector("text=Der Import legt das Pflicht-Zubehör selbst an")
    seite.click("#detail [data-a=save]")
    seite.wait_for_selector("#hud.show:has-text('Gesichert')")
    assert _cfg(seite.ordner)["shops"][0]["pflicht_zubehoer"] is True
    assert seite.fehler == []
