"""
TEXMA — WooCommerce API Diagnose
=================================

Einmal pro Shop ausführen, bevor das Hauptskript (woo_to_cdh.py) scharf geschaltet
wird. Das Skript prüft nichtinvasiv (nur Lese-Zugriffe), ob die API-Antwort so
aussieht, wie woo_to_cdh.py es erwartet. Ergebnis: eine lesbare Zusammenfassung
mit ✓ / ⚠ / ✗ pro Punkt.

Das Skript schreibt NICHTS nach WooCommerce und ändert NICHTS an CDH. Es ruft nur
ab und zeigt an.

Aufruf:
    python diagnose.py               # alle Shops aus der Konfiguration
    python diagnose.py CAF-Shop      # nur den Shop mit diesem Namen
"""

from __future__ import annotations

import json
import sys
from typing import Any

import requests

import woo_to_cdh as w   # gemeinsame Konfig-Ladefunktion (Welle 2)

OK = "\u2713"    # ✓
WARN = "\u26A0"  # ⚠
ERR = "\u2717"   # ✗


def line(ch: str = "─", n: int = 72) -> None:
    print(ch * n)


def bullet(symbol: str, text: str) -> None:
    print(f"  {symbol}  {text}")


def fmt_value(v: Any, maxlen: int = 80) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str)
    if len(s) > maxlen:
        s = s[:maxlen - 3] + "..."
    return s


def get(base: str, auth: dict, path: str,
        params: dict | None = None) -> tuple[int, Any]:
    """
    Liefert (status_code, json|text). Wirft nicht.

    auth ist ein dict mit consumer_key und consumer_secret, das als
    Query-Parameter mitgeschickt wird (Basic-Auth-Header wird vom
    IONOS-Hosting gestrippt).
    """
    url = f"{base}{path}"
    merged = {**(params or {}), **auth}
    try:
        r = requests.get(url, params=merged, timeout=30)
    except requests.RequestException as e:
        return 0, w.ohne_schluessel(e)     # URL mit Schlüsseln nie ausgeben
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


# ---------------------------------------------------------------------------
# Einzelne Prüfungen
# ---------------------------------------------------------------------------

def check_connection(base: str, auth: dict) -> bool:
    print("\n[1/5] Verbindung & Authentifizierung")
    status, data = get(base, auth, "/orders", params={"per_page": 1})
    if status == 200:
        bullet(OK, f"API erreichbar, Authentifizierung erfolgreich ({base})")
        return True
    if status == 401:
        bullet(ERR, "401 Unauthorized — Consumer Key/Secret falsch oder "
                   "ohne Leseberechtigung.")
    elif status == 404:
        bullet(ERR, f"404 — Shop-URL stimmt nicht. Versucht: {base}/orders")
    elif status == 0:
        bullet(ERR, f"Keine Verbindung: {data}")
    else:
        bullet(ERR, f"Unerwarteter Status {status}: {fmt_value(data)}")
    return False


def check_orders(base: str, auth: dict) -> list[dict]:
    """Holt bis zu 5 Bestellungen zur Analyse."""
    print("\n[2/5] Bestellungen abrufen (Status: processing, on-hold)")
    status, data = get(base, auth, "/orders", params={
        "status": "processing,on-hold",
        "per_page": 5,
        "orderby": "date",
        "order": "desc",
    })
    if status != 200 or not isinstance(data, list):
        bullet(ERR, f"Abruf fehlgeschlagen (Status {status})")
        return []
    if not data:
        bullet(WARN, "Keine offenen Bestellungen gefunden — das kann normal "
                     "sein, aber dann können wir Mapping-Felder nicht "
                     "verifizieren. Lege testweise eine Bestellung auf "
                     "'processing' oder 'on-hold', dann nochmal laufen lassen.")
        return []
    bullet(OK, f"{len(data)} offene Bestellung(en) gefunden")
    for o in data:
        already = any(m.get("key") == "_cdh_exported_at" and m.get("value")
                      for m in o.get("meta_data", []))
        flag = " [bereits exportiert]" if already else ""
        bullet("·", f"#{o.get('number')}  "
                    f"Status {o.get('status')}  "
                    f"{o.get('date_created', '')[:10]}  "
                    f"{len(o.get('line_items') or [])} Positionen{flag}")
    return data


