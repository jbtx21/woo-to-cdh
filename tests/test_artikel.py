"""Welle 10a: Artikel-Sammelpflege (artikel_api).

WooCommerce ist ArtikelWoo (conftest). Schlüssel werden zur Laufzeit gebaut
und dürfen in keinem Ergebnis, Log, Verlauf oder in der Rücknahme-Datei
auftauchen. Artikel und Namen erfunden.
"""
import copy
import json
import logging

import pytest
import yaml

import artikel_api as aa
import woo_to_cdh as w
from conftest import ArtikelWoo

KEY = "ck_" + "Q" * 24
SECRET = "cs_" + "W" * 24
CAF = "https://shop.example/caf-shop/"
AGRAR = "https://shop.example/agrar/"
ENS = "https://shop.example/ensinger-shop/"

EINST = {"cdh_import_folder": "wex", "shops": [
    {"id": "caf", "name": "CAF-Shop", "enabled": True, "url": CAF, "datev_no": 19541},
    {"id": "agrar", "name": "Agrar-Shop", "enabled": True, "url": AGRAR, "datev_no": 10698},
    {"id": "ens", "name": "Ensinger-Shop", "enabled": True, "url": ENS, "datev_no": 14020},
]}


def _p(pid, name, sku, ek=None, vk=None, preis=None, typ="simple"):
    return {"id": pid, "type": typ, "status": "publish", "name": name, "sku": sku,
            "regular_price": "" if preis is None else preis,
            "dimensions": {"length": ek or "", "width": vk or "", "height": ""}}


def _v(vid, sku, groesse, ek=None, vk=None, preis=None):
    return {"id": vid, "status": "publish", "sku": sku, "regular_price": preis or "",
            "attributes": [{"name": "Größe", "option": groesse}],
            "dimensions": {"length": ek or "", "width": vk or "", "height": ""}}


def _katalog():
    return {
        CAF: {"produkte": {
            10: _p(10, "Poloshirt", "042/POLO", typ="variable"),
            20: _p(20, "Cap", "042/CAP", "4.07", "7.65", "9.90"),
            30: _p(30, "Anstecker", "042/PIN", None, "2.00", "2.50")},
            "varianten": {10: {11: _v(11, "042/POLO-M", "M", "12.10", "24.90", "29.90"),
                               12: _v(12, "042/POLO-L", "L", "12.10", "24.90", "29.90")}}},
        AGRAR: {"produkte": {
            70: _p(70, "Cap (Agrar)", "042/CAP", "4.00", "7.50", "9.50"),
            80: _p(80, "Weste", "042/WESTE", "20.00", "35.00", "40.00")},
            "varianten": {}},
        ENS: {"produkte": {
            90: _p(90, "Cap", "042/CAP", "4.07", "7.65", "9.90"),
            91: _p(91, "Cap alt", "042/CAP", "4.07", "7.65", "9.90")},
            "varianten": {}},
    }


