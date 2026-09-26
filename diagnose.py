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
    python diagnose.py Ensinger-Shop --felder
                                     # welche Zusatzfelder gibt es? (Namen,
                                     # Häufigkeit, Muster — keine Werte)
    python diagnose.py Ensinger-Shop --zubehoer
                                     # Pflicht-Zubehör-Regeln (Plugin) prüfen
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

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
# Diagnose als Funktion (Welle 4) — für den Shop-Assistenten der Oberfläche
# ---------------------------------------------------------------------------

@dataclass
class Pruefpunkt:
    titel: str
    stufe: str                     # "ok" | "warn" | "fehler"
    text: str
    details: list = field(default_factory=list)


def _basis_und_auth(shop_cfg: dict) -> tuple[str, dict]:
    base = str(shop_cfg["url"]).rstrip("/")
    if not base.endswith("/wp-json/wc/v3"):
        base += "/wp-json/wc/v3"
    return base, {"consumer_key": shop_cfg.get("consumer_key") or "",
                  "consumer_secret": shop_cfg.get("consumer_secret") or ""}


def _zahl(x) -> float | None:
    try:
        return None if x in (None, "") else float(str(x).replace(",", "."))
    except ValueError:
        return None


def diagnose(shop_cfg: dict,
             abruf: Callable[..., tuple[int, Any]] | None = None,
             max_bestellungen: int = 20) -> list[Pruefpunkt]:
    """
    Die fünf Prüfungen des Shop-Assistenten, nur lesend:
      Verbindung und Zugang · Bestellungen lesbar · Preise gepflegt ·
      Varianten erkannt · Versandarten

    shop_cfg braucht url, consumer_key, consumer_secret; optional name und
    included_statuses. abruf(path, params=None) -> (status, json) ersetzt
    den HTTP-Zugriff (Tests). Scheitert die Verbindung, kommt nur der
    erste Punkt zurück.
    """
    if abruf is None:
        base, auth = _basis_und_auth(shop_cfg)
        abruf = lambda path, params=None: get(base, auth, path, params)  # noqa: E731

    punkte: list[Pruefpunkt] = []

    # 1. Verbindung und Zugang
    status, data = abruf("/orders", {"per_page": 1})
    if status != 200:
        grund = {401: "Schlüssel falsch, im falschen Sub-Shop erzeugt oder "
                      "ohne Rechte (README Abschnitt 3)",
                 404: "Shop-Adresse stimmt nicht (URL-Slug prüfen)",
                 0: f"keine Verbindung ({data})"}.get(
            status, f"unerwarteter Status {status}")
        punkte.append(Pruefpunkt("Verbindung und Zugang", "fehler", grund))
        return punkte
    punkte.append(Pruefpunkt("Verbindung und Zugang", "ok",
                             "Shop erreichbar, Schlüssel gültig"))

    # 2. Bestellungen lesbar
    statuses = shop_cfg.get("included_statuses") or w.INCLUDED_STATUSES
    status, orders = abruf("/orders", {"status": ",".join(statuses),
                                       "per_page": max_bestellungen,
                                       "orderby": "date", "order": "desc"})
    if status != 200 or not isinstance(orders, list):
        punkte.append(Pruefpunkt("Bestellungen lesbar", "fehler",
                                 f"Abruf fehlgeschlagen (Status {status})"))
        orders = []
    elif not orders:
        punkte.append(Pruefpunkt("Bestellungen lesbar", "warn",
                                 "Keine offenen Bestellungen — Preise und "
                                 "Varianten lassen sich noch nicht prüfen"))
    else:
        n = len(orders)
        punkte.append(Pruefpunkt(
            "Bestellungen lesbar", "ok",
            f"{n}{'+' if n >= max_bestellungen else ''} offene "
            f"Bestellung{'en' if n != 1 else ''} gefunden"))

    items = [it for o in orders for it in (o.get("line_items") or [])]

    # 3. Preise gepflegt (EK = Länge, VK = Breite)
    if items:
        artikel: dict = {}
        for it in items:
            artikel.setdefault((it.get("product_id"), it.get("variation_id") or 0),
                               it.get("sku") or "?")
        ohne_ek, ohne_vk, fehler = [], [], []
        for (pid, vid), sku in artikel.items():
            path = f"/products/{pid}/variations/{vid}" if vid else f"/products/{pid}"
            st, prod = abruf(path)
            if st != 200 or not isinstance(prod, dict):
                fehler.append(sku)
                continue
            dims = prod.get("dimensions") or {}
            if _zahl(dims.get(w.EK_FIELD)) is None:
                ohne_ek.append(sku)
            if _zahl(dims.get(w.VK_FIELD)) is None:
                ohne_vk.append(sku)
        n = len(artikel)
        if ohne_ek or ohne_vk or fehler:
            teile = []
            if ohne_ek:
                teile.append(f"EK fehlt bei {len(ohne_ek)} von {n} Artikeln")
            if ohne_vk:
                teile.append(f"VK fehlt bei {len(ohne_vk)} von {n} Artikeln")
            if fehler:
                teile.append(f"{len(fehler)} Artikel nicht abrufbar")
            punkte.append(Pruefpunkt(
                "Preise gepflegt", "warn",
                "; ".join(teile) + " — bleibt in CDH leer",
                sorted(set(ohne_ek + ohne_vk + fehler))))
        else:
            punkte.append(Pruefpunkt("Preise gepflegt", "ok",
                                     f"EK und VK bei allen {n} Artikeln"))
    else:
        punkte.append(Pruefpunkt("Preise gepflegt", "warn",
                                 "Keine Bestellung zum Prüfen"))

    # 4. Varianten erkannt (gleiche Logik wie beim Import)
    varianten = [it for it in items if it.get("variation_id")]
    if varianten:
        ohne = sorted({it.get("sku") or "?" for it in varianten
                       if not w._extract_variant_text(it)})
        if ohne:
            punkte.append(Pruefpunkt(
                "Varianten erkannt", "warn",
                f"Kein Variantentext bei {len(ohne)} Artikel(n) — Meta-Keys prüfen",
                ohne))
        else:
            punkte.append(Pruefpunkt("Varianten erkannt", "ok",
                                     "Farbe und Größe bei allen Artikeln"))
    else:
        punkte.append(Pruefpunkt("Varianten erkannt", "warn" if not items else "ok",
                                 "Keine Bestellung zum Prüfen" if not items
                                 else "Keine Variantenartikel in den Bestellungen"))

    # 5. Versandarten (+ Abgleich mit lieferadressen.yaml, falls vorhanden)
    def json_or_raise(path):
        st, d = abruf(path)
        if st != 200:
            raise RuntimeError(f"Status {st}")
        return d

    try:
        va = w.versandarten_aus_zonen(json_or_raise)
    except Exception as e:  # noqa: BLE001
        punkte.append(Pruefpunkt("Versandarten", "warn",
                                 f"Nicht abrufbar ({w.ohne_schluessel(e)})"))
        return punkte
    if not va:
        punkte.append(Pruefpunkt("Versandarten", "warn",
                                 "Keine aktive Versandart gefunden"))
        return punkte
    orte = w.load_delivery_address_names().get(shop_cfg.get("name") or "", [])
    ab = w.versandarten_abgleich(va, orte)
    details = []
    if orte and ab["ohne_versandart"]:
        details.append("Adresse ohne Versandart: " + ", ".join(ab["ohne_versandart"]))
    if orte and ab["ohne_adresse"]:
        details.append("Versandart ohne Adresse: " + ", ".join(ab["ohne_adresse"]))
    punkte.append(Pruefpunkt(
        "Versandarten", "warn" if details else "ok",
        f"{len(va)} gefunden: {', '.join(va)}", details))
    return punkte


