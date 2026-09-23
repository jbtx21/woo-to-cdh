"""Migration config.yaml -> einstellungen.yaml + zugang.yaml (Welle 2).

Trennt die eine config.yaml in zwei Dateien:

  einstellungen.yaml  alles AUSSER Zugangsdaten (Innendienst-tauglich)
  zugang.yaml         Consumer Key/Secret je Shop + Admin-Passwort-Hash

Der Probelauf (--probelauf) schreibt NICHTS und gibt KEINE Schlüsselwerte
aus — nur, ob sie vorhanden sind. Das ist Absicht: die Ausgabe darf gefahrlos
in Logs oder Tickets landen (siehe README §15).

    python migrate_config.py --probelauf            # nur anzeigen
    python migrate_config.py                         # Dateien schreiben
    python migrate_config.py --admin-password ...    # zusätzlich Hash setzen

Migration auf V: nur nach Freigabe und mit Sicherung der alten config.yaml
in Backup\\ (siehe Briefing, Welle 2 + Regel 8: Produktionsstopp).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Diese beiden Felder sind die einzigen Geheimnisse je Shop. Alles andere
# gilt als Einstellung.
GEHEIME_FELDER = ("consumer_key", "consumer_secret")

PBKDF2_ITERATIONS = 200_000   # Briefing: >= 200 000
PBKDF2_ALGO = "pbkdf2_sha256"


# ---------------------------------------------------------------------------
# Passwort-Hash (Admin)
# ---------------------------------------------------------------------------

def hash_admin_password(password: str, *, iterations: int = PBKDF2_ITERATIONS,
                        salt: bytes | None = None) -> dict:
    """Admin-Passwort als PBKDF2-HMAC-SHA256-Hash mit Salt.

    Rückgabe ist eine reine YAML-taugliche Struktur (nur Hex-Strings, Zahlen).
    Der Salt wird bei jedem Aufruf neu gezogen, außer er wird übergeben
    (nur für Tests sinnvoll).
    """
    if not password:
        raise ValueError("Admin-Passwort darf nicht leer sein.")
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {
        "algo": PBKDF2_ALGO,
        "iterations": iterations,
        "salt": salt.hex(),
        "hash": dk.hex(),
    }


def verify_admin_password(password: str, record: dict) -> bool:
    """Prüft ein Passwort gegen einen mit hash_admin_password erzeugten Satz."""
    if not record or not record.get("salt") or not record.get("hash"):
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        bytes.fromhex(record["salt"]),
        int(record.get("iterations", PBKDF2_ITERATIONS)),
    )
    return secrets.compare_digest(dk.hex(), record["hash"])


def leerer_passwort_satz() -> dict:
    """Hash-Struktur ohne gesetztes Passwort (wird später über die GUI gefüllt)."""
    return {"algo": PBKDF2_ALGO, "iterations": PBKDF2_ITERATIONS,
            "salt": "", "hash": ""}


# ---------------------------------------------------------------------------
# Aufteilen
# ---------------------------------------------------------------------------

def split_config(cfg: dict, *, admin_password: str | None = None,
                 vorhandener_zugang: dict | None = None) -> tuple[dict, dict]:
    """config-Dict -> (einstellungen, zugang).

    einstellungen: tiefe Kopie ohne die geheimen Felder je Shop.
    zugang: { admin: {...}, shops: { <name>: {consumer_key, consumer_secret} } }

    Ein bereits vorhandener Zugang (z. B. mit gesetztem Admin-Hash) wird als
    Basis genommen, damit die Migration einen Hash nicht überschreibt.
    """
    einstellungen = copy.deepcopy(cfg)
    zugang = copy.deepcopy(vorhandener_zugang) if vorhandener_zugang else {}
    zugang.setdefault("admin", {})
    zugang["admin"].setdefault("password", leerer_passwort_satz())
    zugang["admin"].setdefault("users", [])
    zugang.setdefault("shops", {})

    for shop in einstellungen.get("shops", []):
        name = shop.get("name") or shop.get("url")
        creds = zugang["shops"].get(name, {})
        for feld in GEHEIME_FELDER:
            if feld in shop:
                creds[feld] = shop.pop(feld)
        if creds:
            zugang["shops"][name] = creds

    if admin_password:
        zugang["admin"]["password"] = hash_admin_password(admin_password)

    return einstellungen, zugang


def _enthaelt_geheimnis(obj) -> bool:
    """Rekursive Sicherung: steht irgendwo noch ein geheimes Feld?"""
    if isinstance(obj, dict):
        if any(k in obj for k in GEHEIME_FELDER):
            return True
        return any(_enthaelt_geheimnis(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_enthaelt_geheimnis(v) for v in obj)
    return False


# ---------------------------------------------------------------------------
# Probelauf (schreibt nichts, nennt keine Werte)
# ---------------------------------------------------------------------------

def _vorhanden(wert) -> bool:
    return bool(str(wert).strip()) if wert is not None else False


def probelauf_bericht(cfg: dict, *, ziel_einstellungen: Path,
                      ziel_zugang: Path) -> str:
    """Baut den Probelauf-Text. Nennt NUR Vorhandensein, nie Schlüsselwerte."""
    zeilen: list[str] = []
    zeilen.append("=== Probelauf Migration config.yaml -> einstellungen.yaml + zugang.yaml ===")
    zeilen.append("(Es wird NICHTS geschrieben. Es werden KEINE Schlüsselwerte ausgegeben.)")
    zeilen.append("")

    shops = cfg.get("shops", [])
    global_keys = sorted(k for k in cfg.keys() if k != "shops")

    zeilen.append(f"einstellungen.yaml  ->  {ziel_einstellungen}")
    zeilen.append(f"    globale Optionen: {', '.join(global_keys) or '(keine)'}")
    zeilen.append(f"    Shops: {len(shops)}")
    zeilen.append("")

    zeilen.append(f"zugang.yaml         ->  {ziel_zugang}")
    zeilen.append("    Admin-Passwort-Hash: wird leer angelegt "
                  "(später über die Oberfläche setzen)")
    zeilen.append("    Consumer Key/Secret je Shop:")
    for shop in shops:
        name = shop.get("name") or shop.get("url") or "(ohne Namen)"
        ck = "vorhanden" if _vorhanden(shop.get("consumer_key")) else "FEHLT"
        cs = "vorhanden" if _vorhanden(shop.get("consumer_secret")) else "FEHLT"
        zeilen.append(f"      - {name:16}  consumer_key: {ck:9}  consumer_secret: {cs}")
    zeilen.append("")

    zeilen.append("Nach dem Schreiben: config.yaml wird nach "
                  "Backup\\config_<Datum>.yaml verschoben.")
    zeilen.append("")

    # Kontrolle: nach dem Split darf in den Einstellungen kein Geheimnis mehr stehen.
    einstellungen, _zugang = split_config(cfg)
    sauber = not _enthaelt_geheimnis(einstellungen)
    zeilen.append(f"Kontrolle: keine Zugangsdaten mehr in einstellungen.yaml? "
                  f"{'ja' if sauber else 'NEIN — Abbruch nötig!'}")

    # Hinweise auf schon existierende Zieldateien (Überschreibgefahr).
    for pfad in (ziel_einstellungen, ziel_zugang):
        if pfad.exists():
            zeilen.append(f"Hinweis: {pfad.name} existiert bereits — echte Migration "
                          f"würde sichern und ersetzen (nur mit --force / nach Backup).")

    zeilen.append("")
    zeilen.append("Probelauf ohne Änderungen beendet.")
    return "\n".join(zeilen)


# ---------------------------------------------------------------------------
# Schreiben (echte Migration)
# ---------------------------------------------------------------------------

def _dump(pfad: Path, daten: dict) -> None:
    with pfad.open("w", encoding="utf-8") as f:
        yaml.safe_dump(daten, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)


def verschiebe_config_nach_backup(config_pfad: Path, backup_dir: Path, *,
                                  datum: str | None = None) -> Path:
    """Verschiebt die alte config.yaml nach Backup\\config_<Datum>.yaml.

    Nach der Migration sollen alte und neue Dateien NICHT nebeneinander liegen
    (sonst warnt load_config()). Der Datei-Stempel macht die Sicherung
    nachvollziehbar. `datum` ist nur für Tests da.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    if datum is None:
        datum = datetime.now().strftime("%Y-%m-%d")
    ziel = backup_dir / f"config_{datum}.yaml"
    if ziel.exists():   # gleiche am selben Tag nicht überschreiben
        ziel = backup_dir / f"config_{datum}_{datetime.now().strftime('%H%M%S')}.yaml"
    shutil.move(str(config_pfad), str(ziel))
    return ziel


