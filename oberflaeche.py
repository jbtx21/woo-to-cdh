"""
TEXMA — WooCommerce → CDH: Oberfläche (Welle 5)
================================================

Öffnet ein Fenster mit der Oberfläche aus ui/index.html (pywebview, unter
Windows über WebView2). Die Oberfläche spricht über window.pywebview.api mit
einstellungen_api.EinstellungenApi — dort liegt die ganze Logik.

Stand Welle 6: Tabs „Import“ (import_api.ImportApi) und „Einstellungen“
(einstellungen_api.EinstellungenApi), seit Welle 7 mit Shop-Assistent und
Zugang erneuern (shop_api.ShopApi). Die Konsolen-EXE bleibt parallel
einsatzbereit und nutzt dieselben Funktionen.

Start (Entwicklung):  python oberflaeche.py
Konfiguration:        einstellungen.yaml, zugang.yaml, lieferadressen.yaml
                      im Programmordner (wie bei WOO_to_CDH.exe)
"""

from __future__ import annotations

import functools
import inspect
import logging
import os
import sys
import time
from pathlib import Path

import woo_to_cdh as w
from import_api import ImportApi
from shop_api import ShopApi

# Mit PyInstaller liegen mitgelieferte Dateien (ui/) im Entpack-Ordner,
# die Konfiguration dagegen neben der EXE (w.BASE_DIR).
RESSOURCEN = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DATEI = RESSOURCEN / "ui" / "index.html"


# WebView2-Laufzeit (Evergreen). Auf Windows 11 immer da, auf Windows 10 nur,
# wenn Edge sie mitgebracht hat oder sie installiert wurde. Ohne sie würde
# pywebview auf die alte IE-Engine ausweichen, in der die Seite nicht läuft.
WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_SCHLUESSEL = [
    ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
    ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
    ("HKEY_CURRENT_USER", rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_GUID}"),
]
WEBVIEW2_HINWEIS = (
    "Für die Oberfläche fehlt auf diesem Rechner die „Microsoft Edge WebView2 "
    "Runtime“ (typisch bei Windows 10).\n\n"
    "Installieren: https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
    "Der Import über WOO_to_CDH.exe funktioniert weiterhin.")


def webview2_version(lies_registry=None) -> str | None:
    """Installierte WebView2-Version oder None. lies_registry(wurzel, pfad)
    liefert den Wert "pv" oder wirft OSError (Tests ersetzen ihn)."""
    if lies_registry is None:
        if sys.platform != "win32":
            return None
        import winreg

        def lies_registry(wurzel, pfad):
            with winreg.OpenKey(getattr(winreg, wurzel), pfad) as k:
                return winreg.QueryValueEx(k, "pv")[0]
    for wurzel, pfad in WEBVIEW2_SCHLUESSEL:
        try:
            v = lies_registry(wurzel, pfad)
        except OSError:
            continue
        if v and v != "0.0.0.0":
            return str(v)
    return None


def _meldung(titel: str, text: str) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, titel, 0x30)
    else:
        print(f"{titel}: {text}", file=sys.stderr)