class Uhr:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def api(tmp_path, monkeypatch):
    (tmp_path / "einstellungen.yaml").write_text(
        yaml.safe_dump(EINST, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (tmp_path / "zugang.yaml").write_text(yaml.safe_dump({"shops": {
        sid: {"consumer_key": KEY, "consumer_secret": SECRET}
        for sid in ("caf", "agrar", "ens")}}), encoding="utf-8")
    monkeypatch.setattr(w, "DELIVERY_ADDRESSES_PATH", tmp_path / "lieferadressen.yaml")
    ArtikelWoo.katalog, ArtikelWoo.posts, ArtikelWoo.abgelehnt = _katalog(), [], set()
    ArtikelWoo.plugin_version = "2.7.0"
    a = aa.ArtikelApi(tmp_path, benutzer="m.mueller", uhr=Uhr(), client_factory=ArtikelWoo)
    a._admin_bis = float("inf")
    return a


def _aendern(api, *aenderungen, andere=False, shop="caf"):
    liste = [{"id": i, "feld": f, "wert": v} for i, f, v in aenderungen]
    return api.artikel_vorschau(shop, liste, andere)


def _shop(url):
    return ArtikelWoo.katalog[url]


def _alles(api):
    """Alles, was die Schnittstelle je nach außen gab, als ein Text."""
    teile = [(api._p_verlauf.read_text(encoding="utf-8") if api._p_verlauf.exists() else "")]
    teile += [p.read_text(encoding="utf-8") for p in api._p_artikel.glob("*.json")] \
        if api._p_artikel.exists() else []
    return "\n".join(teile)


# --- Werte ------------------------------------------------------------------

def test_zahlen_lesen_und_schreiben():
    assert aa._wert("vk", "12,5") == 12.5 and aa._wert("vk", "12.50") == 12.5
    assert aa._wert("ek", "") is None and aa._wert("ek", " 3 € ") == 3.0
    assert aa._wert("ek", 2) == 2.0
    for falsch in ("12,5x", "-3", "1.234,50", "abc", "1,23456"):
        with pytest.raises(ValueError):
            aa._wert("vk", falsch)
    assert aa._wert("sku", "  042/X ") == "042/X"
    assert aa._zahl_schreiben(12.5) == "12.50" and aa._zahl_schreiben(12) == "12.00"
    assert aa._zahl_schreiben(1.2345) == "1.2345" and aa._zahl_schreiben(None) == ""
    assert aa._text("vk", 24.9) == "24,90" and aa._text("ek", None) == "leer"


# --- Laden ------------------------------------------------------------------

def test_laden_nur_im_admin_modus(api):
    api._admin_bis = 0
    assert api.artikel_laden("caf") == {"ok": False, "fehler": "Artikel pflegen braucht den Admin-Modus."}
    assert api.artikel_vorschau("caf", [])["ok"] is False
    assert api.artikel_sichern("x")["ok"] is False
    assert api.artikel_zuruecknehmen("x", True)["ok"] is False


def test_laden_varianten_unter_dem_artikel(api):
    erg = api.artikel_laden("caf")
    assert erg["ok"] and erg["andere"] == ["Agrar-Shop", "Ensinger-Shop"]
    assert [(z["id"], z["parent"]) for z in erg["artikel"]] == [
        (30, 0), (20, 0), (10, 0), (11, 10), (12, 10)]          # nach Name, Varianten darunter
    m = erg["artikel"][3]
    assert m == {"id": 11, "parent": 10, "typ": "variation", "status": "publish",
                 "sku": "042/POLO-M", "name": "Poloshirt", "variante": "M",
                 "merkmale": [{"name": "Größe", "option": "M"}],
                 "ek": 12.1, "vk": 24.9, "preis": 29.9, "kurz": "", "text": "", "bilder": [],
                 "zubehoer": [], "zubehoer_eigen": False, "staffel": []}
    assert erg["artikel"][0]["ek"] is None and erg["letzte"] is None
    assert ArtikelWoo.posts == []


# --- Vorschau ---------------------------------------------------------------

def test_vorschau_zeigt_alt_neu_und_schreibt_nichts(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (11, "vk", "26,90"), (11, "ek", "12,50"), (20, "name", "Cap Logo"),
                   (20, "preis", "9,90"))                       # unverändert → fällt weg
    assert erg["ok"] and erg["fehler"] == [] and erg["vorschau_id"]
    assert erg["anzahl"] == 3
    zeilen = erg["shops"][0]["zeilen"]
    assert {(z["bez"], z["feldname"], z["alt"], z["neu"]) for z in zeilen} == {
        ("042/POLO-M Poloshirt (M)", "VK", "24,90", "26,90"),
        ("042/POLO-M Poloshirt (M)", "EK", "12,10", "12,50"),
        ("042/CAP Cap", "Artikelname", "Cap", "Cap Logo")}
    assert ArtikelWoo.posts == []


def test_nichts_geaendert_keine_vorschau(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (11, "vk", "24.9"))
    assert erg["anzahl"] == 0 and erg["vorschau_id"] is None and erg["fehler"] == []


@pytest.mark.parametrize("aenderung, text", [
    ((11, "vk", "2x"), "VK „2x“ ist keine Zahl"),
    ((11, "ek", "30"), "EK (30,00) größer als VK (24,90)"),
    ((12, "sku", "042/CAP"), "Artikelnummer 042/CAP gibt es im Shop schon (042/CAP Cap)"),
    ((12, "sku", "042/cap"), "Artikelnummer 042/cap gibt es im Shop schon"),
    ((11, "name", "Neu"), "Varianten haben keinen eigenen Namen"),
    ((10, "preis", "30"), "Verkaufspreis wird an den Varianten gepflegt"),
    ((20, "sku", " "), "Artikelnummer darf nicht leer sein"),
    ((999, "vk", "1"), "Artikel #999: unbekannt"),
])
def test_vorschau_fehler_blockieren(api, aenderung, text):
    api.artikel_laden("caf")
    erg = _aendern(api, aenderung)
    assert erg["ok"] and erg["vorschau_id"] is None
    assert any(text in f for f in erg["fehler"]), erg["fehler"]


def test_sku_tausch_innerhalb_der_aenderung_erlaubt(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (11, "sku", "042/POLO-L"), (12, "sku", "042/POLO-M"))
    assert erg["fehler"] == [] and erg["vorschau_id"]


def test_vorschau_warnungen(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "ek", "7,20"), (11, "vk", "49,90"), (12, "ek", ""),
                   (30, "preis", ""))
    assert erg["fehler"] == [] and erg["vorschau_id"]
    t = "\n".join(erg["warnungen"])
    assert "042/CAP Cap: Marge nur 6 %." in t
    assert "042/POLO-M Poloshirt (M): VK ändert sich um +100 % — Tippfehler?" in t
    assert "042/POLO-L Poloshirt (L): EK leer — fehlt dann im CDH-Auftrag." in t
    assert "042/PIN Anstecker: Verkaufspreis leer — im Shop nicht kaufbar." in t


def test_vorschau_erkennt_aenderung_im_shop(api):
    api.artikel_laden("caf")
    _shop(CAF)["varianten"][10][11]["dimensions"]["width"] = "25.90"   # jemand im Backend
    erg = api.artikel_vorschau("caf", [{"id": 11, "feld": "vk", "wert": "26,90", "alt": 24.9}])
    assert erg["vorschau_id"] is None
    assert "VK wurde inzwischen im Shop geändert (jetzt 25,90). Bitte neu laden." in erg["fehler"][0]


# --- Sichern ----------------------------------------------------------------

def test_sichern_batch_verlauf_ruecknahme_datei(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (11, "vk", "26,90"), (11, "ek", "12,5"), (20, "name", "Cap Logo"),
                   (20, "preis", "10"), (12, "sku", "042/POLO-XL"))
    s = api.artikel_sichern(erg["vorschau_id"])
    assert s["ok"] and s["gesichert"] == 5 and s["meldungen"] == []
    pfade = {p: d for p, d in ArtikelWoo.posts}
    assert pfade["/products/batch"] == {"update": [
        {"id": 20, "name": "Cap Logo", "regular_price": "10.00"}]}
    assert sorted(pfade["/products/10/variations/batch"]["update"], key=lambda d: d["id"]) == [
        {"id": 11, "dimensions": {"width": "26.90", "length": "12.50"}},
        {"id": 12, "sku": "042/POLO-XL"}]
    v = _shop(CAF)["varianten"][10][11]
    assert v["dimensions"]["length"] == "12.50" and v["dimensions"]["height"] == ""
    verlauf = api._p_verlauf.read_text(encoding="utf-8")
    assert "CAF-Shop: Sammeländerung, 3 Artikel" in verlauf
    assert "CAF-Shop: 042/POLO-M Poloshirt (M) — VK 24,90 → 26,90; EK 12,10 → 12,50" in verlauf
    assert s["letzte"]["anzahl"] == 5 and s["letzte"]["benutzer"] == "m.mueller"
    # Dieselbe Vorschau gilt nur einmal
    assert api.artikel_sichern(erg["vorschau_id"])["fehler"].startswith("Die Vorschau ist nicht mehr aktuell")
    assert len(ArtikelWoo.posts) == 2


def test_sichern_nichts_wenn_shop_seit_vorschau_geaendert(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "vk", "8,00"), (11, "vk", "26,90"))
    _shop(CAF)["produkte"][20]["dimensions"]["width"] = "7.95"
    s = api.artikel_sichern(erg["vorschau_id"])
    assert not s["ok"] and "seit der Vorschau im Shop geändert (042/CAP Cap)" in s["fehler"]
    assert ArtikelWoo.posts == [] and api._letzte_info() is None


