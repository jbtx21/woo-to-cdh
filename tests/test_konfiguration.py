"""Vorlagen im Repo: gültiges YAML, keine Zugangsdaten."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ECHTER_SCHLUESSEL = re.compile(r"c[ks]_[0-9a-f]{20,}")


def test_vorlagen_sind_gueltiges_yaml():
    for f in ("config.sample.yaml", "lieferadressen.sample.yaml"):
        assert yaml.safe_load((ROOT / f).read_text(encoding="utf-8")), f


def test_keine_echten_schluessel_im_repo():
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".yaml", ".yml", ".md", ".txt", ".html", ".ps1"} and ".git" not in p.parts:
            assert not ECHTER_SCHLUESSEL.search(p.read_text(encoding="utf-8", errors="ignore")), p


def test_shops_vollstaendig():
    cfg = yaml.safe_load((ROOT / "config.sample.yaml").read_text(encoding="utf-8"))
    for s in cfg["shops"]:
        for k in ("name", "url", "consumer_key", "consumer_secret", "datev_no", "order_type"):
            assert k in s, f"{s.get('name')}: {k} fehlt"
        assert s["url"].endswith("/"), s["name"]
    assert "234/" in cfg["veredelung_prefixes"]