# ---------------------------------------------------------------------------
# Feldübersicht — welche Meta-Felder liefert der Shop? (Frage 6)
# ---------------------------------------------------------------------------

def muster(wert: Any) -> str:
    """Form eines Werts ohne den Wert selbst: 4711 → ####, Ja → xx.
    Personalnummern und Namen sind personenbezogen und gehören nicht in
    Konsole oder Chat."""
    if isinstance(wert, (list, dict)):
        return "(Liste)"
    s = str(wert if wert is not None else "").strip()
    if not s:
        return "(leer)"
    m = "".join("#" if c.isdigit() else "x" if c.isalpha() else c for c in s)
    return m if len(m) <= 16 else m[:16] + "…"


def felder_uebersicht(abruf: Callable[..., tuple[int, Any]],
                      anzahl: int = 50) -> dict:
    """Alle Meta-Felder der letzten `anzahl` Bestellungen (jeder Status), an
    der Bestellung und an den Positionen. Nur lesend. Je Feld: key,
    display_key, in wie vielen Bestellungen, Muster der Werte."""
    status, orders = abruf("/orders", {"per_page": anzahl,
                                       "orderby": "date", "order": "desc"})
    if status != 200 or not isinstance(orders, list):
        return {"fehler": f"Abruf fehlgeschlagen (Status {status})"}
    felder: dict = {"bestellung": {}, "position": {}}

    def merken(ziel, m, oid):
        key = str(m.get("key") or "")
        f = ziel.setdefault(key, {"key": key, "anzeige": "", "bestellungen": set(),
                                  "muster": set()})
        f["anzeige"] = f["anzeige"] or str(m.get("display_key") or "")
        f["bestellungen"].add(oid)
        f["muster"].add(muster(m.get("display_value", m.get("value"))))

    for o in orders:
        for m in o.get("meta_data") or []:
            merken(felder["bestellung"], m, o.get("id"))
        for it in o.get("line_items") or []:
            for m in it.get("meta_data") or []:
                merken(felder["position"], m, o.get("id"))

    def liste(d):
        out = [{"key": f["key"], "anzeige": f["anzeige"],
                "bestellungen": len(f["bestellungen"]),
                "muster": sorted(f["muster"])[:4],
                "kandidat": "personal" in (f["key"] + f["anzeige"]).lower()}
               for f in d.values()]
        return sorted(out, key=lambda f: (-f["bestellungen"], f["key"]))

    return {"anzahl": len(orders), "bestellung": liste(felder["bestellung"]),
            "position": liste(felder["position"])}