def check_order_fields(order: dict) -> None:
    print("\n[3/5] Bestellfelder (Prüfung gegen erwartetes Mapping)")
    checks = [
        ("billing.email",        order.get("billing", {}).get("email")),
        ("shipping.company",     order.get("shipping", {}).get("company")),
        ("shipping.first_name",  order.get("shipping", {}).get("first_name")),
        ("shipping.last_name",   order.get("shipping", {}).get("last_name")),
        ("shipping.address_1",   order.get("shipping", {}).get("address_1")),
        ("shipping.postcode",    order.get("shipping", {}).get("postcode")),
        ("shipping.city",        order.get("shipping", {}).get("city")),
        ("date_created",         order.get("date_created")),
        ("number",               order.get("number")),
    ]
    for name, val in checks:
        if val:
            bullet(OK, f"{name:28s} = {fmt_value(val, 50)}")
        else:
            bullet(WARN, f"{name:28s} ist leer — prüfen, ob das bei dieser "
                          f"Bestellung stimmt (Gast-Bestellung? Privatperson?)")

    ships = order.get("shipping_lines") or []
    if ships:
        title = ships[0].get("method_title")
        bullet(OK, f"{'shipping_lines[0].method_title':28s} = {fmt_value(title, 50)}")
    else:
        bullet(WARN, "shipping_lines ist leer — keine Versandart hinterlegt.")


def check_line_items(order: dict) -> tuple[list[tuple], bool]:
    """
    Positionen durchgehen. Gibt eine Liste (product_id, variation_id, sku)
    zurück, die wir dann für den Dimensions-Check nutzen, plus einen
    Bool, ob wir überall einen Variantentext finden konnten.
    """
    print("\n[4/5] Positionen & Variantentexte")
    items = order.get("line_items") or []
    if not items:
        bullet(ERR, "Bestellung hat keine Positionen.")
        return [], False

    refs: list[tuple] = []
    all_have_variant_text = True

    for idx, item in enumerate(items, 1):
        sku = item.get("sku") or ""
        name = item.get("name") or ""
        qty = item.get("quantity")
        pid = item.get("product_id")
        vid = item.get("variation_id") or 0
        bullet("·", f"Pos {idx}: SKU={sku!r}  Menge={qty}  Name={name!r}")

        if not sku:
            bullet(ERR, f"   SKU fehlt — Bestellung würde übersprungen werden.")

        # Variantentext-Extraktion wie in woo_to_cdh.py
        found_variant = ""
        for m in item.get("meta_data") or []:
            k = (m.get("key") or "").lower()
            v = m.get("display_value") or m.get("value") or ""
            if k.startswith("_") or not v:
                continue
            if any(x in k for x in ("groesse", "size", "farbe", "color", "pa_")):
                found_variant = str(v).strip()
                break

        if vid:  # Variantenartikel
            if found_variant:
                bullet(OK, f"   Variantentext: {found_variant!r}  "
                           f"(key passend)")
            else:
                # Zeige, was tatsächlich da ist
                raw_metas = [(m.get("key"), m.get("display_value") or m.get("value"))
                             for m in (item.get("meta_data") or [])
                             if not (m.get("key") or "").startswith("_")]
                bullet(WARN, f"   Variantentext leer — sichtbare Meta-Keys: "
                              f"{raw_metas}")
                bullet("·", "   → Falls hier einer der Keys euren Größe/Variante "
                             "enthält, müssen wir den Filter im Hauptskript "
                             "erweitern.")
                all_have_variant_text = False
        else:
            # Kein Variantenartikel (z.B. Veredelung) — Variantentext darf leer sein
            bullet(OK, "   Kein Variantenartikel (variation_id=0) — "
                       "Variantentext wird leer bleiben, wie erwartet.")

        refs.append((pid, vid, sku))

    return refs, all_have_variant_text


