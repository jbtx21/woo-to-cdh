"""Welle 5: Einstellungen-Schnittstelle der Oberfläche (einstellungen_api).

Alles gegen Dateien in tmp_path, eine gestellte Uhr für den Admin-Modus.
Fake-Keys bewusst nicht hex, damit der Schlüssel-Scanner nicht anschlägt.
"""
import json

import pytest
import yaml

import einstellungen_api as ea
import migrate_config as m
import woo_to_cdh as w

EINST = {
    "cdh_import_folder": "wex",
    "veredelung_prefixes": ["004/", "316/", "234/"],
    "order_no_with_name": True,
    "status_after_export": "completed",
    "shops": [
        {"id": "caf", "name": "CAF-Shop", "enabled": True, "url": "https://shop.example/caf-shop/",
         "datev_no": 19541, "order_type": "AB"},
        {"name": "Ensinger-Shop", "enabled": True, "url": "https://shop.example/ensinger-shop/",
         "datev_no": 14020, "order_type": "AB", "import_on_days": [1],
         "combine_by_delivery": True, "aggregate_all_positions": True,
         "extra_excel_meta": [{"key": "personalnummer", "label": "Personalnummer"}, "teambestellung"]},
    ],
}
ADRESSEN = {
    "Ensinger-Shop": {"Cham": {"name1": "Beispiel GmbH", "name2": "z. Hd. Empfang",
                               "street": "Werkweg 1", "postcode": "93413", "city": "Cham",
                               "country": "DE"}},
    "Anderer-Shop": {"Nord": {"name1": "X", "street": "Y 1", "postcode": "1", "city": "Z"}},
}
PASSWORT = "richtig-geheim"


