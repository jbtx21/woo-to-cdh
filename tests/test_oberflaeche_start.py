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