def test_sichern_einzelner_artikel_abgelehnt(api):
    api.artikel_laden("caf")
    ArtikelWoo.abgelehnt = {20}
    erg = _aendern(api, (20, "vk", "8,00"), (30, "vk", "2,20"))
    s = api.artikel_sichern(erg["vorschau_id"])
    assert s["ok"] and s["gesichert"] == 1
    assert s["meldungen"] == ["042/CAP Cap: Ungültige oder doppelte Artikelnummer."]
    assert s["gesichert_keys"] == ["30:vk"]              # 20 bleibt im Entwurf der Tabelle
    datei = json.loads(next(api._p_artikel.glob("sammel_*.json")).read_text(encoding="utf-8"))
    assert [z["id"] for z in datei["shops"][0]["zeilen"]] == [30]


def test_sichern_in_stuecken_zu_100(api):
    _shop(CAF)["produkte"] = {i: _p(i, f"Artikel {i:03d}", f"A-{i}", "1.00", "2.00", "3.00")
                              for i in range(1, 251)}
    _shop(CAF)["varianten"] = {}
    api.artikel_laden("caf")
    erg = _aendern(api, *[(i, "vk", "2,10") for i in range(1, 251)])
    assert api.artikel_sichern(erg["vorschau_id"])["gesichert"] == 250
    assert [len(d["update"]) for _, d in ArtikelWoo.posts] == [100, 100, 50]


# --- Andere Shops -----------------------------------------------------------

def test_andere_shops_nur_preise_ueber_artikelnummer(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "vk", "7,95"), (20, "name", "Cap Logo"), (30, "vk", "2,10"),
                   andere=True)
    assert erg["fehler"] == [] and erg["vorschau_id"]
    shops = {s["name"]: s["zeilen"] for s in erg["shops"]}
    assert set(shops) == {"CAF-Shop", "Agrar-Shop"}
    assert [(z["bez"], z["feldname"], z["alt"], z["neu"]) for z in shops["Agrar-Shop"]] == [
        ("Agrar-Shop: 042/CAP Cap (Agrar)", "VK", "7,50", "7,95")]  # alt aus dem Agrar-Shop
    assert "Ensinger-Shop: Artikelnummer 042/CAP gibt es mehrfach — dort nicht geändert." \
        in erg["warnungen"]
    assert erg["ohne_treffer"] == ["042/PIN"]
    s = api.artikel_sichern(erg["vorschau_id"])
    assert s["gesichert"] == 4
    assert sorted(s["gesichert_keys"]) == ["20:name", "20:vk", "30:vk"]   # nur der eigene Shop
    assert _shop(AGRAR)["produkte"][70]["dimensions"]["width"] == "7.95"
    assert _shop(AGRAR)["produkte"][70]["name"] == "Cap (Agrar)"
    assert _shop(ENS)["produkte"][90]["dimensions"]["width"] == "7.65"
    assert "Agrar-Shop: Sammeländerung, 1 Artikel" in api._p_verlauf.read_text(encoding="utf-8")


def test_andere_shops_pruefen_mit_eigenen_werten(api):
    """Im Agrar-Shop ist der EK höher — derselbe VK wäre dort unter EK."""
    _shop(AGRAR)["produkte"][70]["dimensions"]["length"] = "7.00"
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "vk", "6,90"), andere=True)
    assert erg["vorschau_id"] is None
    assert erg["fehler"] == ["Agrar-Shop: 042/CAP Cap (Agrar): EK (7,00) größer als VK (6,90)."]
    assert _aendern(api, (20, "vk", "6,90"))["vorschau_id"]      # ohne Option in Ordnung


# --- Zurücknehmen -----------------------------------------------------------