def check_dimensions(base: str, auth: dict,
                     refs: list[tuple]) -> None:
    """
    Prüft, ob zu jedem Artikel EK (length) und VK (width) als Dimension
    gepflegt sind. Holt dafür jedes (product_id, variation_id).
    """
    print("\n[5/5] Preisdaten (EK aus length, VK aus width)")
    seen: set[tuple] = set()
    for pid, vid, sku in refs:
        if (pid, vid) in seen:
            continue
        seen.add((pid, vid))
        if vid:
            path = f"/products/{pid}/variations/{vid}"
        else:
            path = f"/products/{pid}"
        status, data = get(base, auth, path)
        if status != 200 or not isinstance(data, dict):
            bullet(ERR, f"SKU={sku!r}: Abruf fehlgeschlagen (Status {status})")
            continue
        dims = data.get("dimensions") or {}
        length = dims.get("length")
        width = dims.get("width")

        def parse(x):
            if x is None or x == "":
                return None
            try:
                return float(str(x).replace(",", "."))
            except ValueError:
                return None

        ek = parse(length)
        vk = parse(width)
        if ek is not None and vk is not None:
            bullet(OK, f"SKU={sku!r}: EK={ek}  VK={vk}")
        else:
            bullet(WARN, f"SKU={sku!r}: EK={length!r}  VK={width!r}  "
                          f"— Preiszellen in CDH werden leer bleiben. "
                          f"Im Shop Maße nachpflegen.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def diagnose_shop(shop_cfg: dict) -> None:
    name = shop_cfg.get("name", shop_cfg.get("url", "<unbenannt>"))
    line("═")
    print(f"  Shop: {name}")
    line("═")

    base = shop_cfg["url"].rstrip("/")
    if not base.endswith("/wp-json/wc/v3"):
        base = base + "/wp-json/wc/v3"
    auth = {
        "consumer_key":    shop_cfg["consumer_key"],
        "consumer_secret": shop_cfg["consumer_secret"],
    }

    if not check_connection(base, auth):
        print("\nAbbruch — Verbindung/Auth muss zuerst stimmen.")
        return

    orders = check_orders(base, auth)
    if not orders:
        print("\nKeine Bestellung zur weiteren Prüfung verfügbar.")
        return

    # Älteste der gefundenen 5 nehmen — die ist meistens "echt" und nicht
    # eine frisch angelegte Testbestellung mit leeren Feldern.
    sample = orders[-1]
    print(f"\n→ Beispiel-Bestellung #{sample.get('number')} "
          f"wird im Detail geprüft.")

    check_order_fields(sample)
    refs, all_variants_ok = check_line_items(sample)
    if refs:
        check_dimensions(base, auth, refs)

    print()
    line()
    print("  Zusammenfassung")
    line()
    if all_variants_ok:
        bullet(OK, "Variantentexte werden sauber erkannt.")
    else:
        bullet(WARN, "Mindestens ein Variantenartikel lieferte keinen "
                     "Variantentext mit dem Standard-Filter. "
                     "Die Meta-Keys oben prüfen und bei Bedarf den Filter "
                     "in woo_to_cdh.py (_extract_variant_text) anpassen.")
    print()


def lade_shops(nur: str | None = None) -> tuple[list[dict], str]:
    """Shops aus der Konfiguration laden (einstellungen.yaml + zugang.yaml,
    Rückfall auf config.yaml). Optional auf einen Shop-Namen filtern.

    Liefert (shops, quelle). Nutzt bewusst dieselbe Ladefunktion wie das
    Hauptskript, damit Diagnose und Import dieselbe Konfiguration sehen.
    """
    cfg, quelle = w.load_config()
    shops = cfg.get("shops") or []
    if nur:
        shops = [s for s in shops if s.get("name") == nur]
    return shops, quelle


def main() -> int:
    if not w.config_vorhanden():
        print("Konfiguration fehlt: weder einstellungen.yaml + zugang.yaml "
              "noch config.yaml.", file=sys.stderr)
        return 2

    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    shops, quelle = lade_shops(wanted)
    print(f"(Konfiguration geladen aus: {quelle})")

    if not shops:
        if wanted:
            print(f"Shop {wanted!r} nicht in der Konfiguration gefunden.",
                  file=sys.stderr)
        else:
            print("Keine Shops konfiguriert.", file=sys.stderr)
        return 2

    for s in shops:
        fehlt = w.fehlende_zugangsdaten(s)
        if fehlt:
            print(f"{ERR}  Shop {s.get('name', s.get('url'))}: Zugangsdaten "
                  f"fehlen ({', '.join(fehlt)}) — übersprungen.",
                  file=sys.stderr)
            continue
        diagnose_shop(s)

    return 0


if __name__ == "__main__":
    sys.exit(main())
