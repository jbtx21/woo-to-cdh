"""
CDH_WEX.EXE darf nur einmal gleichzeitig laufen.

Regression 23.09.2026: Eine alte Fassung mit subprocess.Popen startete alle
Importe eines Laufs gleichzeitig. Diese Tests schlagen fehl, sobald das
wieder passiert.
"""
import subprocess

import pytest

import woo_to_cdh as w


@pytest.fixture
def fake_exe(tmp_path):
    exe = tmp_path / "CDH_WEX.EXE"
    exe.write_text("dummy")
    return {"cdh_exe": str(exe)}


@pytest.fixture
def recorder(monkeypatch):
    """Ersetzt subprocess.run und protokolliert Start/Ende jedes Aufrufs."""
    events, state = [], {"offen": 0}

    def fake_run(cmd, *a, **k):
        state["offen"] += 1
        events.append(("start", cmd[-1].split("/")[-1].split("\\")[-1], state["offen"]))
        state["offen"] -= 1
        events.append(("ende", cmd[-1].split("/")[-1].split("\\")[-1], state["offen"]))
        return subprocess.CompletedProcess(cmd, 0)

    def verboten(*a, **k):
        raise AssertionError("subprocess.Popen wartet nicht — CDH-Importe würden parallel laufen")

    monkeypatch.setattr(w.subprocess, "run", fake_run)
    monkeypatch.setattr(w.subprocess, "Popen", verboten)
    monkeypatch.setattr(w, "_cdh_wex_running", lambda exe: False)
    return events


def test_strikt_nacheinander(fake_exe, recorder, tmp_path):
    for n in ("a.wex", "b.wex", "c.wex"):
        assert w.start_cdh_wex_import(tmp_path / n, fake_exe)
    assert [e[:2] for e in recorder] == [
        ("start", "a.wex"), ("ende", "a.wex"),
        ("start", "b.wex"), ("ende", "b.wex"),
        ("start", "c.wex"), ("ende", "c.wex"),
    ]
    assert max(e[2] for e in recorder) == 1   # nie mehr als ein Fenster offen


def test_wartet_auf_bereits_offenes_fenster(fake_exe, recorder, monkeypatch, tmp_path):
    abfragen = {"n": 0}

    def laeuft(exe):
        abfragen["n"] += 1
        return abfragen["n"] <= 2
    monkeypatch.setattr(w, "_cdh_wex_running", laeuft)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    assert w.start_cdh_wex_import(tmp_path / "x.wex", fake_exe)
    assert abfragen["n"] >= 3
    assert recorder[0][:2] == ("start", "x.wex")


def test_kein_start_wenn_fenster_offen_bleibt(fake_exe, recorder, monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_cdh_wex_running", lambda exe: True)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    uhr = {"t": 0.0}
    monkeypatch.setattr(w.time, "monotonic", lambda: uhr.__setitem__("t", uhr["t"] + 120) or uhr["t"])
    assert not w.start_cdh_wex_import(tmp_path / "y.wex", fake_exe)
    assert recorder == []                     # nichts gestartet


def test_fehlende_exe(tmp_path):
    assert not w.start_cdh_wex_import(tmp_path / "z.wex", {"cdh_exe": str(tmp_path / "gibtsnicht.exe")})
