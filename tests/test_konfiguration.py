"""Vorlagen im Repo: gültiges YAML, keine Zugangsdaten."""
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ECHTER_SCHLUESSEL = re.compile(r"c[ks]_[0-9a-f]{20,}")
TEXT_SUFFIXE = {".py", ".yaml", ".yml", ".md", ".txt", ".html", ".ps1"}


def getrackte_dateien():
    """Nur die von Git verwalteten Dateien — NICHT gitignorierte Betriebsdaten.

    Die echte config.yaml/zugang.yaml/einstellungen.yaml liegen lokal im
    Ordner (aus V: kopiert), sind aber gitignoriert. Ein rglob über den
    ganzen Ordner würde sie lesen und im Fehlerfall echte Schlüssel in die
    pytest-Ausgabe schreiben. Deshalb prüfen wir nur, was tatsächlich im
    Repo landet.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT,
        capture_output=True, text=True, check=True,
    )
    return [ROOT / zeile for zeile in out.stdout.splitlines() if zeile]


def dateien_mit_schluessel(pfade):
    """Liefert die Pfade mit echtem Schlüssel — NIE den Fund selbst.

    Bewusst kein `assert regex.search(text)`: pytests Assertion-Rewriting
    würde den Match und den Dateiinhalt (also den Schlüssel) ausgeben.
    """
    treffer = []
    for p in pfade:
        if p.is_file() and p.suffix in TEXT_SUFFIXE:
            if ECHTER_SCHLUESSEL.search(p.read_text(encoding="utf-8", errors="ignore")):
                treffer.append(p)
    return treffer


def test_vorlagen_sind_gueltiges_yaml():
    for f in ("config.sample.yaml", "lieferadressen.sample.yaml",
              "einstellungen.sample.yaml", "zugang.sample.yaml"):
        assert yaml.safe_load((ROOT / f).read_text(encoding="utf-8")), f


def test_keine_echten_schluessel_im_repo():
    treffer = dateien_mit_schluessel(getrackte_dateien())
    # Meldung enthält nur Pfade, nie den gefundenen Schlüsseltext.
    assert not treffer, "Echter API-Schlüssel in: " + ", ".join(str(p) for p in treffer)


def test_schluesselpruefung_meldet_nur_pfad(tmp_path):
    """Bei einem Treffer darf nur der Pfad auftauchen, nie der Schlüssel."""
    geheim = tmp_path / "leak.yaml"
    fake = "ck_" + "0123456789abcdef0123456789abcdef01234567"
    geheim.write_text(f"consumer_key: '{fake}'\n", encoding="utf-8")

    treffer = dateien_mit_schluessel([geheim])
    assert treffer == [geheim]

    meldung = "Echter API-Schlüssel in: " + ", ".join(str(p) for p in treffer)
    assert str(geheim) in meldung          # Pfad wird genannt
    assert fake not in meldung             # der Schlüssel niemals


def test_shops_vollstaendig():
    cfg = yaml.safe_load((ROOT / "config.sample.yaml").read_text(encoding="utf-8"))
    for s in cfg["shops"]:
        for k in ("name", "url", "consumer_key", "consumer_secret", "datev_no", "order_type"):
            assert k in s, f"{s.get('name')}: {k} fehlt"
        assert s["url"].endswith("/"), s["name"]
    assert "234/" in cfg["veredelung_prefixes"]


def test_samples_sind_aufgeteilt():
    """einstellungen.sample.yaml ohne Zugangsdaten, zugang.sample.yaml mit."""
    einst = yaml.safe_load((ROOT / "einstellungen.sample.yaml").read_text(encoding="utf-8"))
    zugang = yaml.safe_load((ROOT / "zugang.sample.yaml").read_text(encoding="utf-8"))

    for s in einst["shops"]:
        assert "consumer_key" not in s, f"{s.get('name')}: Schlüssel in einstellungen"
        assert "consumer_secret" not in s, f"{s.get('name')}: Secret in einstellungen"
        # Einstellungen bleiben erhalten:
        for k in ("name", "url", "datev_no", "order_type"):
            assert k in s, f"{s.get('name')}: {k} fehlt"

    assert "shops" in zugang and zugang["shops"], "zugang ohne Shops"
    # Welle 5: zugang.yaml ist nach der festen Shop-id verschlüsselt
    assert set(zugang["shops"]) == {s["id"] for s in einst["shops"]}
    for name, creds in zugang["shops"].items():
        assert "consumer_key" in creds, f"{name}: consumer_key fehlt"
        assert "consumer_secret" in creds, f"{name}: consumer_secret fehlt"
    # Admin-Passwort als Hash-Struktur vorgesehen:
    assert zugang["admin"]["password"]["algo"] == "pbkdf2_sha256"
    assert zugang["admin"]["password"]["iterations"] >= 200000
