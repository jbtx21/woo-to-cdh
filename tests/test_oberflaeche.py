"""Welle 5: Oberfläche (ui/index.html) gegen die echte EinstellungenApi.

pywebview selbst läuft hier nicht (kein Bildschirm). Stattdessen lädt
Chromium die Seite über einen kleinen HTTP-Server, und window.pywebview.api
wird per Init-Skript auf diesen Server umgeleitet — dahinter steht die echte
EinstellungenApi auf Testdateien. Geprüft wird also HTML + JS + Python
zusammen, nur die Brücke ist ersetzt.

Übersprungen, wenn Playwright oder Chromium fehlen.
"""
import glob
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

import einstellungen_api as ea
import migrate_config as m

sync_api = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
CHROMIUM = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
if not CHROMIUM:
    pytest.skip("Chromium nicht vorhanden", allow_module_level=True)

PASSWORT = "richtig-geheim"
EINST = {
    "veredelung_prefixes": ["004/", "316/", "006/", "234/"],
    "order_no_with_name": True, "status_after_export": "completed",
    "shops": [
        {"id": "caf", "name": "CAF-Shop", "enabled": True, "url": "https://shop.example/caf-shop/",
         "datev_no": 19541, "order_type": "AB"},
        {"id": "ensinger", "name": "Ensinger-Shop", "enabled": True,
         "url": "https://shop.example/ensinger-shop/", "datev_no": 14020, "order_type": "AB",
         "import_on_days": [1], "combine_by_delivery": True, "aggregate_all_positions": True},
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
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump(EINST, sort_keys=False), encoding="utf-8")
    (tmp_path / "lieferadressen.yaml").write_text(yaml.safe_dump({"Ensinger-Shop": {
        "Cham": {"name1": "Beispiel GmbH", "street": "Werkweg 1", "postcode": "93413",
                 "city": "Cham", "country": "DE"}}}), encoding="utf-8")
    (tmp_path / "zugang.yaml").write_text(yaml.safe_dump({
        "admin": {"password": m.hash_admin_password(PASSWORT, iterations=1000), "users": []},
        "shops": {"caf": {"consumer_key": "ck_TEST", "consumer_secret": "cs_TEST"}}}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def seite(ordner):
    api = ea.EinstellungenApi(ordner, benutzer="m.mueller")
    api._client_factory = lambda *a, **k: type("C", (), {
        "get_shipping_methods": lambda self: ["Standardversand"]})()
    handler = type("H", (Handler,), {"api": api})
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(ROOT / "ui")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROMIUM[-1])
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        fehler, extern = [], []
        page.on("pageerror", lambda e: fehler.append(str(e)))
        page.on("console", lambda msg: msg.type == "error" and fehler.append(msg.text))
        page.route("**/*", lambda route: (
            route.continue_() if route.request.url.startswith(f"http://127.0.0.1:{port}")
            else (extern.append(route.request.url), route.abort())))
        page.add_init_script(BRIDGE)
        page.goto(f"http://127.0.0.1:{port}/index.html")
        page.wait_for_selector("text=Angemeldet als m.mueller")
        page.fehler, page.extern, page.ordner = fehler, extern, ordner
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


def test_import_tab_platzhalter(seite):
    seite.click("[data-a=tab][data-tab=import]")
    assert seite.locator("#importPane").inner_text().count("WOO_to_CDH.exe") == 1
    assert seite.fehler == []
