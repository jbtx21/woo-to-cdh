"""
TEXMA — Shop anlegen, Zugang erneuern, Altbestellungen (Welle 7)
================================================================

Ergänzt die Einstellungen-Schnittstelle um die Admin-Funktionen des
Shop-Assistenten:

  shop_pruefen(daten)          Grunddaten + Schlüssel prüfen, Diagnose (nur lesend),
                               offene Bestellungen zählen
  shop_anlegen(daten)          Shop ausgeschaltet anlegen, Schlüssel in zugang.yaml
  zugang_erneuern(id, k, s)    neue Schlüssel erst prüfen, dann ersetzen
  altbestellungen(id)          offene Bestellungen eines ausgeschalteten Shops
  altbestellungen_abschliessen(id, ids, bestaetigt)
                               Sammel-Statuswechsel auf „Abgeschlossen" — nicht
                               umkehrbar, nur mit ausdrücklicher Bestätigung

Alles nur im Admin-Modus; die Prüfung sitzt hier, nicht im Fenster.
Schlüssel gehen nie zurück an die Oberfläche und nie ins Log oder in den
Änderungsverlauf. Nur Namen ohne führenden Unterstrich sind für die
Oberfläche sichtbar.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from datetime import datetime

import requests

import diagnose
import woo_to_cdh as w
from einstellungen_api import EinstellungenApi, _Fehler, _schreibe_atomar, _yaml

ALT_STATUS = "completed"
ZUGANG_KOPF = ("# Zugangsdaten WooCommerce → CDH — NUR Geheimnisse.\n"
               "# Gepflegt über die Oberfläche (Admin). Nie in Chats, Tickets oder "
               "Konsolen teilen (README §15).\n\n")
FEHLER_STATUS = {401: "Schlüssel falsch, im falschen Sub-Shop erzeugt oder ohne "
                      "Lesen/Schreiben-Rechte.",
                 403: "Schlüssel ohne ausreichende Rechte.",
                 404: "Shop-Adresse stimmt nicht (URL prüfen)."}


def _fingerabdruck(url: str, key: str, secret: str) -> str:
    return hashlib.sha256(f"{url}\0{key}\0{secret}".encode("utf-8")).hexdigest()


def _url_norm(url: str) -> str:
    return re.sub(r"^https?://", "", str(url or "").strip().lower()).rstrip("/")


def _nummer(o: dict) -> str:
    return str(o.get("number") or o.get("id") or "")


class ShopApi(EinstellungenApi):

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._geprueft: str | None = None    # Fingerabdruck der zuletzt geprüften Schlüssel

    # --- intern -------------------------------------------------------------
    def _admin_noetig(self, was: str) -> None:
        if not self._ist_admin():
            raise _Fehler(f"{was} braucht den Admin-Modus.")

    def _client(self, url: str, key: str, secret: str, statuses=None):
        return (self._client_factory or w.WooClient)(url, key, secret, statuses=statuses)

    def _abruf(self, client):
        """abruf(path, params) -> (status, json) für diagnose.diagnose()."""
        def abruf(path, params=None):
            try:
                return 200, client._get(path, params)
            except requests.HTTPError as e:
                return (e.response.status_code if e.response is not None else 0), None
            except Exception as e:  # noqa: BLE001
                return 0, w.ohne_schluessel(e)
        return abruf

    def _verbindung(self, client) -> None:
        status, daten = self._abruf(client)("/orders", {"per_page": 1})
        if status != 200:
            raise _Fehler(FEHLER_STATUS.get(status) or
                          (f"Keine Verbindung ({daten})." if status == 0
                           else f"Unerwarteter Status {status}."))

    def _offene(self, client) -> list[dict]:
        """Alle Bestellungen im Statusfilter des Shops (ohne Export-Filter)."""
        out, seite = [], 1
        while True:
            teil = client._get("/orders", {"status": ",".join(client.statuses),
                                           "per_page": 100, "page": seite,
                                           "orderby": "date", "order": "asc"})
            out += teil or []
            if not teil or len(teil) < 100:
                return out
            seite += 1

    @staticmethod
    def _schluessel(daten: dict) -> tuple[str, str]:
        key = str(daten.get("key") or "").strip()
        secret = str(daten.get("secret") or "").strip()
        if not key.startswith("ck_") or not secret.startswith("cs_"):
            raise _Fehler("Schlüssel beginnen mit ck_ und cs_.")
        return key, secret

    def _grunddaten(self, daten: dict) -> dict:
        cfg = self._lies(self._p_einst)
        shops = cfg.get("shops") or []
        name = str(daten.get("name") or "").strip()
        url = str(daten.get("url") or "").strip()
        debitor = str(daten.get("debitor") or "").strip()
        fehlt = []
        if not name:
            fehlt.append("Name")
        if not re.fullmatch(r"https://[^\s/]+/.*/", url):
            fehlt.append("Shop-Adresse (https://…/ mit / am Ende)")
        if not re.fullmatch(r"\d{4,6}", debitor):
            fehlt.append("Debitornummer (4–6 Ziffern)")
        if fehlt:
            raise _Fehler("Bitte prüfen: " + ", ".join(fehlt))
        for s in shops:
            if str(s.get("name") or "").strip().lower() == name.lower():
                raise _Fehler(f"Einen Shop „{name}“ gibt es schon.")
            if str(s.get("datev_no") or "") == debitor:
                raise _Fehler(f"Debitor {debitor} gehört schon zu {s.get('name')}.")
            if _url_norm(s.get("url")) == _url_norm(url):
                raise _Fehler(f"Diese Shop-Adresse gehört schon zu {s.get('name')}.")
        ids = {s.get("id") or w.shop_id_aus_name(s.get("name")) for s in shops}
        zugang_ids = set((self._lies(self._p_zugang).get("shops") or {}))
        basis = w.shop_id_aus_name(name)
        sid, n = basis, 2
        while sid in ids or sid in zugang_ids:
            sid, n = f"{basis}-{n}", n + 1
        return {"id": sid, "name": name, "url": url, "debitor": debitor}

    def _zugang_schreiben(self, schluessel: str, name: str, key: str, secret: str) -> None:
        """schluessel = feste Shop-id; bei Shops ohne id in einstellungen.yaml
        der Name, sonst fände der Lader den Zugang nicht (Rückfall vor Welle 5)."""
        zugang = self._lies(self._p_zugang)
        z_shops = zugang.get("shops") or {}
        zugang["shops"] = z_shops
        if schluessel != name:
            z_shops.pop(name, None)        # alter Eintrag nach Namen
        z_shops[schluessel] = {"consumer_key": key, "consumer_secret": secret}
        _schreibe_atomar(self._p_zugang, ZUGANG_KOPF + _yaml(zugang))

    def _shop_cfg(self, shop_id: str) -> dict:
        cfg, _ = w.load_config(self._base)
        shop = next((s for s in cfg.get("shops") or []
                     if (s.get("id") or w.shop_id_aus_name(s.get("name"))) == shop_id), None)
        if shop is None:
            raise _Fehler("Shop nicht gefunden.")
        return shop

    def _ausgeschalteter_client(self, shop_id: str):
        shop = self._shop_cfg(shop_id)
        if shop.get("enabled", True):
            raise _Fehler(f"{shop.get('name')} ist eingeschaltet. Altbestellungen "
                          "werden nur bei ausgeschalteten Shops abgeschlossen — "
                          "sonst gingen echte Bestellungen am Import vorbei.")
        if w.fehlende_zugangsdaten(shop):
            raise _Fehler("Kein Zugang hinterlegt.")
        return shop, self._client(shop["url"], shop["consumer_key"],
                                  shop["consumer_secret"], shop.get("included_statuses"))

    # --- für die Oberfläche -------------------------------------------------
    def shop_pruefen(self, daten: dict) -> dict:
        """Schritt „Prüfen" des Assistenten. Liest nur, schreibt nichts."""
        try:
            self._admin_noetig("Shop hinzufügen")
            daten = daten or {}
            g = self._grunddaten(daten)
            key, secret = self._schluessel(daten)
            client = self._client(g["url"], key, secret)
            punkte = diagnose.diagnose({"name": g["name"], "url": g["url"]},
                                       abruf=self._abruf(client))
            anzahl, nummern = 0, []
            ok = all(p.stufe != "fehler" for p in punkte)
            if ok:
                try:
                    offene = self._offene(client)
                    anzahl, nummern = len(offene), [_nummer(o) for o in offene]
                except Exception as e:  # noqa: BLE001
                    ok = False
                    punkte.append(diagnose.Pruefpunkt(
                        "Bestellungen lesbar", "fehler",
                        f"Offene Bestellungen nicht abrufbar ({type(e).__name__})"))
            self._geprueft = _fingerabdruck(g["url"], key, secret) if ok else None
            logging.info("Shop-Assistent: %s geprüft (%s), %d offene Bestellungen.",
                         g["name"], "ok" if ok else "Fehler", anzahl)
            return {"ok": True, "bestanden": ok, "id": g["id"],
                    "punkte": [{"titel": p.titel, "stufe": p.stufe,
                                "text": w.ohne_schluessel(p.text),
                                "details": [str(d) for d in p.details]} for p in punkte],
                    "offen": {"anzahl": anzahl, "nummern": nummern}}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}

    def shop_anlegen(self, daten: dict) -> dict:
        """Legt den geprüften Shop ausgeschaltet an. Altbestellungen gesondert."""
        try:
            self._admin_noetig("Shop hinzufügen")
            daten = daten or {}
            if daten.get("token") != self._token():
                raise _Fehler("Die Einstellungen wurden inzwischen geändert. "
                              "Bitte neu laden — der Shop ist nicht angelegt.")
            g = self._grunddaten(daten)
            key, secret = self._schluessel(daten)
            if self._geprueft != _fingerabdruck(g["url"], key, secret):
                raise _Fehler("Erst prüfen: Shop-Adresse oder Schlüssel wurden seit "
                              "der Prüfung geändert.")
            cfg = self._lies(self._p_einst)
            neu = {"id": g["id"], "name": g["name"], "enabled": False,
                   "url": g["url"], "datev_no": int(g["debitor"]), "order_type": "AB"}
            cfg.setdefault("shops", [])
            cfg["shops"] = (cfg["shops"] or []) + [neu]

            stempel = datetime.fromtimestamp(self._uhr()).strftime("%Y-%m-%d_%H%M%S")
            self._p_backup.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._p_einst, self._p_backup / f"einstellungen_{stempel}.yaml")
            # Erst der Zugang, dann der Shop: bricht es dazwischen ab, bleibt
            # nur ein ungenutzter Eintrag in zugang.yaml, kein Shop ohne Zugang.
            self._zugang_schreiben(g["id"], g["name"], key, secret)
            kopf = (f"# Einstellungen WooCommerce → CDH — gesichert über die Oberfläche\n"
                    f"# {datetime.fromtimestamp(self._uhr()):%d.%m.%Y %H:%M} von "
                    f"{self._benutzer}. Vorherige Fassung in Backup\\.\n\n")
            _schreibe_atomar(self._p_einst, kopf + _yaml(cfg))
            self._geprueft = None
            text = f"{g['name']}: Shop angelegt (Debitor {g['debitor']}), ausgeschaltet"
            self._verlauf_schreiben([text])
            logging.info("%s von %s.", text, self._benutzer)
            stand = self.laden()
            stand["neu_id"] = g["id"]
            return stand
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}

    def zugang_erneuern(self, shop_id: str, key: str, secret: str) -> dict:
        """Neue Schlüssel erst gegen den Shop prüfen, dann ersetzen."""
        try:
            self._admin_noetig("Zugang erneuern")
            key, secret = self._schluessel({"key": key, "secret": secret})
            cfg = self._lies(self._p_einst)
            shop = next((s for s in cfg.get("shops") or []
                         if (s.get("id") or w.shop_id_aus_name(s.get("name"))) == shop_id), None)
            if shop is None:
                raise _Fehler("Shop nicht gefunden.")
            self._verbindung(self._client(shop["url"], key, secret))
            self._zugang_schreiben(shop["id"] if shop.get("id") else shop.get("name"),
                                   shop.get("name"), key, secret)
            text = f"{shop.get('name')}: Zugang erneuert"
            self._verlauf_schreiben([text])
            logging.info("%s von %s.", text, self._benutzer)
            stand = self.laden()
            stand["hinweis"] = "Verbindung geprüft, Zugang gesichert. Den alten " \
                               "Schlüssel jetzt im Shop widerrufen."
            return stand
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}

    def altbestellungen(self, shop_id: str) -> dict:
        """Offene Bestellungen eines ausgeschalteten Shops — nur lesend."""
        try:
            self._admin_noetig("Altbestellungen abschließen")
            _, client = self._ausgeschalteter_client(shop_id)
            offene = self._offene(client)
            return {"ok": True, "anzahl": len(offene),
                    "bestellungen": [{"id": o.get("id"), "nummer": _nummer(o),
                                      "datum": str(o.get("date_created") or "")[:10]}
                                     for o in offene]}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "fehler": f"Nicht abrufbar ({type(e).__name__})."}

    def altbestellungen_abschliessen(self, shop_id: str, ids, bestaetigt=False) -> dict:
        """Setzt die angezeigten Bestellungen im Shop auf „Abgeschlossen".

        Nicht umkehrbar. Nur Bestellungen, die jetzt noch offen UND in der
        angezeigten Liste sind — was seit der Anzeige neu kam, bleibt liegen.
        """
        try:
            self._admin_noetig("Altbestellungen abschließen")
            if bestaetigt is not True:
                raise _Fehler("Ohne ausdrückliche Bestätigung wird nichts abgeschlossen.")
            gewaehlt = {int(i) for i in ids or []}
            if not gewaehlt:
                raise _Fehler("Keine Bestellungen ausgewählt.")
            shop, client = self._ausgeschalteter_client(shop_id)
            offene = self._offene(client)
            ziel = [o for o in offene if int(o.get("id") or 0) in gewaehlt]
            neu = [o for o in offene if int(o.get("id") or 0) not in gewaehlt]
            erledigt, fehler = [], []
            for o in ziel:
                try:
                    client.set_status(int(o["id"]), ALT_STATUS)
                    erledigt.append(_nummer(o))
                except Exception as e:  # noqa: BLE001
                    fehler.append(_nummer(o))
                    logging.error("Altbestellung %s (%s) nicht abgeschlossen: %s",
                                  _nummer(o), shop.get("name"), w.ohne_schluessel(e))
            if erledigt:
                text = (f"{shop.get('name')}: {len(erledigt)} Altbestellung"
                        f"{'en' if len(erledigt) != 1 else ''} abgeschlossen "
                        f"(Nr. {', '.join(erledigt)})")
                self._verlauf_schreiben([text])
                logging.info("%s von %s.", text, self._benutzer)
            return {"ok": True, "abgeschlossen": erledigt, "fehler_nummern": fehler,
                    "nicht_mehr_offen": len(gewaehlt) - len(ziel),
                    "neu_seitdem": [_nummer(o) for o in neu]}
        except _Fehler as e:
            return {"ok": False, "fehler": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "fehler": f"Nicht abrufbar ({type(e).__name__})."}
