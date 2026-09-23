"""
TEXMA — WooCommerce zu CDH Launcher
====================================

Doppelklick-Starter. Führt den kompletten WooCommerce→CDH-Prozess aus
und hält das Fenster offen, bis der Benutzer Enter drückt.

Wenn mit PyInstaller als Standalone-EXE gebaut, braucht der Client-Rechner
KEIN Python und KEINE Python-Pakete — alles ist in der EXE enthalten.

Bauen:
    python -m pip install pyinstaller
    python -m PyInstaller --onefile --name WOO_to_CDH launcher.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def selbsttest() -> int:
    """Für build.ps1: Lässt sich alles laden, was die EXE braucht? Startet
    keinen Import, fasst weder Shop noch CDH an, wartet nicht auf Enter."""
    import openpyxl  # noqa: F401
    import requests  # noqa: F401
    import yaml  # noqa: F401

    import woo_to_cdh
    print(f"Selbsttest ok — Programmstand {woo_to_cdh.programmstand()}")
    return 0


def main() -> int:
    if "--selbsttest" in sys.argv[1:]:
        return selbsttest()
    # Arbeitsverzeichnis auf den Ordner der EXE/des Skripts setzen, damit
    # woo_to_cdh.py seine config.yaml und logs/ relativ dazu findet — egal
    # von wo die EXE gestartet wurde.
    import os
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).parent
    else:
        base_dir = Path(__file__).parent

    os.chdir(base_dir)
    # Auch in sys.path, damit `import woo_to_cdh` funktioniert
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))

    print("=" * 60)
    print("  TEXMA — WooCommerce zu CDH")
    print("=" * 60)
    print(f"\nArbeitsordner: {base_dir}")
    print(f"Starte Import-Prozess...\n")

    try:
        # Das Hauptskript direkt importieren und main() aufrufen.
        # Dadurch läuft alles in diesem Python-Prozess, und PyInstaller
        # packt beim Build beide Skripte plus alle Abhängigkeiten in die EXE.
        import woo_to_cdh
        exit_code = woo_to_cdh.main()
    except Exception as e:  # noqa: BLE001
        print(f"\nUnerwarteter Fehler: {e}")
        print("\nDetails:")
        traceback.print_exc()
        exit_code = 1

    print()
    print("=" * 60)
    if exit_code == 0:
        print("  Fertig! Alle neuen Bestellungen wurden verarbeitet.")
    elif exit_code == 3:
        print("  Anderer Lauf war bereits aktiv — abgebrochen.")
    else:
        print(f"  Lauf beendet mit Fehlercode {exit_code}.")
        print(f"  Siehe Log-Datei unter:")
        print(f"  {base_dir / 'logs' / 'woo_to_cdh.log'}")
    print("=" * 60)
    print()

    input("Drücke Enter zum Beenden...")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
