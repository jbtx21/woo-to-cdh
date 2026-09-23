"""
TEXMA — WooCommerce → CDH: Oberfläche (Welle 5)
================================================

Öffnet ein Fenster mit der Oberfläche aus ui/index.html (pywebview, unter
Windows über WebView2). Die Oberfläche spricht über window.pywebview.api mit
einstellungen_api.EinstellungenApi — dort liegt die ganze Logik.

Stand Welle 5: Tab „Einstellungen“ vollständig. Der Import läuft bis
Welle 6 weiter über WOO_to_CDH.exe.

Start (Entwicklung):  python oberflaeche.py
Konfiguration:        einstellungen.yaml, zugang.yaml, lieferadressen.yaml
                      im Programmordner (wie bei WOO_to_CDH.exe)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import woo_to_cdh as w
from einstellungen_api import EinstellungenApi

# Mit PyInstaller liegen mitgelieferte Dateien (ui/) im Entpack-Ordner,
# die Konfiguration dagegen neben der EXE (w.BASE_DIR).
RESSOURCEN = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
UI_DATEI = RESSOURCEN / "ui" / "index.html"


def beim_schliessen(fenster) -> bool:
    """Fragt nach, wenn noch ungesicherte Änderungen offen sind.
    False bricht das Schließen ab (pywebview-Regel)."""
    try:
        n = int(fenster.evaluate_js("window.ungesichert ? window.ungesichert() : 0") or 0)
    except Exception:  # noqa: BLE001 — Fenster schon halb zu: schließen lassen
        return True
    if not n:
        return True
    return bool(fenster.create_confirmation_dialog(
        "Ungesicherte Änderungen",
        f"{n} {'Änderung ist' if n == 1 else 'Änderungen sind'} noch nicht "
        "gesichert und gehen verloren. Trotzdem schließen?"))


def main() -> int:
    import webview   # erst hier: Tests und Konsolen-EXE brauchen pywebview nicht

    w.setup_logging("INFO")
    api = EinstellungenApi(w.BASE_DIR)
    logging.info("Oberfläche gestartet von %s", api._benutzer)
    fenster = webview.create_window(
        "WooCommerce → CDH", str(UI_DATEI), js_api=api,
        width=1120, height=780, min_size=(760, 560))
    fenster.events.closing += lambda: beim_schliessen(fenster)
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