def test_zuruecknehmen(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "vk", "8,00"), (11, "vk", "26,90"), (12, "sku", "042/POLO-XL"),
                   andere=True)
    api.artikel_sichern(erg["vorschau_id"])
    letzte = api.artikel_letzte()["letzte"]
    assert letzte["anzahl"] == 4 and {s["name"] for s in letzte["shops"]} == {"CAF-Shop", "Agrar-Shop"}
    assert "CAF-Shop: 042/CAP Cap — VK 7,65 → 8,00" in letzte["zeilen"]
    _shop(CAF)["varianten"][10][11]["dimensions"]["width"] = "27.90"    # danach im Backend
    ArtikelWoo.posts = []
    assert api.artikel_zuruecknehmen(letzte["datei"])["fehler"].startswith("Ohne ausdrückliche")
    z = api.artikel_zuruecknehmen(letzte["datei"], True)
    assert z["ok"] and z["zurueck"] == 3
    assert z["ausgelassen"] == ["CAF-Shop: 042/POLO-M Poloshirt (M) — VK inzwischen anders, bleibt."]
    assert _shop(CAF)["produkte"][20]["dimensions"]["width"] == "7.65"
    assert _shop(CAF)["varianten"][10][12]["sku"] == "042/POLO-L"
    assert _shop(CAF)["varianten"][10][11]["dimensions"]["width"] == "27.90"
    assert _shop(AGRAR)["produkte"][70]["dimensions"]["width"] == "7.50"
    verlauf = api._p_verlauf.read_text(encoding="utf-8")
    assert "CAF-Shop: Sammeländerung zurückgenommen, 2 Artikel" in verlauf
    assert "CAF-Shop: 042/CAP Cap — VK 8,00 → 7,65" in verlauf
    assert api.artikel_letzte()["letzte"] is None
    assert "schon zurückgenommen" in api.artikel_zuruecknehmen(letzte["datei"], True)["fehler"]


def test_zuruecknehmen_nur_die_neueste(api):
    api.artikel_laden("caf")
    api.artikel_sichern(_aendern(api, (20, "vk", "8,00"))["vorschau_id"])
    alt = api.artikel_letzte()["letzte"]["datei"]
    Uhr.t += 60
    try:
        api.artikel_sichern(_aendern(api, (30, "vk", "2,10"))["vorschau_id"])
    finally:
        Uhr.t -= 60
    assert "neuere Sammeländerung" in api.artikel_zuruecknehmen(alt, True)["fehler"]


# --- Schlüssel --------------------------------------------------------------

def test_keine_schluessel_nach_aussen(api, caplog):
    caplog.set_level(logging.DEBUG)
    ergebnisse = [api.artikel_laden("caf")]
    erg = _aendern(api, (20, "vk", "8,00"), andere=True)
    ergebnisse += [erg, api.artikel_sichern(erg["vorschau_id"]), api.artikel_letzte()]
    ergebnisse.append(api.artikel_zuruecknehmen(api._letzte_info()["datei"], True))
    text = json.dumps(ergebnisse, ensure_ascii=False) + caplog.text + _alles(api)
    assert KEY not in text and SECRET not in text


def test_netzfehler_ohne_schluessel(api, monkeypatch):
    import requests

    def kaputt(self, path, params=None):
        raise requests.ConnectionError(f"https://shop.example/?consumer_key={KEY}&consumer_secret={SECRET}")
    monkeypatch.setattr(ArtikelWoo, "_get", kaputt)
    erg = api.artikel_laden("caf")
    assert not erg["ok"] and erg["fehler"].startswith("Artikel nicht abrufbar")
    assert KEY not in erg["fehler"] and SECRET not in erg["fehler"]


def test_kopie_des_katalogs_bleibt_unberuehrt(api):
    """Die Oberfläche bekommt Kopien — Schreiben ändert keine alten Antworten."""
    erg = api.artikel_laden("caf")
    vorher = copy.deepcopy(erg["artikel"])
    api.artikel_sichern(_aendern(api, (20, "vk", "8,00"))["vorschau_id"])
    assert erg["artikel"] == vorher


# --- Texte (Welle 10b) --------------------------------------------------------

LANG = "<p>Poloshirt aus Baumwolle, " + "sehr angenehm zu tragen. " * 20 + "Waschbar bei 60 Grad.</p>"


def test_ausschnitt_zeigt_nur_die_stelle():
    alt, neu = aa._ausschnitt(LANG, LANG.replace("60 Grad", "40 Grad"))
    assert alt.startswith("…") and alt.endswith("Waschbar bei 60 Grad.</p>")
    assert neu.endswith("Waschbar bei 40 Grad.</p>") and len(neu) < 60
    assert aa._ausschnitt("abc", "abc") == ("", "")
    assert aa._ausschnitt("", "Neu") == ("", "Neu")


def test_html_maengel():
    assert aa._html_maengel("<p>a<br>b <strong>c</strong></p>") == 0
    assert aa._html_maengel("<p>a <strong>b</p>") == 1
    assert aa._html_maengel("a</li>") == 1