def felder_ausgeben(shop_cfg: dict) -> None:
    base, auth = _basis_und_auth(shop_cfg)
    erg = felder_uebersicht(lambda path, params=None: get(base, auth, path, params))
    line("═")
    print(f"  FELDER — {shop_cfg.get('name', shop_cfg['url'])}")
    line("═")
    if "fehler" in erg:
        bullet(ERR, erg["fehler"])
        return
    print(f"  Letzte {erg['anzahl']} Bestellungen, jeder Status. Nur Feldnamen und "
          "Muster (# Ziffer, x Buchstabe), keine Werte.")
    for titel, teil in (("An der Bestellung (Checkout-Felder)", "bestellung"),
                        ("An der Position (PPOM-Felder)", "position")):
        print(f"\n  {titel}:")
        if not erg[teil]:
            print("    (keine)")
        for f in erg[teil]:
            pfeil = "  ← Personalnummer?" if f["kandidat"] else ""
            anzeige = f" „{f['anzeige']}“" if f["anzeige"] and f["anzeige"] != f["key"] else ""
            print(f"    {f['key']}{anzeige}  ·  in {f['bestellungen']} Bestellungen"
                  f"  ·  {', '.join(f['muster'])}{pfeil}")
    print()


# ---------------------------------------------------------------------------
# Pflicht-Zubehör — liefert der Shop die Regeln? (Welle 9, nur lesend)
# ---------------------------------------------------------------------------

