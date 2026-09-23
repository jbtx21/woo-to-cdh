"""
TEXMA — WooCommerce → CDH Import Bridge
========================================

Holt neue Bestellungen aus einem oder mehreren WooCommerce-Shops per REST-API,
schreibt sie im CDH-Importformat als XLS in den CDH-Eingangsordner, startet den
CDH-Import, und markiert die Bestellungen in WooCommerce als exportiert.

Konfiguration: config.yaml (siehe config.sample.yaml)
Start:         python woo_to_cdh.py
Logs:          logs/woo_to_cdh.log

Autor:  für TEXMA Textilmarketing GmbH
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

try:
    import openpyxl  # nur für Excel-Export der Bestellungen (Kontrollausdruck)
    _HAS_OPENPYXL = True
except ImportError:
    openpyxl = None
    _HAS_OPENPYXL = False

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------

# Basisverzeichnis = Ordner, in dem die config.yaml und die logs/ liegen.
#
# Bei einem normalen Python-Lauf ist das der Ordner der .py-Datei.
# Bei einer PyInstaller-EXE ist sys.frozen gesetzt und sys.executable
# zeigt auf die EXE selbst — dann nehmen wir deren Ordner.
# So funktionieren Pfade auch über UNC-Pfade (\\SERVER\share\...) und
# wenn die EXE von irgendwo aus gestartet wird.
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.yaml"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Lock-Datei verhindert, dass zwei Läufe gleichzeitig starten.
# Liegt ebenfalls im Skript-Ordner, also auf dem Netzlaufwerk.
LOCK_PATH = BASE_DIR / "running.lock"
# Wenn eine Lock-Datei älter als diese Zeit ist, gilt sie als verwaist
# (Prozess vermutlich abgestürzt) und wird ignoriert.
LOCK_STALE_MINUTES = 10

# Meta-Key, mit dem wir in WooCommerce markieren, dass eine Bestellung
# bereits nach CDH exportiert wurde. Solange das Feld fehlt oder leer ist,
# gilt die Bestellung als "noch nicht exportiert".
EXPORT_META_KEY = "_cdh_exported_at"

# Lokales Export-Log als zweite Wahrheit gegen doppelte Importe.
# Format pro Zeile (TSV): ISO-Zeitstempel, Shop-Name, Bestellnr., WEX-Datei
# Wird VOR dem Aufruf von mark_exported() geschrieben, damit auch bei
# WooCommerce-Fehler keine doppelten Exports entstehen. Beim nächsten Lauf
# wird für jede API-Bestellung geprüft: steht sie schon im Log? Dann skip.
EXPORTED_LOG_PATH = BASE_DIR / "exported.log"

# Feste Lieferadressen je Shop und Lieferort. Eigene Datei, damit der
# Innendienst sie über das Adressen-Tool pflegen kann, ohne an die
# config.yaml mit den API-Schlüsseln zu müssen.
DELIVERY_ADDRESSES_PATH = BASE_DIR / "lieferadressen.yaml"

# Welle 2: Konfiguration ist in zwei Dateien geteilt — Einstellungen ohne
# Geheimnisse (Innendienst) und Zugangsdaten (Admin). Solange die neuen
# Dateien fehlen, wird auf die alte config.yaml zurückgefallen.
EINSTELLUNGEN_PATH = BASE_DIR / "einstellungen.yaml"
ZUGANG_PATH = BASE_DIR / "zugang.yaml"

# Bestellstatus, die wir exportieren (Default; pro Shop überschreibbar
# via included_statuses in config.yaml)
INCLUDED_STATUSES = ["processing", "on-hold"]

# Artikelnummern-Präfixe, die Veredelungspositionen markieren
# (Sticker, Druck etc.). Positionen mit diesen Präfixen werden innerhalb
# einer Bestellung zusammengefasst, wenn Artikelnummer und Preise identisch
# sind.
# Artikelnummern-Präfixe, die Veredelungspositionen markieren:
#   004/  Stick
#   316/  Druck
#   234/  Silberreflex
# Positionen mit diesen Präfixen werden zusammengefasst, wenn Artikelnummer
# und Preise identisch sind.
#
# Diese Liste ist der Standard und lässt sich in der config.yaml über
# "veredelung_prefixes" überschreiben — dann braucht ein neuer Präfix
# keinen EXE-Neubau.
VEREDELUNG_PREFIXES = ("004/", "316/", "234/")

# Welches Custom-Field einer Variation den EK enthält — in WooCommerce ist das
# die "Länge" (cm). Das VK-Feld ist die "Breite" (cm).
# Das entspricht genau dem Schema, das Jannik als Hack im Shop nutzt.
EK_FIELD = "length"   # WooCommerce: dimensions.length
VK_FIELD = "width"    # WooCommerce: dimensions.width


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO") -> None:
    log_file = LOG_DIR / "woo_to_cdh.log"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# WooCommerce API Client
# ---------------------------------------------------------------------------

class WooClient:
    """
    Dünner REST-API-Wrapper für WooCommerce.

    Auth via Query-Parameter (consumer_key / consumer_secret), NICHT via
    Basic Auth Header. Grund: Der IONOS-/Apache-Stack vor dem TEXMA-Shop
    strippt Authorization-Header, bevor sie PHP erreichen. Mit Query-Params
    kommen die Keys zuverlässig an.
    """

    def __init__(self, base_url: str, consumer_key: str, consumer_secret: str,
                 timeout: int = 30, statuses: list | None = None):
        # Basis-URL normalisieren: einmal /wp-json/wc/v3/ anhängen, egal ob
        # der Nutzer in der Config mit oder ohne trailing slash schreibt.
        base = base_url.rstrip("/")
        if not base.endswith("/wp-json/wc/v3"):
            base = base + "/wp-json/wc/v3"
        self.base = base
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.timeout = timeout
        # Erlaubt pro Shop eigene Statuslisten (siehe config.yaml).
        # Fällt auf den globalen Default zurück, wenn nichts angegeben ist.
        self.statuses = statuses if statuses else INCLUDED_STATUSES

    def _auth_params(self) -> dict:
        return {
            "consumer_key": self.consumer_key,
            "consumer_secret": self.consumer_secret,
        }

    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base}{path}"
        merged = {**(params or {}), **self._auth_params()}
        r = requests.get(url, params=merged, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, data: dict) -> Any:
        url = f"{self.base}{path}"
        r = requests.put(url, params=self._auth_params(), json=data,
                         timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def iter_new_orders(self, page_size: int = 50,
                         exported_locally: set | None = None):
        """
        Alle Bestellungen mit Status in self.statuses durchgehen und
        nur die liefern, die NICHT bereits als exportiert markiert sind.

        Prüft zwei Quellen:
          - WooCommerce-Meta-Feld (_cdh_exported_at), im API-Response direkt
          - Lokales exported.log (Set aus load_exported_log()), das auch
            frühere lokale Exporte kennt, selbst wenn mark_exported() damals
            fehlgeschlagen ist.
        """
        exported_locally = exported_locally or set()
        page = 1
        while True:
            orders = self._get("/orders", params={
                "status": ",".join(self.statuses),
                "per_page": page_size,
                "page": page,
                "orderby": "date",
                "order": "asc",
            })
            if not orders:
                return
            for o in orders:
                if self._is_exported(o):
                    continue
                # Lokales Log als zweite Wahrheit
                key = self._local_key(o)
                if key in exported_locally:
                    continue
                yield o
            if len(orders) < page_size:
                return
            page += 1

    @staticmethod
    def _is_exported(order: dict) -> bool:
        for m in order.get("meta_data", []):
            if m.get("key") == EXPORT_META_KEY and m.get("value"):
                return True
        return False

    @staticmethod
    def _local_key(order: dict) -> str:
        """Schlüssel für das lokale Log — an anderer Stelle wird derselbe
        Schlüssel geschrieben. Wir verwenden id + number, damit auch bei
        Zahl-Konflikten (theoretisch) nichts kollidiert."""
        return f"{order.get('id')}|{order.get('number')}"

    def get_variation(self, product_id: int, variation_id: int) -> dict:
        """Variant-Details inkl. dimensions (length, width, height) holen."""
        return self._get(f"/products/{product_id}/variations/{variation_id}")

    def get_product(self, product_id: int) -> dict:
        return self._get(f"/products/{product_id}")

    def mark_exported(self, order_id: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        self._put(f"/orders/{order_id}", {
            "meta_data": [{"key": EXPORT_META_KEY, "value": now}]
        })

    def set_status(self, order_id: int, status: str) -> None:
        """
        Setzt den Bestellstatus in WooCommerce.

        ACHTUNG: WooCommerce verschickt bei manchen Statuswechseln
        automatisch Kundenmails (z.B. "completed" → "Deine Bestellung ist
        abgeschlossen"). Wenn das nicht gewollt ist, entweder einen eigenen
        Status verwenden oder die Mail in WooCommerce deaktivieren.
        """
        self._put(f"/orders/{order_id}", {"status": status})


# ---------------------------------------------------------------------------
# Preis-Extraktion aus Variantendaten
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> float | None:
    """Handle WooCommerce-Strings wie "32,76" oder "32.76" oder leer."""
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_ek_vk(client: WooClient, line_item: dict,
                  price_cache: dict) -> tuple[float | None, float | None]:
    """
    Holt EK (aus length) und VK (aus width) des gekauften Produkts/Varianten.
    Nutzt einen Cache, damit wir dasselbe Produkt nicht 10x aus der API ziehen.
    """
    product_id = line_item.get("product_id")
    variation_id = line_item.get("variation_id") or 0

    cache_key = (product_id, variation_id)
    if cache_key in price_cache:
        return price_cache[cache_key]

    try:
        if variation_id:
            data = client.get_variation(product_id, variation_id)
        else:
            data = client.get_product(product_id)
    except requests.HTTPError as e:
        logging.warning(
            "Preisdaten für Produkt %s/%s nicht abrufbar: %s",
            product_id, variation_id, e,
        )
        price_cache[cache_key] = (None, None)
        return None, None

    dims = data.get("dimensions") or {}
    ek = _to_float(dims.get(EK_FIELD))
    vk = _to_float(dims.get(VK_FIELD))
    price_cache[cache_key] = (ek, vk)
    return ek, vk


# ---------------------------------------------------------------------------
# Bestell-Transformation (WooCommerce JSON → CDH Zeilen-Format)
# ---------------------------------------------------------------------------

class OrderBuildError(Exception):
    """Bestellung konnte nicht in das CDH-Format gebracht werden."""


def build_wex_data(order: dict, shop_cfg: dict, client: WooClient,
                   price_cache: dict) -> dict:
    """
    Wandelt eine WooCommerce-Bestellung in ein Python-Dict um, das dann
    als WEX-XML serialisiert wird. Die Struktur entspricht exakt dem
    CDH-WEX-Schema (siehe Beispiel-WEX von DEKRA).
    """
    ship = order.get("shipping") or {}
    billing = order.get("billing") or {}

    # --- Sender = Hauptkunde in CDH = RECHNUNGSADRESSE --------------------
    # Laut WEX-Spec enthält der Sender-Block nur Debitorennummer und
    # Hauptkundenadresse — also die Firmenanschrift, die zur DatevNo gehört.
    # Bei Allgaier ist das immer "Allgaier Agrarhandel, Allmendingen",
    # unabhängig davon, wer bestellt hat.
    # Der Name des Bestellers gehört NICHT hierher: Er würde sonst bei jeder
    # Bestellung den Ansprechpartner im Kundenstammsatz überschreiben.
    # Er steht im Delivery-Block (und bei Sammelaufträgen in den Trennzeilen).
    bill_person = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
    name1 = (billing.get("company") or "").strip()
    if not name1:
        # Privatkunde ohne Firma — dann ist der Personenname die Kundenadresse
        name1 = bill_person
    name2 = ""

    street = " ".join(filter(None, [billing.get("address_1"),
                                    billing.get("address_2")])).strip()
    postcode = billing.get("postcode") or ""
    city = billing.get("city") or ""
    country = billing.get("country") or "DE"
    email = billing.get("email") or ""

    # Feste Hauptkundenadresse aus der Config hat Vorrang.
    # Nötig für Mitarbeitershops: Dort erfassen die Besteller keine
    # Rechnungsadresse, die WooCommerce-Felder bleiben leer. Ohne diesen
    # Block ginge ein leerer Sender an CDH — und CDH könnte damit den
    # Kundenstammsatz überschreiben.
    sender_cfg = shop_cfg.get("sender_address") or {}
    if sender_cfg:
        name1 = str(sender_cfg.get("name1") or name1).strip()
        street = str(sender_cfg.get("street") or street).strip()
        postcode = str(sender_cfg.get("postcode") or postcode).strip()
        city = str(sender_cfg.get("city") or city).strip()
        country = str(sender_cfg.get("country") or country).strip()
        if sender_cfg.get("email"):
            email = str(sender_cfg["email"]).strip()

    # --- Delivery = LIEFERADRESSE -----------------------------------------
    # Versandfelder haben Vorrang, Rechnung nur als Fallback (z.B. wenn im
    # Shop keine abweichende Lieferadresse erfasst wurde).
    ship_person = f"{ship.get('first_name', '')} {ship.get('last_name', '')}".strip()
    del_name1 = (ship.get("company") or billing.get("company") or "").strip()
    del_person = ship_person or bill_person
    if not del_name1:
        del_name1 = del_person
        del_name2 = ""
    else:
        del_name2 = del_person

    del_street = (
        " ".join(filter(None, [ship.get("address_1"), ship.get("address_2")]))
        or street
    ).strip()
    del_postcode = ship.get("postcode") or postcode
    del_city = ship.get("city") or city
    del_country = ship.get("country") or country

    # Name des Bestellers/Empfängers als eigenes Feld. Wird für die
    # Trennzeilen im Sammel-Modus und optional für die Bestellnummer
    # gebraucht — unabhängig davon, was in den Adressblöcken steht.
    person_name = del_person

    shipping_lines = order.get("shipping_lines") or []
    mode_of_shipment = shipping_lines[0].get("method_title", "") if shipping_lines else ""

    # Bestelldatum im Format "DD.MM.YYYY" (wie DEKRA-WEX)
    date_str = order.get("date_created") or order.get("date_created_gmt") or ""
    try:
        d = datetime.fromisoformat(date_str.replace("Z", ""))
        order_date = d.strftime("%d.%m.%Y")
    except ValueError:
        order_date = datetime.now().strftime("%d.%m.%Y")

    order_no = str(order.get("number") or order.get("id") or "")
    datev_no = str(shop_cfg["datev_no"])
    order_type = shop_cfg.get("order_type", "AB")

    line_items = order.get("line_items") or []
    if not line_items:
        raise OrderBuildError(f"Bestellung {order_no} hat keine Positionen.")

    # Positionen aufbereiten
    positions: list[dict] = []
    for idx, item in enumerate(line_items):
        sku = (item.get("sku") or "").strip()
        if not sku:
            raise OrderBuildError(
                f"Bestellung {order_no}, Position {idx + 1}: SKU fehlt."
            )

        quantity = int(item.get("quantity") or 0)
        product_name = (item.get("name") or "").strip()
        variant_text = _extract_variant_text(item)

        # Wenn der Variantentext im Produktnamen steckt, abschneiden
        if variant_text and product_name.endswith(f" - {variant_text}"):
            product_name = product_name[: -(len(variant_text) + 3)]

        ek, vk = extract_ek_vk(client, item, price_cache)

        positions.append({
            "quantity":      quantity,
            "article_no":    sku,
            "description":   product_name,
            "variant_text":  variant_text,
            "selling_price": vk,
            "buying_price":  ek,
        })

    return {
        "order_type":   order_type,
        "order_no":     order_no,
        "order_date":   order_date,
        "datev_no":     datev_no,
        "contact_person": email,
        "mode_of_shipment": mode_of_shipment,
        "name1":        name1,
        "name2":        name2,
        "street":       street,
        "postcode":     postcode,
        "city":         city,
        "country":      country,
        "email":        email,
        # Lieferadresse getrennt — wird im <Delivery>-Block ausgegeben
        "del_name1":    del_name1,
        "del_name2":    del_name2,
        "del_street":   del_street,
        "del_postcode": del_postcode,
        "del_city":     del_city,
        "del_country":  del_country,
        "person_name":  person_name,
        "positions":    positions,
    }


def _extract_variant_parts(line_item: dict) -> tuple:
    """
    Zerlegt die Varianten-Attribute einer Position in (Farbe, Größe, Rest).

    WooCommerce legt sie in meta_data ab, mit Keys wie "pa_groesse",
    "groesse", "farbe". Die Reihenfolge ist nicht garantiert — deshalb wird
    nach Bedeutung sortiert statt das erste Treffer zu nehmen.
    """
    farbe = ""
    groesse = ""
    rest: list[str] = []

    for m in line_item.get("meta_data", []) or []:
        key = (m.get("key") or "").lower()
        val = m.get("display_value") or m.get("value") or ""
        if key.startswith("_") or not val:
            continue
        val = str(val).strip()
        if any(k in key for k in ("groesse", "grösse", "size")):
            groesse = groesse or val
        elif any(k in key for k in ("farbe", "color", "colour")):
            farbe = farbe or val
        elif key.startswith("pa_"):
            rest.append(val)

    return farbe, groesse, rest


def _extract_variant_text(line_item: dict) -> str:
    """
    Variantentext für die WEX (DescriptionText2), z.B. "mindful blue, M".

    Farbe zuerst, dann Größe — wie im Produktnamen im Shop. Fehlt eines
    von beiden, bleibt nur das vorhandene stehen.
    """
    farbe, groesse, rest = _extract_variant_parts(line_item)
    teile = [t for t in (farbe, groesse) if t] or rest
    return ", ".join(teile)


def _variant_text_labeled(line_item: dict) -> str:
    """
    Beschriftete Fassung für die Excel-Spalte "Artikeltext 2",
    z.B. "Farbe: mindful blue, Größe: M".
    """
    farbe, groesse, rest = _extract_variant_parts(line_item)
    teile = []
    if farbe:
        teile.append(f"Farbe: {farbe}")
    if groesse:
        teile.append(f"Größe: {groesse}")
    if not teile:
        teile = rest
    return ", ".join(teile)


# ---------------------------------------------------------------------------
# WEX-Schreiber (XML im CDH-dietronic-Werbemittel-Exchange-Format)
# ---------------------------------------------------------------------------

def _wex_text(value: Any) -> str:
    """
    Wandelt einen Wert in XML-sicheren Text.
    Leere/None werden zu leerem String.
    """
    if value is None:
        return ""
    return str(value)


def _xml_escape(text: str) -> str:
    """Minimaler XML-Escape für Text-Inhalte."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _xml_tag(name: str, value: Any, indent: str = "") -> str:
    """Erzeugt <tag>value</tag>\n. Leere Werte werden zu <tag />\n."""
    text = _wex_text(value)
    if text == "":
        return f"{indent}<{name} />\n"
    return f"{indent}<{name}>{_xml_escape(text)}</{name}>\n"


def _aggregate_all_positions(positions: list[dict]) -> list[dict]:
    """
    Fasst ALLE Positionen zusammen, nicht nur Veredelungen. Zwei Positionen
    werden gemergt, wenn Artikelnummer, Variantentext und beide Preise
    identisch sind — also dieselbe Artikelvariante zum selben Preis.

    Gedacht für Shops, bei denen die Ware gesammelt an einen Standort geht
    und dort verteilt wird (Ensinger). Die Zuordnung "wer bekommt was" geht
    dabei im CDH-Auftrag verloren; sie steht weiterhin in der Excel-Datei.

    Die Reihenfolge der ersten Nennung bleibt erhalten.
    """
    result: list[dict] = []
    index_map: dict[tuple, int] = {}

    for pos in positions:
        key = (
            pos.get("article_no") or "",
            pos.get("variant_text") or "",
            pos.get("selling_price"),
            pos.get("buying_price"),
        )
        # Positionen ohne Artikelnummer (Trenner) nie zusammenfassen
        if not key[0]:
            result.append(dict(pos))
            continue
        if key in index_map:
            existing = result[index_map[key]]
            try:
                existing["quantity"] = int(existing.get("quantity", 0)) \
                                       + int(pos.get("quantity", 0))
            except (TypeError, ValueError):
                existing["quantity"] = (str(existing.get("quantity") or "")
                                        + "+" + str(pos.get("quantity") or ""))
            continue
        index_map[key] = len(result)
        result.append(dict(pos))

    return result


def load_delivery_addresses() -> dict:
    """
    Liest lieferadressen.yaml und liefert:

        { "<Shop-Name>": { "<lieferort-klein>": {name1, name2, ...} } }

    Die Lieferort-Schlüssel werden kleingeschrieben und getrimmt, damit
    "Cham", "cham " und "CHAM" denselben Eintrag treffen.

    Fehlt die Datei oder ist sie fehlerhaft, kommt ein leeres Dict zurück —
    dann greifen wie bisher die Adressen aus WooCommerce.
    """
    if not DELIVERY_ADDRESSES_PATH.exists():
        return {}
    try:
        with DELIVERY_ADDRESSES_PATH.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001
        logging.error("lieferadressen.yaml konnte nicht gelesen werden (%s) — "
                      "es gelten die Adressen aus WooCommerce.", e)
        return {}

    table: dict = {}
    for shop, orte in (raw or {}).items():
        if not isinstance(orte, dict):
            continue
        table[str(shop)] = {
            str(ort).strip().lower(): adr
            for ort, adr in orte.items()
            if isinstance(adr, dict)
        }
    return table


def apply_delivery_address(data: dict, shop_name: str, delivery: str,
                            table: dict) -> bool:
    """
    Überschreibt den Delivery-Block mit der hinterlegten Adresse für diesen
    Lieferort. Gibt True zurück, wenn ein Eintrag gefunden wurde.

    Nicht hinterlegte Lieferorte bleiben unverändert — dort gilt weiterhin
    die Versandadresse aus der Bestellung.
    """
    if not table or not delivery:
        return False
    adr = (table.get(shop_name) or {}).get(delivery.strip().lower())
    if not adr:
        return False

    data["del_name1"] = str(adr.get("name1") or "").strip()
    data["del_name2"] = str(adr.get("name2") or "").strip()
    data["del_street"] = str(adr.get("street") or "").strip()
    data["del_postcode"] = str(adr.get("postcode") or "").strip()
    data["del_city"] = str(adr.get("city") or "").strip()
    data["del_country"] = str(adr.get("country") or "DE").strip()
    return True


def build_combined_wex_data(orders_data: list[dict], shop_cfg: dict,
                             delivery_location: str) -> dict:
    """
    Kombiniert mehrere Bestellungen (aus build_wex_data) zu EINER Sammel-WEX
    für einen gemeinsamen Lieferort.

    Zwei Varianten, gesteuert über "aggregate_all_positions" im Shop-Block:

    Standard (Allgaier):
      Pro Bestellung ein Trenner (Bestellnr. / Vorname Nachname), danach ihre
      Positionen. Am Ende ein Trenner "Veredelungen" mit den zusammengefassten
      Stick-/Druck-Positionen. Die Zuordnung Person → Artikel bleibt sichtbar.

    Voll zusammengefasst (Ensinger):
      Keine Trenner pro Bestellung. Alle gleichen Artikelvarianten werden über
      alle Bestellungen des Lieferorts addiert. Der CDH-Auftrag zeigt nur noch
      Gesamtmengen. Die Zuordnung pro Person steht in der Excel-Datei.

    In beiden Fällen gilt: OrderNo enthält alle Bestellnummern mit "+"
    verbunden, die Kopfadresse kommt aus der ersten Bestellung.
    """
    if not orders_data:
        raise OrderBuildError("build_combined_wex_data: keine Bestellungen "
                              "übergeben.")

    first = orders_data[0]
    order_nos = [d["order_no"] for d in orders_data]
    combined_no = "+".join(order_nos)

    aggregate_all = bool(shop_cfg.get("aggregate_all_positions"))

    all_positions: list[dict] = []

    if aggregate_all:
        # --- Variante: alles zusammenfassen --------------------------------
        # Ein einzelner Kopf-Trenner nennt Lieferort und Anzahl Bestellungen,
        # damit im CDH-Auftrag erkennbar bleibt, was gebündelt wurde.
        all_positions.append({
            "quantity":      1,
            "article_no":    "",
            "description":   delivery_location or "Sammelauftrag",
            "variant_text":  f"{len(orders_data)} Bestellungen",
            "selling_price": "0.00",
            "buying_price":  "0.00",
        })
        pool: list[dict] = []
        for od in orders_data:
            pool.extend(od["positions"])
        all_positions.extend(_aggregate_all_positions(pool))

        return {
            "order_type":       first["order_type"],
            "order_no":         combined_no,
            "order_date":       first["order_date"],
            "datev_no":         first["datev_no"],
            "contact_person":   first.get("contact_person", ""),
            "mode_of_shipment": delivery_location or first.get("mode_of_shipment", ""),
            "name1":            first["name1"],
            "name2":            "",
            "street":           first["street"],
            "postcode":         first["postcode"],
            "city":             first["city"],
            "country":          first["country"],
            "email":            first["email"],
            "del_name1":        first["name1"],
            "del_name2":        "",
            "del_street":       first["street"],
            "del_postcode":     first["postcode"],
            "del_city":         first["city"],
            "del_country":      first["country"],
            "positions":        all_positions,
        }

    # --- Variante: Trenner pro Bestellung ---------------------------------
    veredelung_pool: list[dict] = []

    for od in orders_data:
        # Trenner für diese Bestellung — zeigt Bestellnummer und Empfänger.
        # person_name kommt aus der Lieferadresse; name1 (Firma) nur als
        # letzter Fallback, falls gar kein Name erfasst wurde.
        person = (od.get("person_name") or od.get("del_name2")
                  or od.get("name2") or od.get("name1") or "").strip()
        all_positions.append({
            "quantity":      1,
            "article_no":    "",           # kein Artikel — Trenner
            "description":   od["order_no"],
            "variant_text":  person,
            "selling_price": "0.00",
            "buying_price":  "0.00",
        })
        # Normale Positionen dieser Bestellung, Veredelungen sammeln
        for pos in od["positions"]:
            if _is_veredelung(pos.get("article_no") or ""):
                veredelung_pool.append(pos)
            else:
                all_positions.append(pos)

    # Veredelungen aggregieren (über alle Bestellungen dieses Lieferorts)
    if veredelung_pool:
        aggregated = _aggregate_veredelungen(veredelung_pool)
        # Sammel-Trenner
        all_positions.append({
            "quantity":      1,
            "article_no":    "",
            "description":   "Veredelungen",
            "variant_text":  "bestellungsübergreifend",
            "selling_price": "0.00",
            "buying_price":  "0.00",
        })
        all_positions.extend(aggregated)

    return {
        "order_type":       first["order_type"],
        "order_no":         combined_no,
        "order_date":       first["order_date"],
        "datev_no":         first["datev_no"],
        "contact_person":   first.get("contact_person", ""),
        "mode_of_shipment": delivery_location or first.get("mode_of_shipment", ""),
        # Sender = Rechnungsadresse der Firma. Die ist bei allen Bestellungen
        # eines Shops identisch, deshalb ist "first" hier unkritisch.
        # Name2 (Ansprechpartner) bleibt leer: Der Sammelauftrag gehört
        # keiner einzelnen Person — die Namen stehen in den Trennzeilen.
        "name1":            first["name1"],
        "name2":            "",
        "street":           first["street"],
        "postcode":         first["postcode"],
        "city":             first["city"],
        "country":          first["country"],
        "email":            first["email"],
        # Delivery: bewusst die Firmenadresse, NICHT die private
        # Lieferadresse der ersten Bestellung. Ein Sammelauftrag bündelt
        # mehrere Empfänger; welcher Standort gemeint ist, steht in
        # ModeOfShippment (= Lieferort).
        "del_name1":        first["name1"],
        "del_name2":        "",
        "del_street":       first["street"],
        "del_postcode":     first["postcode"],
        "del_city":         first["city"],
        "del_country":      first["country"],
        "positions":        all_positions,
    }


def _sanitize_for_filename(s: str) -> str:
    """
    Macht einen String dateisystemtauglich für WEX-Dateinamen.
    Ersetzt Umlaute, Leerzeichen und alles Nicht-ASCII/-Sonderzeichen.
    """
    if not s:
        return "ohne-lieferort"
    # Umlaute
    umlaut_map = {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    }
    for k, v in umlaut_map.items():
        s = s.replace(k, v)
    # Alles außer Buchstaben, Ziffern, Bindestrich → Bindestrich
    out = []
    prev_dash = False
    for c in s:
        if c.isalnum():
            out.append(c)
            prev_dash = False
        else:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    result = "".join(out).strip("-")
    return result or "ohne-lieferort"


def _is_veredelung(article_no: str) -> bool:
    """True, wenn die Artikelnummer einem Veredelungs-Präfix zugeordnet ist."""
    if not article_no:
        return False
    return any(article_no.startswith(p) for p in VEREDELUNG_PREFIXES)


def _aggregate_veredelungen(positions: list[dict]) -> list[dict]:
    """
    Fasst Veredelungspositionen (Sticker, Druck) innerhalb einer Bestellung
    zusammen. Zwei Positionen werden gemergt, wenn ArticleNo1, SellingPrice
    und BuyingPrice identisch sind — dann werden die Mengen addiert.

    Nicht-Veredelungs-Positionen bleiben unangetastet. Die Reihenfolge der
    Positionen bleibt so weit wie möglich erhalten: Die erste Nennung einer
    Veredelungsposition behält ihren Platz, spätere identische Positionen
    fließen dort ein.
    """
    result: list[dict] = []
    # Merker: ArticleNo1 → Index in result (nur für Veredelungen)
    index_map: dict[tuple, int] = {}

    for pos in positions:
        art = pos.get("article_no") or ""
        if _is_veredelung(art):
            key = (art, pos.get("selling_price"), pos.get("buying_price"))
            if key in index_map:
                # Menge auf bestehende Position addieren
                existing = result[index_map[key]]
                try:
                    existing["quantity"] = int(existing.get("quantity", 0)) \
                                           + int(pos.get("quantity", 0))
                except (TypeError, ValueError):
                    # Fallback: Mengen als Strings, wenn Umwandlung nicht klappt
                    existing["quantity"] = (str(existing.get("quantity") or "")
                                            + "+" + str(pos.get("quantity") or ""))
                continue
            index_map[key] = len(result)
        result.append(dict(pos))

    return result


def write_cdh_wex(target_path: Path, data: dict) -> None:
    """
    Schreibt eine CDH-WEX-Datei (XML) im Format, das CDH_WEX.EXE direkt
    importiert. Struktur und Feldnamen exakt wie im dietronic-WEX-Schema
    (siehe Beispiel-WEX von DEKRA).

    Beim Öffnen der Datei mit CDH_WEX.EXE wird automatisch der Import
    gestartet und ein Ergebnis-Fenster angezeigt.
    """
    parts: list[str] = []
    parts.append('<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n')
    parts.append('<!--CDH-dietronic-WEX-Werbemittel-Exchange-XML-File-->\n')
    parts.append('<!--WEXVersion=1.0-->\n')
    parts.append('<PurchaseOrder XmlStandard="1" SystemId="CDH">\n')
    parts.append('  <OrderHeader>\n')

    # Sender: Absender (= Besteller im Shop)
    parts.append('    <Sender>\n')
    parts.append(_xml_tag("DatevNo", data["datev_no"], "      "))
    parts.append('      <NameAddress>\n')
    parts.append(_xml_tag("Name1",          data["name1"],       "        "))
    parts.append(_xml_tag("Name2",          data["name2"],       "        "))
    parts.append(_xml_tag("Street",         data["street"],      "        "))
    parts.append(_xml_tag("PostalCodeCity", data["postcode"],    "        "))
    parts.append(_xml_tag("City",           data["city"],        "        "))
    parts.append(_xml_tag("Country",        data["country"],     "        "))
    parts.append(_xml_tag("Email",          data["email"],       "        "))
    parts.append('      </NameAddress>\n')
    parts.append('    </Sender>\n')

    # Delivery: Lieferadresse. Fällt auf die Rechnungsadresse zurück, wenn
    # im Shop keine abweichende Lieferadresse erfasst wurde.
    parts.append('    <Delivery>\n')
    parts.append(_xml_tag("ModeOfShippment", data["mode_of_shipment"], "      "))
    parts.append('      <NameAddress>\n')
    parts.append(_xml_tag("Name1",          data.get("del_name1") or data["name1"],       "        "))
    parts.append(_xml_tag("Name2",          data.get("del_name2") or data["name2"],       "        "))
    parts.append(_xml_tag("Street",         data.get("del_street") or data["street"],     "        "))
    parts.append(_xml_tag("PostalCodeCity", data.get("del_postcode") or data["postcode"], "        "))
    parts.append(_xml_tag("City",           data.get("del_city") or data["city"],         "        "))
    parts.append(_xml_tag("Country",        data.get("del_country") or data["country"],   "        "))
    parts.append(_xml_tag("Email",          data["email"],       "        "))
    parts.append('      </NameAddress>\n')
    parts.append('    </Delivery>\n')

    # OrderNo: optional mit Empfängername, wenn "order_no_with_name" in der
    # Config gesetzt ist. Der Dateiname der WEX bleibt davon unberührt.
    parts.append(_xml_tag("OrderNo",   data.get("order_no_wex") or data["order_no"], "    "))
    parts.append(_xml_tag("OrderDate", data["order_date"], "    "))
    parts.append(_xml_tag("OrderId",   data["order_type"], "    "))
    parts.append('  </OrderHeader>\n')

    # ListOfOrderDetails: Positionen
    # Vorverarbeitung: Veredelungspositionen (Sticker, Druck) mit gleichem
    # ArticleNo1 und gleichen Preisen werden zu EINER Position mit summierter
    # Menge zusammengefasst. Aggregation gilt nur innerhalb einer Bestellung.
    positions = _aggregate_veredelungen(data["positions"])

    parts.append('  <ListOfOrderDetails>\n')
    for pos in positions:
        parts.append('    <OrderDetails>\n')
        parts.append(_xml_tag("Quantity",    pos["quantity"],     "      "))
        parts.append(_xml_tag("ArticleNo1",  pos["article_no"],   "      "))
        parts.append(_xml_tag("Description1", pos["description"], "      "))

        # DescriptionText2: Variantentext (bei uns "XL", "M", o.ä.)
        # DEKRA nutzt hier HTML; wir halten es simpel als Plaintext.
        variant = pos.get("variant_text") or ""
        if variant:
            parts.append(f"      <DescriptionText2>{_xml_escape(variant)}</DescriptionText2>\n")

        # Preise als "123.45" (Punkt, keine Komma). Float → String.
        selling = pos.get("selling_price")
        if selling is not None:
            parts.append(f"      <SellingPrice>{selling}</SellingPrice>\n")
        buying = pos.get("buying_price")
        if buying is not None:
            parts.append(f"      <BuyingPrice>{buying}</BuyingPrice>\n")

        parts.append('    </OrderDetails>\n')
    parts.append('  </ListOfOrderDetails>\n')
    parts.append('</PurchaseOrder>\n')

    target_path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 mit BOM — genau wie die DEKRA-WEX-Datei
    target_path.write_text("".join(parts), encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# Excel-Kontrollausdruck (parallel zur WEX)
# ---------------------------------------------------------------------------
#
# Format: identisch zum Advanced-Order-Export-Plugin (21 Spalten mit
# Datev-Nr in Spalte 0). Eine Zeile pro Bestellposition. Bei Sammel-WEX
# landen alle Bestellungen des Lieferorts in derselben Excel — jede mit
# ihrer echten Bestellnummer, Vorname, Nachname etc. (NICHT als Trennzeilen).
# Aggregierte Veredelungen aus der WEX bleiben in der Excel EINZELN — pro
# Ursprungsbestellung — damit die Produktion sieht, welche Sticker/Drucke
# für wen sind.

_EXCEL_HEADERS = [
    "Datev-Nr",
    "Bestellnummer",
    "Bestellstatus",
    "Auftragsdatum",
    "Kundenhinweis",
    "Firma (Fakturierung)",
    "Adresse 1 & 2 (Abrechnung)",
    "Postleitzahl (Abrechnung)",
    "Stadt (Abrechnung)",
    "E-Mail (Besteller)",
    "Vorname (Empfänger)",
    "Nachname (Empfänger)",
    "Lieferort",
    "Anzahl",
    "Artikelnummer",
    "Artikelname",
    "Artikeltext 1",
    "Artikeltext 2",
    "VK Gesamt",
    "VK Artikel",
    "EK Artikel",
]


def _parse_extra_meta_config(shop_cfg: dict) -> list[tuple]:
    """
    Liest die Shop-Option "extra_excel_meta" und liefert eine Liste aus
    (meta_key, Spaltenüberschrift).

    Erlaubt sind beide Schreibweisen:

        extra_excel_meta:
          - personalnummer                    # Key = Überschrift
          - key: mitarbeiterin_2
            label: Mitarbeiterin 2
    """
    entries = shop_cfg.get("extra_excel_meta") or []
    result: list[tuple] = []
    for e in entries:
        if isinstance(e, dict):
            key = str(e.get("key") or "").strip()
            label = str(e.get("label") or key).strip()
        else:
            key = str(e).strip()
            label = key
        if key:
            result.append((key, label))
    return result


def _meta_matches(entry: dict, wanted: set) -> bool:
    """Prüft key und display_key eines Meta-Eintrags gegen die Suchbegriffe."""
    for field in ("key", "display_key"):
        val = str(entry.get(field) or "").strip().lower()
        if val and val in wanted:
            return True
    return False


def _read_order_meta(order: dict, key: str, item: dict | None = None) -> str:
    """
    Liest ein Meta-Feld zu einer Bestellposition.

    Gesucht wird in dieser Reihenfolge:
      1. line_items[].meta_data der Position — dort liegen PPOM-Felder
         (Produktoptionen wie "MitarbeiterIn", "Personalnummer").
      2. order.meta_data — für Felder, die an der Bestellung hängen
         (Checkout-Zusatzfelder).

    Verglichen wird gegen key UND display_key, jeweils mit und ohne
    führenden Unterstrich und ohne Rücksicht auf Groß-/Kleinschreibung —
    WooCommerce-Plugins speichern das uneinheitlich.

    Bevorzugt wird display_value (die lesbare Fassung, z.B. "Ja" statt "1").
    """
    if not key:
        return ""
    k = key.strip().lower()
    wanted = {k, k.lstrip("_"), "_" + k.lstrip("_")}

    sources = []
    if item:
        sources.append(item.get("meta_data") or [])
    sources.append(order.get("meta_data") or [])

    for metas in sources:
        for m in metas:
            if not _meta_matches(m, wanted):
                continue
            val = m.get("display_value")
            if val in (None, ""):
                val = m.get("value")
            if val in (None, ""):
                return ""
            if isinstance(val, (list, dict)):
                return str(val)
            return str(val).strip()
    return ""


def _row_from_order_position(order: dict, item: dict, shop_cfg: dict,
                              client: "WooClient",
                              price_cache: dict) -> list:
    """
    Baut eine Excel-Zeile für eine einzelne Bestellposition, wie sie der
    Advanced-Order-Export erzeugen würde. Preise werden über den bekannten
    extract_ek_vk-Weg geholt (Cache verhindert doppelte API-Aufrufe).
    """
    billing = order.get("billing") or {}
    shipping = order.get("shipping") or {}
    shipping_lines = order.get("shipping_lines") or []

    # Datum
    d = order.get("date_created") or order.get("date_created_gmt") or ""
    try:
        auftragsdatum = datetime.fromisoformat(
            d.replace("Z", "")).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        auftragsdatum = d

    # Lieferort aus Versandart
    lieferort = ""
    if shipping_lines:
        lieferort = (shipping_lines[0].get("method_title") or "").strip()

    # Preise über den bekannten Weg
    ek, vk = extract_ek_vk(client, item, price_cache)
    quantity = int(item.get("quantity") or 0)
    total = item.get("total")
    try:
        vk_gesamt = float(total) if total not in (None, "") else 0.0
    except (TypeError, ValueError):
        vk_gesamt = 0.0

    # Artikelname und variant_text ähnlich wie in build_wex_data trennen
    product_name = (item.get("name") or "").strip()
    variant_text = _extract_variant_text(item)
    if variant_text and product_name.endswith(f" - {variant_text}"):
        product_name = product_name[: -(len(variant_text) + 3)]

    # Artikeltext 1 = kurze Beschreibung, Artikeltext 2 = Variantentext.
    # Für die Kurzbeschreibung ist der Wert im line_item nicht immer da —
    # wir lassen ihn leer, wenn nicht vorhanden.
    artikeltext_1 = ""
    artikeltext_2 = _variant_text_labeled(item)

    row = [
        str(shop_cfg.get("datev_no", "")),
        str(order.get("number") or order.get("id") or ""),
        order.get("status") or "",
        auftragsdatum,
        (order.get("customer_note") or "").strip(),
        (billing.get("company") or "").strip(),
        " ".join(filter(None, [billing.get("address_1"),
                                billing.get("address_2")])).strip(),
        (billing.get("postcode") or "").strip(),
        (billing.get("city") or "").strip(),
        (billing.get("email") or "").strip(),
        (shipping.get("first_name") or billing.get("first_name") or "").strip(),
        (shipping.get("last_name") or billing.get("last_name") or "").strip(),
        lieferort,
        quantity,
        (item.get("sku") or "").strip(),
        product_name,
        artikeltext_1,
        artikeltext_2,
        round(vk_gesamt, 2),
        _to_price_cell(vk),
        _to_price_cell(ek),
    ]

    # Zusätzliche Meta-Felder aus dem Shop, hinten angehängt. Die 21
    # Standardspalten bleiben dadurch an ihrer Position.
    # item wird mitgegeben, weil PPOM-Felder an der Position hängen.
    for key, _label in _parse_extra_meta_config(shop_cfg):
        row.append(_read_order_meta(order, key, item))

    return row


def _to_price_cell(value):
    """Preise für Excel als Zahl, wenn möglich; sonst leer."""
    if value in (None, ""):
        return ""
    try:
        return round(float(str(value).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return str(value)


def write_excel_export(target_path: Path, orders: list, shop_cfg: dict,
                       client: "WooClient", price_cache: dict) -> None:
    """
    Schreibt einen Excel-Kontrollausdruck mit einer Zeile pro Bestell-
    Position. `orders` ist eine Liste der WooCommerce-Bestell-Dicts.

    Rückwärtskompatibel mit dem Advanced-Order-Export-Format (21 Spalten,
    identische Reihenfolge).

    Schweigsam bei fehlendem openpyxl — dann wird kein Excel geschrieben
    und stattdessen einmal in den Log geschrieben.
    """
    if not _HAS_OPENPYXL:
        logging.warning("openpyxl nicht verfügbar — Excel-Export "
                        "übersprungen (%s).", target_path.name)
        return

    # Zusatzspalten aus der Shop-Konfiguration hinten anhängen
    extra_meta = _parse_extra_meta_config(shop_cfg)
    headers = list(_EXCEL_HEADERS) + [label for _key, label in extra_meta]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bestellungen"
    ws.append(headers)

    for order in orders:
        for item in (order.get("line_items") or []):
            try:
                row = _row_from_order_position(order, item, shop_cfg,
                                                client, price_cache)
                ws.append(row)
            except Exception as e:  # noqa: BLE001
                logging.exception("Excel-Zeile für Bestellung %s "
                                  "Position %s fehlgeschlagen: %s",
                                  order.get("number"),
                                  item.get("sku"), e)

    # Warnen nur, wenn KEIN einziges konfiguriertes Zusatzfeld gefüllt war.
    # Einzelne leere Spalten sind normal: PPOM-Felder können bedingt sein
    # (z.B. Name der Kollegin nur, wenn "Teambestellung = Ja"). Erst wenn
    # alle leer bleiben, deutet das auf falsche Data Names hin.
    if extra_meta:
        gefunden = [
            label for key, label in extra_meta
            if any(_read_order_meta(o, key, it)
                   for o in orders
                   for it in (o.get("line_items") or []))
        ]
        if not gefunden:
            logging.warning("Keines der konfigurierten Zusatzfelder (%s) war "
                            "in %s gefüllt — Data Names im Shop prüfen "
                            "(PPOM Fields).",
                            ", ".join(k for k, _l in extra_meta),
                            target_path.name)

    # Spaltenbreiten grob nach Header-Länge, damit die Datei ohne
    # Nach-Anpassen lesbar ist.
    for col_idx, header in enumerate(headers, start=1):
        col_letter = openpyxl.utils.get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = max(12, len(header) + 2)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(target_path)


# ---------------------------------------------------------------------------
# Shop-Runner
# ---------------------------------------------------------------------------

def process_shop(shop_cfg: dict, global_cfg: dict) -> dict:
    """
    Einen Shop abarbeiten. Gibt eine Zusammenfassung zurück.

    Zwei Modi (per Config-Flag "combine_by_delivery"):

      Standard-Modus (eine WEX pro Bestellung):
        1. Für jede offene Bestellung eine WEX-Datei schreiben.
        2. In WooCommerce als exportiert markieren.
        3. CDH_WEX.EXE mit der Datei starten.

      Sammel-Modus ("combine_by_delivery: true", z.B. Allgaier, Ensinger):
        1. Alle offenen Bestellungen abrufen und nach Lieferort gruppieren
           (shipping_lines[0].method_title). Bestellungen ohne Lieferort
           landen in einer eigenen Gruppe "ohne-lieferort".
        2. Pro Lieferort EINE Sammel-WEX bauen — mit Trennern pro
           Kundenbestellung und zusammengefassten Veredelungen am Ende.
        3. Erst nach erfolgreichem Schreiben der WEX werden ALLE
           Bestellungen dieser Gruppe in WooCommerce als exportiert markiert.
        4. CDH_WEX.EXE mit der Sammel-Datei starten.
    """
    shop_name = shop_cfg.get("name", shop_cfg["url"])
    logging.info("--- Shop: %s ---", shop_name)

    # Tages-Gate: Manche Shops werden nur an bestimmten Tagen im Monat
    # importiert (Ensinger: zum Monatsersten). Ohne Eintrag läuft der Shop
    # bei jedem Aufruf. Die Prüfung steht bewusst ganz vorn, damit an
    # anderen Tagen keine API-Abfrage passiert.
    import_days = shop_cfg.get("import_on_days")
    if import_days:
        try:
            allowed = {int(d) for d in import_days}
        except (TypeError, ValueError):
            logging.error("[%s] 'import_on_days' enthält ungültige Werte "
                          "(%r) — Shop wird regulär verarbeitet.",
                          shop_name, import_days)
            allowed = set()
        heute = datetime.now().day
        if allowed and heute not in allowed:
            logging.info("[%s] Übersprungen: Import nur am %s des Monats, "
                         "heute ist der %d.",
                         shop_name,
                         ", ".join(f"{d}." for d in sorted(allowed)),
                         heute)
            return {"shop": shop_name, "ok": 0, "errors": 0, "files": [],
                    "skipped": "Tages-Gate"}

    # Statusfilter: pro Shop überschreibbar (z.B. Allgaier nur "processing").
    shop_statuses = shop_cfg.get("included_statuses")
    client = WooClient(shop_cfg["url"], shop_cfg["consumer_key"],
                       shop_cfg["consumer_secret"],
                       statuses=shop_statuses)
    if shop_statuses:
        logging.info("[%s] Statusfilter: %s", shop_name,
                     ", ".join(shop_statuses))

    target_folder = Path(shop_cfg.get("cdh_import_folder")
                         or global_cfg.get("cdh_import_folder")
                         or "./output")

    # Zielordner für Excel-Kontrollausdrucke — parallel zu den WEX.
    # Wird bei Bedarf angelegt. Ohne Konfig-Eintrag fällt es auf
    # <cdh_import_folder>/../excel-archiv zurück.
    excel_folder_cfg = (shop_cfg.get("excel_export_folder")
                        or global_cfg.get("excel_export_folder"))
    if excel_folder_cfg:
        excel_folder = Path(excel_folder_cfg)
    else:
        # Default: excel-archiv NEBEN wex-archiv, also eine Ebene höher.
        excel_folder = target_folder.parent / "excel-archiv"

    price_cache: dict = {}
    summary = {"shop": shop_name, "ok": 0, "errors": 0, "files": []}

    # Lokales Export-Log einlesen — dient als zweite Wahrheit zusätzlich
    # zum WooCommerce-Meta-Feld. Verhindert Doppel-Exporte, wenn eine
    # frühere mark_exported()-Anfrage scheitert.
    exported_locally = load_exported_log()
    if exported_locally:
        logging.info("[%s] %d bereits exportierte Bestellungen aus "
                     "exported.log geladen.", shop_name, len(exported_locally))

    # Feste Lieferadressen je Lieferort, gepflegt über das Adressen-Tool.
    delivery_table = load_delivery_addresses()
    if delivery_table.get(shop_name):
        logging.info("[%s] %d feste Lieferadresse(n) hinterlegt.",
                     shop_name, len(delivery_table[shop_name]))

    combine_mode = bool(shop_cfg.get("combine_by_delivery"))
    if combine_mode:
        logging.info("[%s] Sammel-Modus aktiv: Bestellungen werden nach "
                     "Lieferort gruppiert.", shop_name)
    # Optionaler Statuswechsel nach erfolgreichem Export. Ohne Eintrag in
    # der Config bleibt der Status unverändert.
    status_after_export = (shop_cfg.get("status_after_export")
                           or global_cfg.get("status_after_export") or "")
    # Die REST-API erwartet den Status ohne "wc-"-Präfix ("completed",
    # nicht "wc-completed") — beides in der Config zulassen.
    status_after_export = str(status_after_export).strip()
    if status_after_export.startswith("wc-"):
        status_after_export = status_after_export[3:]
    if status_after_export:
        logging.info("[%s] Nach Export wird der Status auf '%s' gesetzt.",
                     shop_name, status_after_export)

    # Optional: Empfängername in die Bestellnummer schreiben, damit im
    # CDH-Auftragskopf "3939 Anna Weber" statt nur "3939" steht.
    # Gilt nur im Standard-Modus — im Sammel-Modus enthält die Bestellnummer
    # bereits alle Nummern ("3935+3936+..."), und die Namen stehen dort in
    # den Trennzeilen.
    order_no_with_name = bool(shop_cfg.get("order_no_with_name",
                                           global_cfg.get("order_no_with_name")))

    # ---- Modus 1: Standard, eine WEX pro Bestellung ------------------------

    if not combine_mode:
        for order in client.iter_new_orders(
                exported_locally=exported_locally):
            order_id = order.get("id")
            order_no = order.get("number") or order_id
            try:
                wex_data = build_wex_data(order, shop_cfg, client, price_cache)
            except OrderBuildError as e:
                logging.error("[%s] Bestellung %s übersprungen: %s",
                              shop_name, order_no, e)
                summary["errors"] += 1
                continue
            except Exception as e:  # noqa: BLE001
                logging.exception("[%s] Bestellung %s: unerwarteter Fehler: %s",
                                  shop_name, order_no, e)
                summary["errors"] += 1
                continue

            if order_no_with_name:
                person = (wex_data.get("person_name") or "").strip()
                if person:
                    wex_data["order_no_wex"] = f"{order_no} {person}"

            # Feste Lieferadresse für diesen Lieferort, falls hinterlegt
            lieferort = wex_data.get("mode_of_shipment") or ""
            if apply_delivery_address(wex_data, shop_name, lieferort,
                                      delivery_table):
                logging.info("[%s] Bestellung %s: feste Lieferadresse "
                             "'%s' angewendet.", shop_name, order_no, lieferort)

            date_part = datetime.now().strftime("%Y-%m-%d")
            filename = f"orders-{date_part}-{order_no}.wex"
            target_path = target_folder / filename

            try:
                write_cdh_wex(target_path, wex_data)
            except Exception as e:  # noqa: BLE001
                logging.exception("[%s] WEX für Bestellung %s konnte nicht "
                                  "geschrieben werden: %s",
                                  shop_name, order_no, e)
                summary["errors"] += 1
                continue

            # Excel-Kontrollausdruck parallel schreiben (best effort — ein
            # Fehler soll den CDH-Import nicht blockieren).
            excel_path = excel_folder / f"orders-{date_part}-{order_no}.xlsx"
            try:
                write_excel_export(excel_path, [order], shop_cfg,
                                   client, price_cache)
            except Exception as e:  # noqa: BLE001
                logging.exception("[%s] Excel-Export für Bestellung %s "
                                  "fehlgeschlagen: %s",
                                  shop_name, order_no, e)

            # WICHTIG: Erst lokal loggen, DANN in WooCommerce markieren.
            # Wenn mark_exported fehlschlägt, ist die Bestellung trotzdem
            # in unserem lokalen Log und wird beim nächsten Lauf übersprungen.
            append_to_exported_log(shop_name, order_id, order_no,
                                   target_path.name)

            try:
                client.mark_exported(order_id)
            except Exception as e:  # noqa: BLE001
                logging.warning("[%s] Bestellung %s: WooCommerce-Markierung "
                                "fehlgeschlagen (%s). Ist aber lokal in "
                                "exported.log vermerkt — kein Doppel-Export.",
                                shop_name, order_no, e)

            if status_after_export:
                try:
                    client.set_status(order_id, status_after_export)
                    logging.info("[%s] Bestellung %s → Status '%s'",
                                 shop_name, order_no, status_after_export)
                except Exception as e:  # noqa: BLE001
                    logging.warning("[%s] Bestellung %s: Status konnte nicht "
                                    "auf '%s' gesetzt werden (%s). Export "
                                    "selbst ist davon nicht betroffen.",
                                    shop_name, order_no, status_after_export, e)

            logging.info("[%s] Bestellung %s → %s", shop_name, order_no,
                         target_path.name)
            summary["ok"] += 1
            summary["files"].append(str(target_path))

            start_cdh_wex_import(target_path, global_cfg)

        return summary

    # ---- Modus 2: Sammel-WEX pro Lieferort ---------------------------------

    # Erst: Alle Bestellungen abrufen, nach Lieferort gruppieren.
    # Wir merken uns pro Gruppe: (order_id, order_no, wex_data, order_dict)
    # order_dict wird später für den Excel-Kontrollausdruck gebraucht.
    groups: dict[str, list[tuple]] = {}
    for order in client.iter_new_orders(exported_locally=exported_locally):
        order_id = order.get("id")
        order_no = order.get("number") or order_id
        try:
            wex_data = build_wex_data(order, shop_cfg, client, price_cache)
        except OrderBuildError as e:
            logging.error("[%s] Bestellung %s übersprungen: %s",
                          shop_name, order_no, e)
            summary["errors"] += 1
            continue
        except Exception as e:  # noqa: BLE001
            logging.exception("[%s] Bestellung %s: unerwarteter Fehler: %s",
                              shop_name, order_no, e)
            summary["errors"] += 1
            continue

        # Lieferort aus der Versandart auslesen
        shipping_lines = order.get("shipping_lines") or []
        delivery = ""
        if shipping_lines:
            delivery = (shipping_lines[0].get("method_title") or "").strip()
        if not delivery:
            delivery = "ohne-lieferort"

        groups.setdefault(delivery, []).append((order_id, order_no, wex_data, order))

    if not groups:
        logging.info("[%s] Keine neuen Bestellungen.", shop_name)
        return summary

    # Pro Lieferort-Gruppe: Sammel-WEX bauen, schreiben, markieren, importieren.
    date_part = datetime.now().strftime("%Y-%m-%d")
    shop_slug = _sanitize_for_filename(shop_name).lower()

    for delivery, entries in groups.items():
        order_nos = [e[1] for e in entries]
        wex_datas = [e[2] for e in entries]
        raw_orders = [e[3] for e in entries]
        logging.info("[%s] Lieferort '%s': %d Bestellung(en) (%s)",
                     shop_name, delivery, len(entries), ", ".join(order_nos))

        try:
            combined = build_combined_wex_data(wex_datas, shop_cfg, delivery)
        except Exception as e:  # noqa: BLE001
            logging.exception("[%s] Sammel-WEX für Lieferort '%s' konnte "
                              "nicht gebaut werden: %s",
                              shop_name, delivery, e)
            summary["errors"] += len(entries)
            continue

        # Feste Lieferadresse für diesen Lieferort, falls hinterlegt.
        # Ohne Eintrag bleibt die Firmenadresse stehen.
        if apply_delivery_address(combined, shop_name, delivery,
                                  delivery_table):
            logging.info("[%s] Lieferort '%s': feste Lieferadresse "
                         "angewendet (%s, %s %s).",
                         shop_name, delivery,
                         combined["del_street"], combined["del_postcode"],
                         combined["del_city"])
        elif delivery_table.get(shop_name):
            logging.warning("[%s] Lieferort '%s': keine feste Lieferadresse "
                            "hinterlegt — es gilt die Firmenadresse. Im "
                            "Adressen-Tool ergänzen.", shop_name, delivery)

        delivery_slug = _sanitize_for_filename(delivery)
        filename = f"orders-{date_part}-{shop_slug}-{delivery_slug}.wex"
        target_path = target_folder / filename

        try:
            write_cdh_wex(target_path, combined)
        except Exception as e:  # noqa: BLE001
            logging.exception("[%s] Sammel-WEX-Datei für Lieferort '%s' "
                              "konnte nicht geschrieben werden: %s",
                              shop_name, delivery, e)
            summary["errors"] += len(entries)
            continue

        # Excel-Kontrollausdruck parallel zur Sammel-WEX (alle Bestellungen
        # dieses Lieferorts in einer Datei — jede Position mit ihrer echten
        # Bestellnummer, KEIN aggregierter Sticker, KEINE Trenner-Zeilen).
        excel_path = excel_folder / f"orders-{date_part}-{shop_slug}-{delivery_slug}.xlsx"
        try:
            write_excel_export(excel_path, raw_orders, shop_cfg,
                               client, price_cache)
        except Exception as e:  # noqa: BLE001
            logging.exception("[%s] Excel-Export für Lieferort '%s' "
                              "fehlgeschlagen: %s",
                              shop_name, delivery, e)

        # Erst lokal loggen (zweite Wahrheit), dann WooCommerce markieren.
        # Wenn WooCommerce-Markierung fehlschlägt, sind Bestellungen dank
        # lokalem Log trotzdem gegen Doppel-Export geschützt.
        for order_id, order_no, _, _ in entries:
            append_to_exported_log(shop_name, order_id, order_no,
                                   target_path.name)

        for order_id, order_no, _, _ in entries:
            try:
                client.mark_exported(order_id)
            except Exception as e:  # noqa: BLE001
                logging.warning("[%s] Bestellung %s (Lieferort '%s'): "
                                "WooCommerce-Markierung fehlgeschlagen "
                                "(%s). Ist aber lokal in exported.log "
                                "vermerkt — kein Doppel-Export.",
                                shop_name, order_no, delivery, e)

        if status_after_export:
            for order_id, order_no, _, _ in entries:
                try:
                    client.set_status(order_id, status_after_export)
                except Exception as e:  # noqa: BLE001
                    logging.warning("[%s] Bestellung %s: Status konnte nicht "
                                    "auf '%s' gesetzt werden (%s). Export "
                                    "selbst ist davon nicht betroffen.",
                                    shop_name, order_no, status_after_export, e)
            logging.info("[%s] %d Bestellung(en) auf Status '%s' gesetzt.",
                         shop_name, len(entries), status_after_export)

        logging.info("[%s] Sammel-WEX '%s' → %s (%d Bestellung(en), "
                     "%d Positionen)",
                     shop_name, delivery, target_path.name, len(entries),
                     len(combined["positions"]))
        summary["ok"] += len(entries)
        summary["files"].append(str(target_path))

        # CDH_WEX.EXE mit der Sammel-Datei starten (blockiert bis "Ende")
        start_cdh_wex_import(target_path, global_cfg)

    return summary


def _cdh_wex_running(exe: str) -> bool:
    """
    True, wenn CDH_WEX.EXE auf diesem Rechner gerade läuft.

    Nutzt tasklist (Windows-Bordmittel). Auf anderen Systemen oder bei
    Fehlern kommt False zurück — dann verlässt sich der Ablauf allein auf
    das blockierende subprocess.run.
    """
    if os.name != "nt":
        return False
    name = Path(exe).name
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return name.lower() in (out.stdout or "").lower()
    except Exception:  # noqa: BLE001
        return False


def _wait_until_cdh_closed(exe: str, reason: str,
                           timeout_min: int = 15) -> bool:
    """
    Wartet, bis kein CDH_WEX.EXE mehr läuft. Gibt False zurück, wenn es
    nach timeout_min Minuten immer noch offen ist.
    """
    if not _cdh_wex_running(exe):
        return True
    logging.warning("CDH-Import ist noch geöffnet (%s) — warte, bis es "
                    "geschlossen ist.", reason)
    print(f"\n  >>> Ein CDH-Importfenster ist noch offen. Bitte dort auf "
          f"'Ende' klicken — der nächste Auftrag wartet so lange.\n")
    deadline = time.monotonic() + timeout_min * 60
    while time.monotonic() < deadline:
        time.sleep(2)
        if not _cdh_wex_running(exe):
            return True
    return False


def start_cdh_wex_import(wex_path: Path, global_cfg: dict) -> bool:
    """
    Übergibt EINE WEX-Datei an CDH und kehrt erst zurück, wenn das
    CDH-Importfenster wieder geschlossen ist.

    CDH_WEX.EXE darf nur einmal gleichzeitig laufen ("WEX Importer bereits
    ausgeführt"). Deshalb drei Sicherungen:
      1. Vorher prüfen, ob schon ein CDH-Import offen ist (z. B. von Hand
         aus dem wex-archiv gestartet) — dann warten.
      2. subprocess.run blockiert, bis das Programm beendet ist.
      3. Nachher noch einmal prüfen — falls CDH_WEX.EXE ein weiteres
         Programm startet und sich selbst sofort beendet.

    Gibt False zurück, wenn der Import nicht gestartet werden konnte. Die
    WEX-Datei bleibt dann im wex-archiv und lässt sich per Doppelklick
    nachholen.
    """
    exe = global_cfg.get("cdh_exe") or r"C:\CDH\CDH_WEX.EXE"
    if not Path(exe).exists():
        logging.warning("CDH-WEX-EXE nicht gefunden: %s — Import nicht "
                        "gestartet. Datei wartet unter %s.",
                        exe, wex_path)
        return False

    if not _wait_until_cdh_closed(exe, "vor dem Start"):
        logging.error("CDH-Import war nach 15 Minuten noch geöffnet — %s "
                      "wurde NICHT übergeben. Bitte aus dem wex-archiv per "
                      "Doppelklick nachholen.", wex_path.name)
        return False

    try:
        logging.info("CDH-WEX-Import gestartet: %s — warte auf 'Ende' im "
                     "CDH-Fenster ...", wex_path.name)
        result = subprocess.run([exe, str(wex_path)])
    except Exception as e:  # noqa: BLE001
        logging.exception("CDH-WEX-Import konnte nicht gestartet werden: %s", e)
        return False

    # Sicherheitsnetz, falls CDH_WEX.EXE nur ein Starter war
    _wait_until_cdh_closed(exe, "nach dem Start")

    logging.info("CDH-WEX-Import abgeschlossen (Exit %d): %s",
                 result.returncode, wex_path.name)
    return True


# ---------------------------------------------------------------------------
# Lokales Export-Log (zweite Wahrheit gegen doppelte Importe)
# ---------------------------------------------------------------------------
#
# Warum: Wenn nach dem Schreiben der WEX-Datei die WooCommerce-Markierung
# (mark_exported) fehlschlägt (Netzwerk, IONOS, Timeout), würde die Bestellung
# beim nächsten Lauf nochmal exportiert werden. Das lokale Log wird
# GESCHRIEBEN, BEVOR wir mark_exported() aufrufen — dadurch weiß der nächste
# Lauf auch dann, dass die Bestellung schon durch ist, wenn die
# WooCommerce-Seite versagt hat.
#
# Format: TSV mit Header, eine Zeile pro exportierter Bestellung:
#   timestamp \t shop \t order_id \t order_no \t wex_file
# Zeitstempel: ISO-8601 (Sekundengenauigkeit).

_EXPORTED_LOG_HEADER = "timestamp\tshop\torder_id\torder_no\twex_file\n"


def load_exported_log() -> set:
    """
    Liest exported.log ein und liefert ein Set aus "<order_id>|<order_no>"-
    Schlüsseln. Fehlt die Datei oder ist beschädigt, kommt ein leeres Set
    zurück — dann wirkt nur noch die WooCommerce-Markierung als Filter.
    """
    keys: set = set()
    if not EXPORTED_LOG_PATH.exists():
        return keys
    try:
        with EXPORTED_LOG_PATH.open("r", encoding="utf-8") as f:
            first = True
            for line in f:
                if first:
                    first = False
                    # Header überspringen, wenn er das ist
                    if line.startswith("timestamp"):
                        continue
                line = line.rstrip("\n\r")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 4:
                    continue
                order_id = parts[2].strip()
                order_no = parts[3].strip()
                if order_id or order_no:
                    keys.add(f"{order_id}|{order_no}")
    except OSError as e:
        logging.warning("exported.log konnte nicht gelesen werden (%s) — "
                        "Fallback nur auf WooCommerce-Markierung.", e)
    return keys


def append_to_exported_log(shop_name: str, order_id, order_no,
                           wex_filename: str) -> None:
    """
    Hängt einen Log-Eintrag ans exported.log. Schreibt bei Bedarf den Header.
    Fehler beim Schreiben werden nur geloggt — der Ablauf soll NICHT
    scheitern, nur weil das Log nicht schreibbar ist.
    """
    try:
        new_file = not EXPORTED_LOG_PATH.exists()
        with EXPORTED_LOG_PATH.open("a", encoding="utf-8", newline="") as f:
            if new_file:
                f.write(_EXPORTED_LOG_HEADER)
            ts = datetime.now().isoformat(timespec="seconds")
            f.write(f"{ts}\t{shop_name}\t{order_id}\t{order_no}\t{wex_filename}\n")
    except OSError as e:
        logging.error("exported.log konnte NICHT geschrieben werden (%s) — "
                      "wenn WooCommerce-Markierung auch scheitert, kommt "
                      "die Bestellung beim nächsten Lauf nochmal!", e)


# ---------------------------------------------------------------------------
# Lock-Datei (verhindert parallele Läufe vom Netzlaufwerk)
# ---------------------------------------------------------------------------

def acquire_lock() -> bool:
    """
    Versucht, eine Lock-Datei im Skript-Ordner anzulegen. Gibt True zurück,
    wenn das geklappt hat (= wir dürfen laufen). False, wenn schon ein
    anderer Lauf aktiv ist.

    Alte Lock-Dateien (Prozess abgestürzt) werden nach LOCK_STALE_MINUTES
    automatisch ignoriert und überschrieben.
    """
    if LOCK_PATH.exists():
        age_sec = time.time() - LOCK_PATH.stat().st_mtime
        if age_sec < LOCK_STALE_MINUTES * 60:
            try:
                owner = LOCK_PATH.read_text(encoding="utf-8").strip()
            except OSError:
                owner = "unbekannt"
            print(f"Ein anderer Lauf ist bereits aktiv (seit {int(age_sec)}s, "
                  f"gestartet von: {owner}).")
            print("Bitte warte, bis er fertig ist, oder lösche "
                  f"{LOCK_PATH} falls er hängt.")
            return False
        else:
            # Lock ist zu alt — vermutlich ein abgestürzter Lauf.
            # Wir übernehmen.
            try:
                LOCK_PATH.unlink()
            except OSError:
                pass

    try:
        owner_info = f"{os.environ.get('COMPUTERNAME', '?')}\\" \
                     f"{os.environ.get('USERNAME', '?')} " \
                     f"PID {os.getpid()} @ {datetime.now().isoformat(timespec='seconds')}"
        LOCK_PATH.write_text(owner_info, encoding="utf-8")
        return True
    except OSError as e:
        print(f"Lock-Datei konnte nicht angelegt werden: {e}")
        return False


def release_lock() -> None:
    """Entfernt die Lock-Datei. Fehler werden ignoriert (best effort)."""
    try:
        LOCK_PATH.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Konfiguration laden (Welle 2: geteilte Dateien mit Rückfall)
# ---------------------------------------------------------------------------

def _load_split_config() -> dict:
    """Liest einstellungen.yaml + zugang.yaml und führt sie zusammen.

    Die Zugangsdaten (consumer_key/-secret) werden je Shop über den Namen
    wieder in die Shop-Einträge eingesetzt, sodass der Rest des Programms
    dieselbe Struktur wie bisher aus config.yaml sieht.
    """
    with EINSTELLUNGEN_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    with ZUGANG_PATH.open("r", encoding="utf-8") as f:
        zugang = yaml.safe_load(f) or {}

    secret_by_shop = zugang.get("shops") or {}
    for shop in cfg.get("shops", []):
        name = shop.get("name") or shop.get("url")
        creds = secret_by_shop.get(name) or {}
        for feld in ("consumer_key", "consumer_secret"):
            if feld in creds:
                shop[feld] = creds[feld]
    return cfg


def fehlende_zugangsdaten(shop_cfg: dict) -> list[str]:
    """Welche Zugangsfelder fehlen oder sind leer? Leer = alles da.

    Fehlt ein Shop in zugang.yaml (oder ist der Eintrag unvollständig), kommt
    er ohne consumer_key/-secret aus _load_split_config(). Ohne diese Prüfung
    endet das in einem KeyError beim Anlegen des WooClient.
    """
    return [f for f in ("consumer_key", "consumer_secret")
            if not str(shop_cfg.get(f) or "").strip()]


def config_vorhanden() -> bool:
    """Gibt es überhaupt eine Konfiguration (geteilt oder alt)?"""
    return (EINSTELLUNGEN_PATH.exists() and ZUGANG_PATH.exists()) \
        or CONFIG_PATH.exists()


def load_config() -> tuple[dict, str]:
    """Lädt die Konfiguration und liefert (cfg, quelle).

    Bevorzugt die geteilten Dateien (einstellungen.yaml + zugang.yaml).
    Fehlt eine davon, wird auf config.yaml zurückgefallen — so lange, bis
    die Migration vollständig durchgeführt wurde.
    """
    if EINSTELLUNGEN_PATH.exists() and ZUGANG_PATH.exists():
        if CONFIG_PATH.exists():
            logging.warning(
                "config.yaml liegt noch neben einstellungen.yaml/zugang.yaml — "
                "es gelten die neuen Dateien. Alte config.yaml nach Backup\\ "
                "verschieben (die Migration erledigt das normalerweise).")
        return _load_split_config(), "einstellungen.yaml + zugang.yaml"

    if (EINSTELLUNGEN_PATH.exists()) != (ZUGANG_PATH.exists()):
        fehlt = ZUGANG_PATH.name if EINSTELLUNGEN_PATH.exists() else EINSTELLUNGEN_PATH.name
        logging.warning("Nur eine der geteilten Konfigdateien vorhanden "
                        "(%s fehlt) — Rückfall auf config.yaml.", fehlt)

    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f), "config.yaml"

    raise FileNotFoundError("keine Konfiguration gefunden")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not config_vorhanden():
        print("Konfiguration fehlt: weder einstellungen.yaml + zugang.yaml "
              f"noch config.yaml in {BASE_DIR}", file=sys.stderr)
        print("Migration aus config.yaml: python migrate_config.py --probelauf",
              file=sys.stderr)
        return 2

    if not acquire_lock():
        return 3

    try:
        cfg, quelle = load_config()

        setup_logging(cfg.get("log_level", "INFO"))
        logging.info("Konfiguration geladen aus: %s", quelle)
        logging.info("=== Lauf gestartet (%s) von %s\\%s ===",
                     datetime.now().isoformat(),
                     os.environ.get("COMPUTERNAME", "?"),
                     os.environ.get("USERNAME", "?"))

        # Veredelungs-Präfixe aus der Config übernehmen, falls gesetzt.
        # Damit lässt sich ein neuer Präfix ohne EXE-Neubau nachtragen.
        cfg_prefixes = cfg.get("veredelung_prefixes")
        if cfg_prefixes:
            global VEREDELUNG_PREFIXES
            VEREDELUNG_PREFIXES = tuple(
                str(p).strip() for p in cfg_prefixes if str(p).strip()
            )
        logging.info("Veredelungs-Präfixe: %s",
                     ", ".join(VEREDELUNG_PREFIXES))

        total_ok = 0
        total_err = 0
        for shop_cfg in cfg.get("shops", []):
            if not shop_cfg.get("enabled", True):
                logging.info("Shop %s ist deaktiviert — übersprungen.",
                             shop_cfg.get("name", shop_cfg.get("url")))
                continue
            fehlt = fehlende_zugangsdaten(shop_cfg)
            if fehlt:
                logging.error("Shop %s: Zugangsdaten fehlen (%s) — in "
                              "zugang.yaml unter dem Shop-Namen eintragen. "
                              "Shop übersprungen.",
                              shop_cfg.get("name", shop_cfg.get("url")),
                              ", ".join(fehlt))
                total_err += 1
                continue
            try:
                s = process_shop(shop_cfg, cfg)
                total_ok += s["ok"]
                total_err += s["errors"]
            except requests.HTTPError as e:
                logging.error("Shop %s: API-Fehler: %s",
                              shop_cfg.get("name"), e)
                total_err += 1
            except Exception as e:  # noqa: BLE001
                logging.exception("Shop %s: unerwarteter Fehler: %s",
                                  shop_cfg.get("name"), e)
                total_err += 1

        logging.info("=== Lauf beendet: %d exportiert, %d Fehler ===",
                     total_ok, total_err)

        return 0 if total_err == 0 else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