def test_texte_laden_vorschau_sichern(api):
    _shop(CAF)["produkte"][20]["short_description"] = "<p>Kappe mit Logo</p>"
    _shop(CAF)["produkte"][20]["description"] = LANG
    erg = api.artikel_laden("caf")
    cap = next(z for z in erg["artikel"] if z["id"] == 20)
    assert cap["kurz"] == "<p>Kappe mit Logo</p>" and cap["text"] == LANG
    neu_lang = LANG.replace("60 Grad", "40 Grad")
    v = _aendern(api, (20, "kurz", "<p>Kappe mit Stick</p>"), (20, "text", neu_lang))
    assert v["fehler"] == [] and v["vorschau_id"]
    zeilen = {z["feld"]: z for z in v["shops"][0]["zeilen"]}
    assert zeilen["kurz"]["alt"] == "<p>Kappe mit Logo</p>" and zeilen["kurz"]["text"] is True
    assert zeilen["text"]["neu"].endswith("40 Grad.</p>") and len(zeilen["text"]["neu"]) < 80
    s = api.artikel_sichern(v["vorschau_id"])
    assert s["gesichert"] == 2
    assert ArtikelWoo.posts[0][1] == {"update": [{"id": 20, "short_description": "<p>Kappe mit Stick</p>",
                                                  "description": neu_lang}]}
    verlauf = api._p_verlauf.read_text(encoding="utf-8")
    assert "Beschreibung „…" in verlauf and "60 Grad.</p>“ → „…" in verlauf
    assert "sehr angenehm zu tragen. sehr angenehm" not in verlauf        # nicht der ganze Text
    # Rücknahme stellt den vollständigen Text wieder her
    api.artikel_zuruecknehmen(api._letzte_info()["datei"], True)
    assert _shop(CAF)["produkte"][20]["description"] == LANG


@pytest.mark.parametrize("wert, text", [
    ('<p onclick="x()">Hi</p>', "enthält Skript-Code"),
    ("<script>alert(1)</script>", "enthält Skript-Code"),
    ('<a href="javascript:x">a</a>', "enthält Skript-Code"),
])
def test_texte_ohne_skripte(api, wert, text):
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "text", wert))
    assert erg["vorschau_id"] is None and text in erg["fehler"][0]


def test_texte_nur_am_hauptartikel(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (11, "kurz", "Hallo"))
    assert erg["fehler"] == ["042/POLO-M Poloshirt (M): Texte werden am Hauptartikel gepflegt."]


def test_texte_warnung_bei_kaputtem_html(api):
    _shop(CAF)["produkte"][30]["description"] = "<p>alt <b>kaputt</p>"      # schon vorher 1 Mangel
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "text", "<p>Neu <strong>fett</p>"), (30, "text", "<p>neu <b>kaputt</p>"))
    assert erg["vorschau_id"]
    assert erg["warnungen"] == ["042/CAP Cap: Beschreibung — HTML-Tags nicht sauber geschlossen, im Shop prüfen."]


def test_texte_leerzeichen_bleiben(api):
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "kurz", "  Text mit Einzug\n"))
    api.artikel_sichern(erg["vorschau_id"])
    assert _shop(CAF)["produkte"][20]["short_description"] == "  Text mit Einzug\n"


# --- Bilder (Welle 10c) --------------------------------------------------------

def _bild(bid, name):
    return {"id": bid, "src": f"https://shop.example/wp-content/uploads/{name}.jpg", "name": name}


@pytest.fixture
def mit_bildern(api):
    medien = {501: _bild(501, "cap-vorne"), 502: _bild(502, "cap-hinten"),
              503: _bild(503, "polo-rot"), 504: _bild(504, "polo-blau")}
    k = _shop(CAF)
    k["medien"] = medien
    k["produkte"][20]["images"] = [medien[501], medien[502]]
    k["produkte"][10]["images"] = [medien[503], medien[504]]
    k["varianten"][10][11]["image"] = medien[503]
    k["varianten"][10][12]["image"] = None
    return api


def test_bilder_laden_mit_vorrat(mit_bildern):
    erg = mit_bildern.artikel_laden("caf")
    z = {r["id"]: r for r in erg["artikel"]}
    assert z[20]["bilder"] == [501, 502] and z[11]["bilder"] == [503] and z[12]["bilder"] == []
    assert erg["bildvorrat"][503] == {"src": "https://shop.example/wp-content/uploads/polo-rot.jpg",
                                      "name": "polo-rot"}
    assert set(erg["bildvorrat"]) == {501, 502, 503, 504}


def test_bilder_reihenfolge_hauptbild_entfernen(mit_bildern):
    api = mit_bildern
    api.artikel_laden("caf")
    erg = _aendern(api, (20, "bilder", [502, 501, 504]), (12, "bilder", [504]), (11, "bilder", []))
    assert erg["fehler"] == [] and erg["vorschau_id"]
    zeilen = {z["id"]: z for z in erg["shops"][0]["zeilen"]}
    assert zeilen[20]["alt"] == "2: Hauptbild cap-vorne, dazu cap-hinten"
    assert zeilen[20]["neu"] == "3: Hauptbild cap-hinten, dazu cap-vorne, polo-blau"
    assert zeilen[11]["neu"] == "keins" and zeilen[12]["neu"] == "polo-blau"
    assert erg["warnungen"] == []                      # Variante ohne eigenes Bild ist normal
    s = api.artikel_sichern(erg["vorschau_id"])
    assert s["gesichert"] == 3
    posts = dict(ArtikelWoo.posts)
    assert posts["/products/batch"] == {"update": [{"id": 20, "images": [{"id": 502}, {"id": 501}, {"id": 504}]}]}
    assert sorted(posts["/products/10/variations/batch"]["update"], key=lambda d: d["id"]) == [
        {"id": 11, "image": {"id": 0}}, {"id": 12, "image": {"id": 504}}]
    assert [b["id"] for b in _shop(CAF)["produkte"][20]["images"]] == [502, 501, 504]
    assert "Bilder 2: Hauptbild cap-vorne, dazu cap-hinten → 3: Hauptbild cap-hinten" in \
        api._p_verlauf.read_text(encoding="utf-8")