def zubehoer_uebersicht(abruf: Callable[..., tuple[int, Any]]) -> dict:
    """Alle Produkte lesen und die Zubehör-Regeln des Plugins „CDH Required
    Accessories" zeigen. Findet sie nichts, obwohl im Backend Zubehör
    gepflegt ist, gibt die Schnittstelle das Feld nicht heraus."""
    produkte, seite = [], 1
    while True:
        status, teil = abruf("/products", {"per_page": 100, "page": seite})
        if status != 200 or not isinstance(teil, list):
            return {"fehler": f"Abruf fehlgeschlagen (Status {status})"}
        produkte += teil
        if len(teil) < 100:
            break
        seite += 1
    nach_id = {p.get("id"): p for p in produkte}
    regeln, zubehoer_ids = [], set()
    for p in produkte:
        r = w._zubehoer_regeln(p)
        if not r:
            continue
        zeilen = []
        for acc, per in r:
            z = nach_id.get(acc)
            zubehoer_ids.add(acc)
            if z is None:
                zeilen.append(("?", per, f"Zubehör {acc} nicht im Shop gefunden"))
            elif z.get("type") != "simple" or z.get("status") != "publish":
                zeilen.append((z.get("sku") or "?", per,
                               "kein einfaches, veröffentlichtes Produkt"))
            else:
                zeilen.append((z.get("sku") or "?", per, "" if z.get("sku") else "ohne Artikelnummer"))
        regeln.append({"sku": p.get("sku") or "", "name": p.get("name") or "", "zubehoer": zeilen})
    sichtbar = sorted(nach_id[i].get("sku") or str(i) for i in zubehoer_ids
                      if i in nach_id and nach_id[i].get("catalog_visibility", "visible") != "hidden")
    return {"produkte": len(produkte), "regeln": regeln, "sichtbar": sichtbar}


def zubehoer_ausgeben(shop_cfg: dict) -> None:
    base, auth = _basis_und_auth(shop_cfg)
    erg = zubehoer_uebersicht(lambda path, params=None: get(base, auth, path, params))
    line("═")
    print(f"  PFLICHT-ZUBEHÖR — {shop_cfg.get('name', shop_cfg['url'])}")
    line("═")
    if "fehler" in erg:
        bullet(ERR, erg["fehler"])
        return
    print(f"  {erg['produkte']} Produkte gelesen, {len(erg['regeln'])} mit Zubehör-Regel.")
    if not erg["regeln"]:
        bullet(WARN, "Keine Regel gefunden. Ist im Backend Zubehör gepflegt, gibt die "
                     "Schnittstelle das Feld nicht heraus — dann Bescheid geben.")
    for r in erg["regeln"]:
        teile = [f"{menge:g} × {sku}" + (f" ({hinweis})" if hinweis else "")
                 for sku, menge, hinweis in r["zubehoer"]]
        symbol = WARN if any(h for _s, _m, h in r["zubehoer"]) else OK
        bullet(symbol, f"{r['sku'] or '?'} {r['name']}: " + ", ".join(teile))
    if erg["sichtbar"]:
        bullet(WARN, "Im Katalog noch sichtbar (Katalogsichtbarkeit auf „Versteckt“ "
                     "stellen): " + ", ".join(erg["sichtbar"]))
    print()


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

    argumente = [a for a in sys.argv[1:] if not a.startswith("--")]
    nur_felder = "--felder" in sys.argv[1:]
    nur_zubehoer = "--zubehoer" in sys.argv[1:]
    wanted = argumente[0] if argumente else None
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
        if nur_felder:
            felder_ausgeben(s)
        elif nur_zubehoer:
            zubehoer_ausgeben(s)
        else:
            diagnose_shop(s)

    return 0


if __name__ == "__main__":
    sys.exit(main())
