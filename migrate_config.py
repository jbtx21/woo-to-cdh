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

Welle 5 — feste Shop-ids statt Namen als Schlüssel in zugang.yaml:

    python migrate_config.py --shop-ids --probelauf  # nur anzeigen
    python migrate_config.py --shop-ids              # umstellen (mit Backup)

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

from woo_to_cdh import shop_id_aus_name

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

def vergebe_shop_ids(shops: list) -> dict:
    """Ergänzt fehlende Shop-ids (aus dem Namen, eindeutig) und liefert
    {Name: id}. Vorhandene ids bleiben unverändert."""
    vergeben = {s["id"] for s in shops if s.get("id")}
    zuordnung = {}
    for shop in shops:
        name = shop.get("name") or shop.get("url")
        if not shop.get("id"):
            basis = shop_id_aus_name(name)
            sid, n = basis, 2
            while sid in vergeben:
                sid, n = f"{basis}-{n}", n + 1
            vergeben.add(sid)
            # id als erstes Feld, damit sie in der Datei oben steht
            neu = {"id": sid, **shop}
            shop.clear()
            shop.update(neu)
        zuordnung[name] = shop["id"]
    return zuordnung


def split_config(cfg: dict, *, admin_password: str | None = None,
                 vorhandener_zugang: dict | None = None) -> tuple[dict, dict]:
    """config-Dict -> (einstellungen, zugang).

    einstellungen: tiefe Kopie ohne die geheimen Felder je Shop, jeder Shop
    mit fester id (Welle 5).
    zugang: { admin: {...}, shops: { <id>: {consumer_key, consumer_secret} } }

    Ein bereits vorhandener Zugang (z. B. mit gesetztem Admin-Hash) wird als
    Basis genommen, damit die Migration einen Hash nicht überschreibt.
    """
    einstellungen = copy.deepcopy(cfg)
    zugang = copy.deepcopy(vorhandener_zugang) if vorhandener_zugang else {}
    zugang.setdefault("admin", {})
    zugang["admin"].setdefault("password", leerer_passwort_satz())
    zugang["admin"].setdefault("users", [])
    zugang.setdefault("shops", {})

    vergebe_shop_ids(einstellungen.get("shops", []))
    for shop in einstellungen.get("shops", []):
        name = shop.get("name") or shop.get("url")
        creds = zugang["shops"].pop(name, None) or zugang["shops"].get(shop["id"], {})
        for feld in GEHEIME_FELDER:
            if feld in shop:
                creds[feld] = shop.pop(feld)
        if creds:
            zugang["shops"][shop["id"]] = creds

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
# Welle 5: feste Shop-ids in bestehenden einstellungen.yaml/zugang.yaml
# ---------------------------------------------------------------------------

def shop_ids_umstellen(einstellungen: dict, zugang: dict) -> tuple[dict, dict, list]:
    """Ergänzt ids in einstellungen und schlüsselt zugang.shops auf ids um.

    Liefert (einstellungen, zugang, zeilen) — zeilen beschreibt je Shop, was
    passiert, ohne Schlüsselwerte. Idempotent: ein zweiter Lauf ändert nichts.
    """
    einstellungen = copy.deepcopy(einstellungen)
    zugang = copy.deepcopy(zugang)
    zuordnung = vergebe_shop_ids(einstellungen.get("shops", []))
    alt = zugang.get("shops") or {}
    neu: dict = {}
    zeilen = []
    for name, sid in zuordnung.items():
        if sid in alt:
            neu[sid] = alt.pop(sid)
            zeilen.append(f"  {name:16} id {sid:12} Zugang schon unter id")
        elif name in alt:
            neu[sid] = alt.pop(name)
            zeilen.append(f"  {name:16} id {sid:12} Zugang: Name -> id")
        else:
            zeilen.append(f"  {name:16} id {sid:12} KEIN Zugang in zugang.yaml")
    for rest in alt:   # Einträge ohne passenden Shop nicht stillschweigend löschen
        neu[rest] = alt[rest]
        zeilen.append(f"  {rest:16} (kein Shop in einstellungen.yaml) bleibt stehen")
    zugang["shops"] = neu
    return einstellungen, zugang, zeilen


def shop_ids_migration(ordner: Path, *, probelauf: bool,
                       datum: str | None = None) -> str:
    """--shop-ids: bestehende Dateien umstellen. Vorher Backup beider Dateien."""
    pe, pz = ordner / "einstellungen.yaml", ordner / "zugang.yaml"
    for p in (pe, pz):
        if not p.exists():
            raise FileNotFoundError(f"{p} fehlt — erst die Migration aus config.yaml.")
    einstellungen, zugang, zeilen = shop_ids_umstellen(_lade_yaml(pe), _lade_yaml(pz))
    kopf = ["=== " + ("Probelauf " if probelauf else "") +
            "Umstellung zugang.yaml auf feste Shop-ids ===",
            "(Es werden KEINE Schlüsselwerte ausgegeben.)", ""]
    if probelauf:
        return "\n".join(kopf + zeilen + ["", "Probelauf ohne Änderungen beendet."])
    stempel = datum or datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = ordner / "Backup"
    backup.mkdir(parents=True, exist_ok=True)
    for p in (pe, pz):
        shutil.copy2(p, backup / f"{p.stem}_{stempel}{p.suffix}")
    _dump(pe, einstellungen)
    _dump(pz, zugang)
    return "\n".join(kopf + zeilen + ["", f"Gesichert nach {backup}", "Umgestellt."])


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
    p.add_argument("--shop-ids", action="store_true",
                   help="Bestehende einstellungen.yaml/zugang.yaml auf feste "
                        "Shop-ids umstellen (Welle 5).")
    args = p.parse_args(argv)

    if args.shop_ids:
        try:
            print(shop_ids_migration(args.config.resolve().parent,
                                     probelauf=args.probelauf))
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 2
        return 0

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