def test_bild_per_adresse_und_ruecknahme(mit_bildern):
    api = mit_bildern
    api.artikel_laden("caf")
    url = "https://cdn.example/bilder/cap-seite.webp?v=2"
    erg = _aendern(api, (20, "bilder", [501, url]))
    assert erg["fehler"] == [] and erg["shops"][0]["zeilen"][0]["neu"] == \
        "2: Hauptbild cap-vorne, dazu cap-seite.webp"
    api.artikel_sichern(erg["vorschau_id"])
    neu_id = _shop(CAF)["produkte"][20]["images"][1]["id"]
    assert neu_id >= 9000
    datei = json.loads(next(api._p_artikel.glob("sammel_*.json")).read_text(encoding="utf-8"))
    assert datei["shops"][0]["zeilen"][0]["neu"] == [501, neu_id]    # id statt Adresse
    z = api.artikel_zuruecknehmen(api._letzte_info()["datei"], True)
    assert z["zurueck"] == 1 and z["ausgelassen"] == []
    assert [b["id"] for b in _shop(CAF)["produkte"][20]["images"]] == [501, 502]
    assert "Bilder 2: Hauptbild cap-vorne, dazu cap-seite.webp → 2: Hauptbild cap-vorne, dazu cap-hinten" \
        in api._p_verlauf.read_text(encoding="utf-8")


@pytest.mark.parametrize("aenderung, text", [
    ((12, "bilder", [503, 504]), "Eine Variante hat höchstens ein Bild"),
    ((20, "bilder", [777]), "Bild #777 ist im Shop nicht bekannt"),
    ((20, "bilder", ["http://x.example/a.jpg"]), "muss mit https:// beginnen"),
    ((20, "bilder", ["https://x.example/datei.pdf"]), "auf .jpg, .png, .webp oder .gif enden"),
    ((20, "bilder", [True]), "Bilder „[True]“ ist ungültig"),
    ((20, "bilder", list(range(501, 505)) + [f"https://x.example/{i}.jpg" for i in range(17)]),
     "Höchstens 20 Bilder"),
])
def test_bilder_fehler(mit_bildern, aenderung, text):
    mit_bildern.artikel_laden("caf")
    erg = _aendern(mit_bildern, aenderung)
    assert erg["vorschau_id"] is None and any(text in f for f in erg["fehler"]), erg["fehler"]


def test_bilder_warnung_ohne_bild(mit_bildern):
    mit_bildern.artikel_laden("caf")
    erg = _aendern(mit_bildern, (20, "bilder", []))
    assert erg["vorschau_id"] and erg["warnungen"] == ["042/CAP Cap: danach ohne Bild."]


def test_bilder_nicht_in_andere_shops(mit_bildern):
    mit_bildern.artikel_laden("caf")
    erg = _aendern(mit_bildern, (20, "bilder", [502]), andere=True)
    assert [s["name"] for s in erg["shops"]] == ["CAF-Shop"]


# --- Pflicht-Zubehör (Welle 10d) -------------------------------------------------

REGEL = "_cdh_required_accessories"


@pytest.fixture
def mit_zubehoer(api):
    k = _shop(CAF)
    k["produkte"][40] = _p(40, "Stick Logo", "004/STICK", "2.10", "5.50", "0")
    k["produkte"][41] = {**_p(41, "Druck Entwurf", "004/DRUCK", "1", "2", "0"), "status": "draft"}
    k["produkte"][42] = _p(42, "Stick ohne Nummer", "", "1", "2", "0")
    k["produkte"][10]["meta_data"] = [{"key": "_andere_meta", "value": "bleibt"}]
    k["varianten"][10][12]["meta_data"] = [{"key": REGEL, "value": [{"accessory_id": 40, "qty_per_unit": 2}]}]
    return api


def test_zubehoer_laden(mit_zubehoer):
    _shop(CAF)["produkte"][20]["meta_data"] = [
        {"key": REGEL, "value": {"a": {"accessory_id": 40, "qty_per_unit": "1"}}}]   # Plugin: Plätze A/B
    z = {r["id"]: r for r in mit_zubehoer.artikel_laden("caf")["artikel"]}
    assert z[20]["zubehoer"] == [{"id": 40, "menge": 1.0}]
    assert z[12]["zubehoer_eigen"] is True and z[11]["zubehoer_eigen"] is False
    assert z[12]["zubehoer"] == []                         # an Varianten nicht pflegbar


def test_zubehoer_setzen_sichern_zurueck(mit_zubehoer):
    api = mit_zubehoer
    api.artikel_laden("caf")
    erg = _aendern(api, (10, "zubehoer", [{"id": 40, "menge": "1,5"}]))
    assert erg["fehler"] == [] and erg["vorschau_id"]
    zeile = erg["shops"][0]["zeilen"][0]
    assert (zeile["feldname"], zeile["alt"], zeile["neu"]) == (
        "Pflicht-Zubehör", "keins", "004/STICK Stick Logo × 1,5")
    assert "042/POLO Poloshirt: Varianten mit eigener Regel (L) — dort gilt weiter deren Regel." \
        in erg["warnungen"]
    assert any("„Pflicht-Zubehör ergänzen“ für CAF-Shop aus" in t for t in erg["warnungen"])
    api.artikel_sichern(erg["vorschau_id"])
    assert ArtikelWoo.posts[0][1] == {"update": [{"id": 10, "meta_data": [
        {"key": REGEL, "value": [{"accessory_id": 40, "qty_per_unit": 1.5}]}]}]}
    meta = _shop(CAF)["produkte"][10]["meta_data"]
    assert {"key": "_andere_meta", "value": "bleibt"} in meta
    # Der Import liest dieselbe Regel
    assert w._zubehoer_regeln(_shop(CAF)["produkte"][10]) == [(40, 1.5)]
    assert "Pflicht-Zubehör keins → 004/STICK Stick Logo × 1,5" in api._p_verlauf.read_text(encoding="utf-8")
    api.artikel_zuruecknehmen(api._letzte_info()["datei"], True)
    assert w._zubehoer_regeln(_shop(CAF)["produkte"][10]) == []


