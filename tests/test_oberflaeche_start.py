"""oberflaeche.py: WebView2-Prüfung (Windows 10 hat die Laufzeit nicht immer)."""
import oberflaeche as o


def _registry(werte):
    def lies(wurzel, pfad):
        if (wurzel, pfad) in werte:
            return werte[(wurzel, pfad)]
        raise OSError("nicht da")
    return lies


def test_webview2_gefunden_maschinenweit():
    wurzel, pfad = o.WEBVIEW2_SCHLUESSEL[0]
    assert o.webview2_version(_registry({(wurzel, pfad): "129.0.2792.65"})) == "129.0.2792.65"


def test_webview2_nur_fuer_benutzer():
    wurzel, pfad = o.WEBVIEW2_SCHLUESSEL[2]
    assert o.webview2_version(_registry({(wurzel, pfad): "128.0.1"})) == "128.0.1"


def test_webview2_fehlt_oder_platzhalter():
    assert o.webview2_version(_registry({})) is None
    wurzel, pfad = o.WEBVIEW2_SCHLUESSEL[0]
    assert o.webview2_version(_registry({(wurzel, pfad): "0.0.0.0"})) is None


def test_hinweis_nennt_download_und_konsole():
    assert "go.microsoft.com" in o.WEBVIEW2_HINWEIS and "WOO_to_CDH.exe" in o.WEBVIEW2_HINWEIS


# --- Schließen hing mit „Keine Rückmeldung“ (24.09.2026) -------------------------

class _Api:
    def __init__(self, n):
        self._ungesichert = n


def test_schliessen_ohne_aenderungen_fragt_nicht():
    assert o.beim_schliessen(_Api(0), frage=lambda *a: (_ for _ in ()).throw(AssertionError)) is True


def test_schliessen_mit_aenderungen_fragt():
    fragen = []
    assert o.beim_schliessen(_Api(2), frage=lambda t, x: fragen.append(x) or False) is False
    assert "2 Änderungen sind noch nicht gesichert" in fragen[0]
    assert o.beim_schliessen(_Api(1), frage=lambda t, x: True) is True


def test_schliessen_fragt_das_fenster_nicht_ab():
    """Kein evaluate_js und kein pywebview-Dialog im Schließen-Ereignis."""
    import inspect
    quelle = inspect.getsource(o.beim_schliessen) + inspect.getsource(o._frage)
    assert ".evaluate_js(" not in quelle and ".create_confirmation_dialog(" not in quelle


def test_ungesichert_melden(tmp_path):
    api = o.OberflaecheApi(tmp_path, benutzer="t")
    assert api._ungesichert == 0
    api.ungesichert_melden(3)
    assert api._ungesichert == 3
    api.ungesichert_melden("kaputt")
    assert api._ungesichert == 0