def _startdauer() -> float | None:
    """Sekunden seit dem Doppelklick, einschließlich Entpacken der EXE.
    Bei einer PyInstaller-onefile-EXE entpackt ein Elternprozess und startet
    dann Python; gemessen wird ab dessen Start. Nur Windows, sonst None."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, os.getppid())   # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        zeiten = [wintypes.FILETIME() for _ in range(4)]
        try:
            if not k32.GetProcessTimes(h, *[ctypes.byref(z) for z in zeiten]):
                return None
        finally:
            k32.CloseHandle(h)
        ft = (zeiten[0].dwHighDateTime << 32) | zeiten[0].dwLowDateTime
        return time.time() - (ft / 1e7 - 11644473600)
    except Exception:  # noqa: BLE001 — nur Messung
        return None


def _frage(titel: str, text: str) -> bool:
    """Ja/Nein-Frage als Windows-Meldungsfenster. Bewusst nicht über
    pywebview: Beim Schließen läuft der Aufruf im Fenster-Thread, und
    evaluate_js/create_confirmation_dialog warten dort auf sich selbst —
    das Fenster hing mit „Keine Rückmeldung“ (24.09.2026)."""
    if sys.platform != "win32":
        return True
    import ctypes
    MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, IDYES = 0x4, 0x30, 0x100, 6
    return ctypes.windll.user32.MessageBoxW(
        None, text, titel, MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2) == IDYES


def beim_schliessen(api, frage=_frage) -> bool:
    """Fragt nach, wenn noch ungesicherte Änderungen offen sind. Die Zahl
    meldet die Seite laufend selbst (ungesichert_melden), hier wird nichts
    im Fenster abgefragt. False bricht das Schließen ab (pywebview-Regel)."""
    n = api._ungesichert
    if not n:
        return True
    return frage("Ungesicherte Änderungen",
                 f"{n} {'Änderung ist' if n == 1 else 'Änderungen sind'} noch nicht "
                 "gesichert und gehen verloren. Trotzdem schließen?")


LANGSAM_SEKUNDEN = 0.5


def _mit_zeitmessung(cls):
    """Jede Funktion, die das Fenster aufruft, schreibt ins Log, wenn sie
    länger als LANGSAM_SEKUNDEN braucht — nur Name und Dauer, nie die
    Argumente (darin können Schlüssel stecken)."""
    for name in dir(cls):
        f = getattr(cls, name)
        if name.startswith("_") or not callable(f):
            continue

        def huelle(self, *a, _f=f, _name=name, **kw):
            t0 = time.perf_counter()
            try:
                return _f(self, *a, **kw)
            finally:
                dauer = time.perf_counter() - t0
                if dauer >= LANGSAM_SEKUNDEN:
                    logging.info("Oberfläche: %s dauerte %.1f s", _name, dauer)
        functools.update_wrapper(huelle, f)
        huelle.__signature__ = inspect.signature(f)
        setattr(cls, name, huelle)
    return cls


@_mit_zeitmessung
class OberflaecheApi(ShopApi, ImportApi):
    """Eine Schnittstelle für beide Tabs (pywebview kennt nur ein js_api).
    ShopApi bringt die Einstellungen (EinstellungenApi) mit."""

    def __init__(self, base_dir=None, benutzer=None, client_factory=None, oeffnen=None):
        ShopApi.__init__(self, base_dir, benutzer=benutzer,
                         client_factory=client_factory)
        self._init_import(oeffnen)
        self._ungesichert = 0

    def ungesichert_melden(self, anzahl) -> None:
        """Die Seite meldet, wie viele Änderungen ungesichert sind (für die
        Rückfrage beim Schließen)."""
        try:
            self._ungesichert = max(0, int(anzahl or 0))
        except (TypeError, ValueError):
            self._ungesichert = 0


def selbsttest() -> list[str]:
    """Für build.ps1: Fehlt der gebauten EXE etwas? Liefert die Mängel,
    leer = in Ordnung. Öffnet kein Fenster, liest keine Konfiguration."""
    fehlt = []
    for datei in (UI_DATEI, RESSOURCEN / "ui" / "fonts"):
        if not datei.exists():
            fehlt.append(f"{datei.relative_to(RESSOURCEN)} fehlt")
    try:
        import webview  # noqa: F401
    except ImportError as e:
        fehlt.append(f"pywebview fehlt ({e})")
    return fehlt


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--selbsttest" in argv:
        fehlt = selbsttest()
        text = "; ".join(fehlt) or f"ok — Programmstand {w.programmstand()}"
        # Die Fenster-EXE hat keine Konsole: Ergebnis zusätzlich als Datei
        (w.BASE_DIR / "selbsttest.txt").write_text(text + "\n", encoding="utf-8")
        print(f"Selbsttest {text}")
        return 1 if fehlt else 0

    import webview   # erst hier: Tests und Konsolen-EXE brauchen pywebview nicht

    w.setup_logging("INFO")
    if sys.platform == "win32" and not webview2_version():
        logging.error("WebView2-Laufzeit fehlt — Oberfläche nicht gestartet.")
        _meldung("WooCommerce → CDH", WEBVIEW2_HINWEIS)
        return 2
    start = time.perf_counter()
    api = OberflaecheApi(w.BASE_DIR)
    logging.info("Oberfläche gestartet von %s, Programmstand %s", api._benutzer,
                 w.programmstand())
    dauer = _startdauer()
    if dauer is not None:
        logging.info("Oberfläche: Start bis Python %.1f s (Entpacken der EXE)", dauer)
    fenster = webview.create_window(
        "WooCommerce → CDH", str(UI_DATEI), js_api=api,
        width=1120, height=780, min_size=(760, 560))
    fenster.events.closing += lambda: beim_schliessen(api)
    fenster.events.loaded += lambda: logging.info(
        "Oberfläche: Fenster bereit nach %.1f s", time.perf_counter() - start)
    # Nur WebView2, kein stiller Rückfall auf die IE-Engine
    webview.start(gui="edgechromium" if sys.platform == "win32" else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