@pytest.mark.parametrize("aenderung, text", [
    ((11, "zubehoer", [{"id": 40, "menge": 1}]), "wird am Hauptartikel gepflegt"),
    ((20, "zubehoer", [{"id": i, "menge": 1} for i in (40, 30, 43, 44, 45)]),
     "Höchstens 4 Zubehörartikel"),
    ((20, "zubehoer", [{"id": 20, "menge": 1}]), "nicht sein eigenes Zubehör"),
    ((20, "zubehoer", [{"id": 10, "menge": 1}]), "muss ein einfacher Artikel sein"),
    ((20, "zubehoer", [{"id": 41, "menge": 1}]), "ist nicht veröffentlicht"),
    ((20, "zubehoer", [{"id": 42, "menge": 1}]), "hat keine Artikelnummer"),
    ((20, "zubehoer", [{"id": 999, "menge": 1}]), "gibt es im Shop nicht"),
    ((20, "zubehoer", [{"id": 40, "menge": 0}]), "größer als 0"),
    ((20, "zubehoer", [{"id": 40, "menge": 1}, {"id": 40, "menge": 2}]), "doppelt"),
    ((20, "zubehoer", [{"id": 40, "menge": "x"}]), "ist ungültig"),
])
def test_zubehoer_pruefungen_wie_plugin(mit_zubehoer, aenderung, text):
    mit_zubehoer.artikel_laden("caf")
    erg = _aendern(mit_zubehoer, aenderung)
    assert erg["vorschau_id"] is None and any(text in f for f in erg["fehler"]), erg["fehler"]


def test_zubehoer_ohne_hinweis_wenn_im_tool_an(mit_zubehoer, tmp_path):
    einst = yaml.safe_load((tmp_path / "einstellungen.yaml").read_text(encoding="utf-8"))
    einst["shops"][0]["pflicht_zubehoer"] = True
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump(einst), encoding="utf-8")
    mit_zubehoer.artikel_laden("caf")
    erg = _aendern(mit_zubehoer, (20, "zubehoer", [{"id": 40, "menge": 1}]))
    assert erg["warnungen"] == [] and erg["vorschau_id"]


# --- Staffelpreise im Artikel-Tab (28.09.2026) ----------------------------------

STAFFEL = "_cdh_staffelpreise"


def test_staffel_laden_und_zubehoer_markiert(mit_zubehoer):
    _shop(CAF)["produkte"][40]["meta_data"] = [{"key": STAFFEL, "value": [{"ab": 10, "vk": "4,00", "ek": ""}]}]
    z = {r["id"]: r for r in mit_zubehoer.artikel_laden("caf")["artikel"]}
    assert z[40]["staffel"] == [{"ab": 10, "vk": 4.0, "ek": None}]
    assert z[40]["ist_zubehoer"] is True          # nur über die Regel einer Variante genutzt
    assert z[20]["ist_zubehoer"] is False and z[11]["staffel"] == []


def test_staffel_sichern_und_zurueck(mit_zubehoer):
    api = mit_zubehoer
    api.artikel_laden("caf")
    erg = _aendern(api, (40, "staffel", [{"ab": "10", "vk": "4,00", "ek": ""},
                                         {"ab": "1", "vk": "5", "ek": "2,10"},
                                         {"ab": "", "vk": "", "ek": ""}]))
    assert erg["fehler"] == [] and erg["vorschau_id"]
    z = erg["shops"][0]["zeilen"][0]
    assert (z["feldname"], z["alt"], z["neu"]) == (
        "Staffelpreise", "keine", "ab 1: VK 5,00, EK 2,10; ab 10: VK 4,00")
    api.artikel_sichern(erg["vorschau_id"])
    assert ArtikelWoo.posts[0][1] == {"update": [{"id": 40, "meta_data": [{"key": STAFFEL, "value": [
        {"ab": 1, "vk": 5.0, "ek": 2.1}, {"ab": 10, "vk": 4.0, "ek": None}]}]}]}
    # Der Import liest dieselbe Staffel
    assert w.staffel_lesen(_shop(CAF)["produkte"][40]) == [
        {"ab": 1, "vk": 5.0, "ek": 2.1}, {"ab": 10, "vk": 4.0, "ek": None}]
    api.artikel_zuruecknehmen(api._letzte_info()["datei"], True)
    assert w.staffel_lesen(_shop(CAF)["produkte"][40]) == []


@pytest.mark.parametrize("aenderung, text", [
    ((40, "staffel", [{"ab": 1, "vk": 5, "ek": 6}]), "Staffel — Zeile 1: EK (6.00) größer als VK (5.00)."),
    ((40, "staffel", [{"ab": 1, "vk": "", "ek": 1}]), "Staffel — Zeile 1: VK fehlt"),
    ((40, "staffel", [{"ab": 1, "vk": 5}, {"ab": 1, "vk": 4}]), "Staffel — Zeile 2: ab 1 doppelt."),
    ((10, "staffel", [{"ab": 1, "vk": 5}]), "Staffelpreise nur an einfachen Artikeln"),
    ((11, "staffel", [{"ab": 1, "vk": 5}]), "Staffelpreise nur an einfachen Artikeln"),
])
def test_staffel_pruefungen(mit_zubehoer, aenderung, text):
    mit_zubehoer.artikel_laden("caf")
    erg = _aendern(mit_zubehoer, aenderung)
    assert erg["vorschau_id"] is None and any(text in f for f in erg["fehler"]), erg["fehler"]