class Uhr:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def ordner(tmp_path):
    (tmp_path / "einstellungen.yaml").write_text(
        yaml.safe_dump(EINST, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (tmp_path / "lieferadressen.yaml").write_text(
        yaml.safe_dump(ADRESSEN, allow_unicode=True, sort_keys=False), encoding="utf-8")
    zugang = {"admin": {"password": m.hash_admin_password(PASSWORT, iterations=1000),
                        "users": []},
              "shops": {"caf": {"consumer_key": "ck_TEST_CAF", "consumer_secret": "cs_TEST_CAF"},
                        "Ensinger-Shop": {"consumer_key": "ck_TEST_ENS",
                                          "consumer_secret": "cs_TEST_ENS"}}}
    (tmp_path / "zugang.yaml").write_text(yaml.safe_dump(zugang), encoding="utf-8")
    return tmp_path


@pytest.fixture
def uhr():
    return Uhr()


@pytest.fixture
def api(ordner, uhr):
    return ea.EinstellungenApi(ordner, benutzer="m.mueller", uhr=uhr)


def _cfg(ordner):
    return yaml.safe_load((ordner / "einstellungen.yaml").read_text(encoding="utf-8"))


def _shop(stand, sid):
    return next(s for s in stand["shops"] if s["id"] == sid)


def _sichern(api, stand, **kw):
    return api.sichern({"shops": stand["shops"], "global": stand["global"],
                        "token": stand["token"], **kw})


# --- laden ------------------------------------------------------------------

def test_laden_modell_wie_entwurf(api):
    st = api.laden()
    assert st["ok"] and st["user"] == "m.mueller" and st["log"] == []
    caf, ens = _shop(st, "caf"), _shop(st, "ensinger")
    assert caf == {**caf, "name": "CAF-Shop", "url": "shop.example/caf-shop/",
                   "debitor": "19541", "active": True, "statuses": ["processing", "on-hold"],
                   "after": "completed", "days": [], "nameInNo": True,
                   "bundling": "einzeln", "orte": [], "unknownOrt": "cdh", "zugang": True}
    assert ens["bundling"] == "voll" and ens["days"] == [1]
    assert ens["excel"] == [{"key": "personalnummer", "label": "Personalnummer"},
                            {"key": "teambestellung", "label": "teambestellung"}]
    assert ens["orte"] == [{"ort": "Cham", "name1": "Beispiel GmbH", "name2": "z. Hd. Empfang",
                            "street": "Werkweg 1", "plz": "93413", "city": "Cham", "country": "DE"}]
    assert ens["zugang"] is True           # über den Namen gefunden (alte zugang.yaml)
    assert st["global"]["prefixes"] == [{"prefix": "004/", "label": "Stick"},
                                        {"prefix": "316/", "label": "Stick"},
                                        {"prefix": "234/", "label": "Silberreflex"}]
    assert st["adminPasswortGesetzt"] and not st["admin"]["aktiv"]


def test_laden_ohne_zugangsdaten(api):
    text = json.dumps(api.laden())
    assert "ck_TEST" not in text and "cs_TEST" not in text and "hash" not in text.lower().replace(
        "adminpasswortgesetzt", "")


def test_laden_ohne_einstellungen(tmp_path):
    st = ea.EinstellungenApi(tmp_path).laden()
    assert not st["ok"] and "migrate_config" in st["fehler"]


# --- sichern: normale Felder ------------------------------------------------

def test_ohne_aenderung_nichts_geschrieben(api, ordner):
    vorher = (ordner / "einstellungen.yaml").read_bytes()
    st = api.laden()
    _shop(st, "caf")["methods"] = ["Standardversand"]      # nur Anzeige
    erg = _sichern(api, st)
    assert erg["ok"] and erg["gesichert"] == []
    assert (ordner / "einstellungen.yaml").read_bytes() == vorher
    assert not (ordner / "Backup").exists()


def test_aenderungen_sichern_mit_backup_und_verlauf(api, ordner):
    st = api.laden()
    caf = _shop(st, "caf")
    caf.update(days=[1, 15], after="", nameInNo=False, bundling="trenn",
               unknownOrt="sperren", statuses=["processing"], active=False,
               excelSum=True, excelSumBy="gesamt", excelSumVed=False,
               excel=[{"key": "kst", "label": "Kostenstelle"}])
    erg = _sichern(api, st)
    assert erg["ok"], erg
    assert erg["gesichert"] == [
        "CAF: ausgeschaltet",
        "CAF: Bestellungen „In Bearbeitung“",
        "CAF: Import nur am 1., 15. des Monats",
        "CAF: nach dem Import Status nicht ändern",
        "CAF: Bündelung auf „Mit Trennzeilen“",
        "CAF: Name in Bestellnummer aus",
        "CAF: Lieferort ohne feste Adresse → Import sperren",
        "CAF: Summenblatt gesamt, Veredelungen ohne",
        "CAF: Zusatzfelder in der Excel Kostenstelle",
        "Feste Shop-ids ergänzt",
    ]
    shop = _cfg(ordner)["shops"][0]
    assert shop == {**EINST["shops"][0], "enabled": False, "included_statuses": ["processing"],
                    "import_on_days": [1, 15], "status_after_export": "",
                    "order_no_with_name": False, "combine_by_delivery": True,
                    "extra_excel_meta": [{"key": "kst", "label": "Kostenstelle"}],
                    "unknown_delivery": "sperren", "excel_summary": True,
                    "excel_summary_by": "gesamt", "excel_summary_veredelungen": False}
    # Bedeutung im Import stimmt
    assert w._status_after_export(shop, _cfg(ordner)) == ""
    # Ensinger unverändert (nur die fehlende id ist dazugekommen)
    ens = _cfg(ordner)["shops"][1]
    assert ens.pop("id") == "ensinger" and ens == EINST["shops"][1]
    assert len(list((ordner / "Backup").glob("einstellungen_*.yaml"))) == 1
    log = api.laden()["log"]
    assert log[0]["who"] == "m.mueller" and log[0]["what"] == "Feste Shop-ids ergänzt"
    assert any(l["what"] == "CAF: ausgeschaltet" for l in log)
    # Neu geladen zeigt die Oberfläche den gesicherten Stand
    assert _ohne(_shop(erg, "caf")) == _ohne(caf)


def _ohne(s):
    return {k: v for k, v in s.items() if k not in ea.NUR_ANZEIGE}


def test_zugang_nach_ids_weiter_gefunden(api, ordner):
    st = api.laden()
    _shop(st, "ensinger")["active"] = False
    assert _sichern(api, st)["ok"]
    cfg, _ = w.load_config(ordner)
    assert cfg["shops"][1]["id"] == "ensinger"
    assert cfg["shops"][1]["consumer_key"] == "ck_TEST_ENS"   # Rückfall über den Namen


def test_bündelung_einzeln_und_voll(api, ordner):
    st = api.laden()
    _shop(st, "ensinger")["bundling"] = "einzeln"
    st = _sichern(api, st)
    ens = _cfg(ordner)["shops"][1]
    assert "combine_by_delivery" not in ens and "aggregate_all_positions" not in ens
    _shop(st, "ensinger")["bundling"] = "voll"
    st = _sichern(api, st)
    ens = _cfg(ordner)["shops"][1]
    assert ens["combine_by_delivery"] and ens["aggregate_all_positions"]
    assert _shop(st, "ensinger")["unknownOrt"] == "cdh"


def test_lieferadressen_anlegen_aendern_loeschen(api, ordner):
    st = api.laden()
    ens = _shop(st, "ensinger")
    ens["orte"][0]["street"] = "Werkweg 2"
    ens["orte"].append({"ort": "Garbsen", "name1": "Beispiel GmbH", "name2": "",
                        "street": "Uniweg 2", "plz": "30823", "city": "Garbsen", "country": ""})
    erg = _sichern(api, st)
    assert erg["gesichert"] == ["Ensinger: Lieferadresse Cham geändert",
                                "Ensinger: Lieferadresse Garbsen angelegt", "Feste Shop-ids ergänzt"]
    adr = yaml.safe_load((ordner / "lieferadressen.yaml").read_text(encoding="utf-8"))
    assert adr["Ensinger-Shop"]["Garbsen"] == {"name1": "Beispiel GmbH", "name2": "",
                                              "street": "Uniweg 2", "postcode": "30823",
                                              "city": "Garbsen", "country": "DE"}
    assert adr["Anderer-Shop"] == ADRESSEN["Anderer-Shop"]        # fremde Einträge bleiben
    assert list((ordner / "Backup").glob("lieferadressen_*.yaml"))
    _shop(erg, "ensinger")["orte"] = []
    erg = _sichern(api, erg)
    assert "Ensinger: Lieferadresse Cham gelöscht" in erg["gesichert"]
    adr = yaml.safe_load((ordner / "lieferadressen.yaml").read_text(encoding="utf-8"))
    assert "Ensinger-Shop" not in adr and "Anderer-Shop" in adr


@pytest.mark.parametrize("aenderung, meldung", [
    ({"statuses": []}, "mindestens ein Status"),
    ({"days": [32]}, "Importtage"),
    ({"bundling": "quer"}, "Bündelung"),
    ({"unknownOrt": "irgendwo"}, "Lieferort ohne Adresse"),
    ({"after": "processing"}, "nach dem Import"),
    ({"orte": [{"ort": "Cham", "name1": "", "street": "", "plz": "1", "city": "C"}]},
     "Firma, Straße fehlt"),
    ({"orte": [{"ort": "A", "name1": "a", "street": "s", "plz": "1", "city": "c"},
               {"ort": "a ", "name1": "a", "street": "s", "plz": "1", "city": "c"}]}, "doppelt"),
    ({"excel": [{"key": "", "label": "x"}]}, "Zusatzfeld"),
])
def test_pruefung(api, ordner, aenderung, meldung):
    vorher = (ordner / "einstellungen.yaml").read_bytes()
    st = api.laden()
    _shop(st, "ensinger").update(aenderung)
    erg = _sichern(api, st)
    assert not erg["ok"] and meldung in erg["fehler"]
    assert (ordner / "einstellungen.yaml").read_bytes() == vorher


def test_konflikt_anderer_rechner(api, ordner):
    st = api.laden()
    p = ordner / "einstellungen.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\n# anderer Rechner\n", encoding="utf-8")
    _shop(st, "caf")["active"] = False
    erg = _sichern(api, st)
    assert not erg["ok"] and "anderen Rechner" in erg["fehler"]
    assert "# anderer Rechner" in p.read_text(encoding="utf-8")


def test_shops_hinzufuegen_entfernen_abgelehnt(api):
    st = api.laden()
    st["shops"] = st["shops"][:1]
    erg = _sichern(api, st)
    assert not erg["ok"] and "Shop-Assistent" in erg["fehler"]


# --- geschützte Felder und Admin-Modus --------------------------------------

def test_debitor_nur_im_admin_modus(api, ordner, uhr):
    st = api.laden()
    _shop(st, "caf")["debitor"] = "19542"
    erg = _sichern(api, st)
    assert not erg["ok"] and "Admin-Modus nötig für: CAF-Shop: Debitornummer" in erg["fehler"]
    assert _cfg(ordner)["shops"][0]["datev_no"] == 19541

    assert api.admin_anmelden(PASSWORT)["ok"]
    erg = _sichern(api, st)
    assert erg["ok"] and "CAF: Debitornummer 19541 → 19542" in erg["gesichert"]
    assert _cfg(ordner)["shops"][0]["datev_no"] == 19542


def test_admin_laeuft_nach_10_minuten_ab(api, uhr):
    assert api.admin_anmelden(PASSWORT)["rest_minuten"] == 10
    uhr.t += 9 * 60 + 59
    assert api.admin_status()["aktiv"]
    uhr.t += 2
    assert not api.admin_status()["aktiv"]
    st = api.laden()
    _shop(st, "caf")["debitor"] = "11111"
    assert not _sichern(api, st)["ok"]


def test_praefixe_nur_im_admin_modus(api, ordner):
    st = api.laden()
    st["global"]["prefixes"].append({"prefix": "006/", "label": "Transferdruck"})
    st["global"]["prefixes"][0]["label"] = "Stick klein"
    assert "Veredelungen" in _sichern(api, st)["fehler"]
    api.admin_anmelden(PASSWORT)
    erg = _sichern(api, st)
    assert erg["gesichert"] == ["Veredelungen: 004/ heißt jetzt Stick klein",
                                "Veredelungen: 006/ Transferdruck ergänzt",
                                "Feste Shop-ids ergänzt"]
    assert _cfg(ordner)["veredelung_prefixes"][-1] == {"prefix": "006/", "label": "Transferdruck"}


def test_praefix_doppelt(api):
    api.admin_anmelden(PASSWORT)
    st = api.laden()
    st["global"]["prefixes"].append({"prefix": "004/", "label": "nochmal"})
    assert "Präfix doppelt" in _sichern(api, st)["fehler"]


def test_falsches_passwort_und_pause(api, uhr):
    for i in range(1, 5):
        erg = api.admin_anmelden("falsch")
        assert not erg["ok"]
        assert ("Nach 5 Versuchen" in erg["fehler"]) == (i >= 3)
    assert "1 Minute Pause" in api.admin_anmelden("falsch")["fehler"]
    assert "warten" in api.admin_anmelden(PASSWORT)["fehler"]      # auch richtig: Pause
    uhr.t += 61
    assert api.admin_anmelden(PASSWORT)["ok"]


def test_admin_abmelden(api):
    api.admin_anmelden(PASSWORT)
    assert not api.admin_abmelden()["aktiv"]


def test_kein_passwort_gesetzt(ordner, uhr):
    z = yaml.safe_load((ordner / "zugang.yaml").read_text(encoding="utf-8"))
    z["admin"]["password"] = m.leerer_passwort_satz()
    (ordner / "zugang.yaml").write_text(yaml.safe_dump(z), encoding="utf-8")
    api = ea.EinstellungenApi(ordner, benutzer="x", uhr=uhr)
    assert not api.laden()["adminPasswortGesetzt"]
    assert "Kein Admin-Passwort" in api.admin_anmelden("egal")["fehler"]


def test_admin_nur_fuer_eingetragene_benutzer(ordner, uhr):
    z = yaml.safe_load((ordner / "zugang.yaml").read_text(encoding="utf-8"))
    z["admin"]["users"] = ["J.Boekle"]
    (ordner / "zugang.yaml").write_text(yaml.safe_dump(z), encoding="utf-8")
    assert "nicht als Admin" in ea.EinstellungenApi(
        ordner, benutzer="m.mueller", uhr=uhr).admin_anmelden(PASSWORT)["fehler"]
    assert ea.EinstellungenApi(ordner, benutzer="j.boekle", uhr=uhr).admin_anmelden(PASSWORT)["ok"]


# --- Versandarten -----------------------------------------------------------

def test_versandarten(api, ordner):
    class Client:
        def __init__(self, url, key, secret, **kw):
            assert key == "ck_TEST_CAF"
        def get_shipping_methods(self):
            return ["Standardversand", "Abholung"]
    api._client_factory = Client
    assert api.versandarten("caf") == {"ok": True, "methoden": ["Standardversand", "Abholung"]}
    assert not api.versandarten("gibtsnicht")["ok"]


def test_versandarten_fehler_ohne_details(api):
    class Client:
        def __init__(self, *a, **k):
            pass
        def get_shipping_methods(self):
            raise RuntimeError("https://x?consumer_key=ck_TEST_CAF 401")
    api._client_factory = Client
    erg = api.versandarten("caf")
    assert not erg["ok"] and "ck_TEST" not in erg["fehler"]


# --- nach außen nur öffentliche Methoden -------------------------------------

def test_oeffentliche_methoden():
    namen = {n for n in dir(ea.EinstellungenApi) if not n.startswith("_")}
    assert namen == {"laden", "sichern", "admin_anmelden", "admin_abmelden",
                     "admin_status", "versandarten"}