def schreibe_migration(cfg: dict, *, ziel_einstellungen: Path, ziel_zugang: Path,
                       admin_password: str | None = None, force: bool = False,
                       backup_dir: Path | None = None,
                       quelle_config: Path | None = None,
                       datum: str | None = None) -> Path | None:
    """Schreibt beide Dateien. Vorhandene Ziele werden vorher gesichert.

    Ohne force wird ein bestehendes Ziel NICHT überschrieben (Sicherheitsnetz).
    Ist `quelle_config` gesetzt, wird die alte config.yaml am Ende nach
    Backup\\config_<Datum>.yaml verschoben; der Backup-Pfad wird zurückgegeben.
    """
    vorhandener_zugang = None
    if ziel_zugang.exists():
        vorhandener_zugang = yaml.safe_load(ziel_zugang.read_text(encoding="utf-8")) or None

    einstellungen, zugang = split_config(
        cfg, admin_password=admin_password, vorhandener_zugang=vorhandener_zugang)

    if _enthaelt_geheimnis(einstellungen):
        raise RuntimeError("Abbruch: einstellungen.yaml enthielte noch Zugangsdaten.")

    for pfad, daten in ((ziel_einstellungen, einstellungen), (ziel_zugang, zugang)):
        if pfad.exists():
            if not force:
                raise FileExistsError(
                    f"{pfad} existiert bereits. Mit --force überschreiben "
                    f"(vorher sichern!).")
            if backup_dir is not None:
                backup_dir.mkdir(parents=True, exist_ok=True)
                (backup_dir / pfad.name).write_text(
                    pfad.read_text(encoding="utf-8"), encoding="utf-8")
        _dump(pfad, daten)

    # Alte config.yaml aus dem Weg räumen, damit nichts doppelt gilt.
    if quelle_config is not None and quelle_config.exists():
        ziel_backup = backup_dir if backup_dir is not None \
            else quelle_config.resolve().parent / "Backup"
        return verschiebe_config_nach_backup(quelle_config, ziel_backup, datum=datum)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _lade_yaml(pfad: Path) -> dict:
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migration config.yaml -> einstellungen.yaml + zugang.yaml")
    p.add_argument("--config", default="config.yaml", type=Path,
                   help="Quelldatei (Standard: config.yaml im aktuellen Ordner)")
    p.add_argument("--probelauf", action="store_true",
                   help="Nichts schreiben, nur anzeigen was entstünde (keine Schlüsselwerte).")
    p.add_argument("--admin-password", default=None,
                   help="Admin-Passwort; wird gehasht in zugang.yaml abgelegt.")
    p.add_argument("--force", action="store_true",
                   help="Bestehende Zieldateien überschreiben (nach Sicherung).")
    args = p.parse_args(argv)

    config_pfad = args.config
    if not config_pfad.exists():
        print(f"config.yaml fehlt: {config_pfad}", file=sys.stderr)
        return 2

    cfg = _lade_yaml(config_pfad)
    ordner = config_pfad.resolve().parent
    ziel_einstellungen = ordner / "einstellungen.yaml"
    ziel_zugang = ordner / "zugang.yaml"

    if args.probelauf:
        print(probelauf_bericht(cfg, ziel_einstellungen=ziel_einstellungen,
                                ziel_zugang=ziel_zugang))
        return 0

    backup = schreibe_migration(
        cfg, ziel_einstellungen=ziel_einstellungen, ziel_zugang=ziel_zugang,
        admin_password=args.admin_password, force=args.force,
        backup_dir=ordner / "Backup", quelle_config=config_pfad)
    print(f"Geschrieben: {ziel_einstellungen}")
    print(f"Geschrieben: {ziel_zugang}")
    if backup:
        print(f"Alte config.yaml gesichert nach: {backup}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