def test_staffel_fenster_findet_regeln_an_varianten(mit_zubehoer):
    """Einstellungen → Pflicht-Zubehör → Staffelpreise: der Stick hängt nur an
    einer Variante des Polos — er muss trotzdem in der Liste stehen."""
    erg = mit_zubehoer.zubehoer_artikel("caf")
    assert erg["ok"] and [a["sku"] for a in erg["artikel"]] == ["004/STICK"]


def test_staffel_standardstufen_ohne_preis_zaehlen_nicht(mit_zubehoer):
    """Tool und Plugin 2.6.1 belegen 1/10/25/50/100/250/500 vor — nur Stufen
    mit Preis werden gespeichert."""
    mit_zubehoer.artikel_laden("caf")
    zeilen = [{"ab": str(ab), "vk": "", "ek": ""} for ab in (1, 10, 25, 50, 100, 250, 500)]
    zeilen[0]["vk"], zeilen[3]["vk"] = "5,00", "3,50"
    erg = _aendern(mit_zubehoer, (40, "staffel", zeilen))
    assert erg["fehler"] == [] and erg["shops"][0]["zeilen"][0]["neu"] == "ab 1: VK 5,00; ab 50: VK 3,50"
    # Sieben Stufen mit Preis gehen auch
    for z, vk in zip(zeilen, ("5", "4.8", "4.5", "4.2", "4", "3.8", "3.5")):
        z["vk"] = vk
    erg = _aendern(mit_zubehoer, (40, "staffel", zeilen))
    assert erg["fehler"] == [] and erg["shops"][0]["zeilen"][0]["neu"].count("ab ") == 7


# --- Vier Plätze A–D (Plugin 2.7, 28.09.2026) -------------------------------------

@pytest.fixture
def vier(mit_zubehoer):
    k = _shop(CAF)["produkte"]
    k[43] = _p(43, "Druck Logo", "005/DRUCK", "1.50", "3.00", "0")
    k[44] = _p(44, "Transfer", "005/TRANSFER", "0.40", "0.80", "0")
    return mit_zubehoer


def test_vier_plaetze_stick_druck_transfer(vier):
    vier.artikel_laden("caf")
    regel = [{"id": 40, "menge": 1}, {"id": 43, "menge": 1}, {"id": 44, "menge": 1}]
    erg = _aendern(vier, (20, "zubehoer", regel))
    assert erg["fehler"] == [] and erg["vorschau_id"]
    vier.artikel_sichern(erg["vorschau_id"])
    assert w._zubehoer_regeln(_shop(CAF)["produkte"][20]) == [(40, 1.0), (43, 1.0), (44, 1.0)]


@pytest.mark.parametrize("version, text", [
    ("2.6.1", "erst mit Plugin 2.7 — im Shop läuft 2.6.1"),
    (None, "die Version im Shop ist nicht lesbar"),
])
def test_mehr_als_zwei_nur_mit_plugin_27(vier, version, text):
    ArtikelWoo.plugin_version = version
    vier.artikel_laden("caf")
    erg = _aendern(vier, (20, "zubehoer", [{"id": 40, "menge": 1}, {"id": 43, "menge": 1},
                                          {"id": 44, "menge": 1}]))
    assert erg["vorschau_id"] is None and any(text in f for f in erg["fehler"]), erg["fehler"]
    # Zwei gehen auch mit altem Plugin
    erg = _aendern(vier, (20, "zubehoer", [{"id": 40, "menge": 1}, {"id": 43, "menge": 1}]))
    assert erg["vorschau_id"]


def test_zubehoer_am_zubehoer_erlaubt_kreislauf_nicht(vier):
    _shop(CAF)["produkte"][43]["meta_data"] = [
        {"key": REGEL, "value": [{"accessory_id": 44, "qty_per_unit": 1}]}]   # Druck → Transfer
    vier.artikel_laden("caf")
    erg = _aendern(vier, (20, "zubehoer", [{"id": 43, "menge": 1}]))       # Cap → Druck (→ Transfer)
    assert erg["fehler"] == [] and erg["vorschau_id"]
    assert not any("Ebenen" in t or "Kreislauf" in t for t in erg["warnungen"])
    erg = _aendern(vier, (44, "zubehoer", [{"id": 43, "menge": 1}]))       # Transfer → Druck: Kreis
    assert erg["vorschau_id"] is None
    assert erg["fehler"] == ["005/TRANSFER Transfer: Kreislauf beim Zubehör "
                             "(005/TRANSFER Transfer → 005/DRUCK Druck Logo → 005/TRANSFER Transfer)."]


def test_ppom_veredelungsartikel_gelten_als_zubehoer(api, tmp_path):
    einst = yaml.safe_load((tmp_path / "einstellungen.yaml").read_text(encoding="utf-8"))
    einst["shops"][0]["ppom_veredelung"] = [{"feld": "Zusatzoptionen", "option": "Pin", "artikel": "042/PIN"}]
    (tmp_path / "einstellungen.yaml").write_text(yaml.safe_dump(einst), encoding="utf-8")
    z = {r["id"]: r for r in api.artikel_laden("caf")["artikel"]}
    assert z[30]["ist_zubehoer"] is True and z[20]["ist_zubehoer"] is False
