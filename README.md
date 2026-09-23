# Technische Dokumentation · WooCommerce → CDH Import

Stand: September 2026

Automatisierter Import von WooCommerce-Bestellungen aus den TEXMA-Kundenshops
in CDH Office. Ersetzt die frühere Kette aus Export-Plugin, Mailversand,
Ordnerablage und PowerShell-Zwischenprogramm durch einen Doppelklick.

---

## 0. Repository und Entwicklung

Quelltext: `github.com/jbtx21/woo-to-cdh` (privat). Das Repo enthält **nur Code,
Vorlagen, Tests und Doku**. Betriebsdaten liegen ausschließlich auf V:.

| Im Repo | Nur auf V: |
|---|---|
| `woo_to_cdh.py`, `launcher.py`, `diagnose.py`, `adressen_gui.py`, `xlsx_to_wex.py` | `config.yaml` (Zugangsdaten) |
| `config.sample.yaml`, `lieferadressen.sample.yaml` | `lieferadressen.yaml` (Ansprechpartner) |
| `tests/` mit anonymisierten Testbestellungen | `exported.log`, `logs\`, `wex-archiv\`, `excel-archiv\` |
| `docs/` mit Entwurf und Briefing | EXE-Dateien |

Die `.gitignore` sorgt dafür, dass die rechte Spalte nicht versehentlich
committet wird. Ein Test prüft zusätzlich, dass keine echten API-Schlüssel im
Repo stehen.

**Einrichten:**

```powershell
git clone https://github.com/jbtx21/woo-to-cdh.git C:\TEXMA\woo-to-cdh
cd C:\TEXMA\woo-to-cdh
pip install -r requirements-dev.txt
Copy-Item "V:\Warenwirtschaftssystem\WooCommerce Import\config.yaml" .   # bleibt lokal
python -m pytest -q
```

**Bauen und ausrollen:** `.\build.ps1` baut nur, `.\build.ps1 -Deploy` kopiert
zusätzlich nach V:. Das Skript bricht ab, wenn Tests rot sind oder eine YAML auf
V: kaputt ist.

**Golden-WEX:** `tests/golden/` enthält WEX-Dateien für feste Testbestellungen.
Weicht die Ausgabe ab, schlägt der Test fehl — jede Formatänderung wird so
sichtbar. Absichtliche Änderung:

```powershell
$env:UPDATE_GOLDEN=1; python -m pytest tests/test_golden.py; Remove-Item Env:UPDATE_GOLDEN
git diff tests/golden/
```

Weitere Entwicklung nach `docs/BRIEFING_IMPORT_PROGRAMM.md`.

---

## 1. Was das Programm tut

Pro Lauf:

1. Holt per WooCommerce-REST-API die offenen Bestellungen jedes aktivierten Shops.
2. Zieht zu jeder Position EK und VK aus den Variantendaten.
3. Schreibt eine WEX-Datei (XML) ins `wex-archiv\` und eine gleichnamige
   XLSX ins `excel-archiv\`.
4. Vermerkt die Bestellung lokal in `exported.log`, markiert sie in
   WooCommerce (Meta `_cdh_exported_at`) und setzt den Status.
5. Startet `CDH_WEX.EXE` mit der WEX-Datei und wartet, bis der Benutzer im
   CDH-Fenster auf „Ende" klickt.

Seit Welle 3 in zwei Schritten, die auch die künftige Oberfläche nutzt:

- **`abrufen(einstellungen)`** — nur lesend (Schritte 1–2). Holt alle
  aktiven Shops, baut die WEX-Daten und liefert ein `Pruefergebnis`: je Shop
  die Einheiten (eine Bestellung bzw. eine Lieferort-Gruppe), Warnungen und
  Sperren. Statusfilter, Importtage (mit `trotzdem`) und Duplikatschutz sind
  berücksichtigt. Schreibt nichts.
- **`importieren(auswahl, fortschritt_callback, abbruch_flag)`** — Schritte
  3–5 je Einheit, strikt nacheinander. Abbruch nur zwischen zwei Einheiten.
  Vor jeder Einheit wird `exported.log` noch einmal geprüft, falls ein
  anderer Arbeitsplatz inzwischen importiert hat.

Die Konsolen-EXE ruft beides nacheinander für alles auf. Dadurch werden
erst **alle** Shops abgerufen, dann importiert; am Ende steht im Log je Shop
„N Bestellung(en) exportiert".

Schritt 5 blockiert bewusst: `CDH_WEX.EXE` darf nur einmal gleichzeitig
laufen, sonst meldet CDH „WEX Importer bereits ausgeführt" und der zweite
Auftrag geht verloren. Drei Sicherungen:

1. Vor jedem Start prüft das Skript per `tasklist`, ob schon ein
   CDH-Import offen ist — etwa von Hand aus dem `wex-archiv` gestartet —
   und wartet, bis er geschlossen ist.
2. `subprocess.run` kehrt erst zurück, wenn das Fenster geschlossen ist.
3. Danach wird noch einmal geprüft, falls `CDH_WEX.EXE` nur ein Starter
   für ein weiteres Programm ist.

Bleibt ein CDH-Fenster länger als 15 Minuten offen, wird der Auftrag nicht
übergeben. Die WEX-Datei liegt dann im `wex-archiv` und lässt sich per
Doppelklick nachholen; im Log steht, welche.

**Nicht `subprocess.Popen` verwenden.** Das wartet nicht und startet alle
Importe eines Laufs gleichzeitig.

---

## 2. Ablageorte

Alles liegt auf dem Netzlaufwerk, damit jeder Arbeitsplatz mit CDH den Import
auslösen kann.

```
V:\Warenwirtschaftssystem\WooCommerce Import\
│                (= \\SERVER2019TEX\Verwaltung\Warenwirtschaftssystem\...)
├── WOO_to_CDH.exe        Startdatei für die Mitarbeiter
├── woo_to_cdh.py         Hauptskript
├── launcher.py           Einstiegspunkt für den EXE-Build
├── diagnose.py           Prüfskript, ändert nichts
├── config.yaml           Shops, Zugangsdaten, Optionen (nur Admin)
├── lieferadressen.yaml   Feste Lieferadressen (Innendienst)
├── Lieferadressen.exe    Oberfläche zum Pflegen der Adressen
├── exported.log          Historie aller Exporte (TSV)
├── running.lock          Sperrdatei während eines Laufs
├── logs\                 woo_to_cdh.log
├── wex-archiv\           erzeugte WEX-Dateien
└── excel-archiv\         erzeugte Kontroll-XLSX
```

Entwicklungsumgebung lokal: `C:\TEXMA\woo-cdh\`. Dort liegen dieselben
Dateien plus `dist\` für den EXE-Build.

Die EXE sucht `config.yaml` und die Unterordner **im eigenen Verzeichnis**.
Liegt sie woanders als die Config, bricht der Lauf mit „config.yaml fehlt" ab.

---

## 3. Authentifizierung

Das Skript nutzt **Query-Parameter-Auth**:

```
?consumer_key=ck_...&consumer_secret=cs_...
```

Kein Basic Auth. Grund: Das Hosting (IONOS) entfernt den
`Authorization`-Header, bevor er PHP erreicht — Basic Auth scheitert dort
reproduzierbar mit 401.

**Voraussetzung im WordPress-Multisite:** Das Snippet „Fix WooCommerce Caps"
muss netzwerkweit aktiv bleiben. Ohne das bekommen die API-Schlüssel nicht die
nötigen Rechte, egal wie sie angelegt wurden.

**Pro Sub-Shop zu prüfen, wenn ein neuer Shop 401 liefert:**

- Wurden die Schlüssel im jeweiligen Sub-Shop-Admin erzeugt, nicht im
  Netzwerk-Admin?
- Ist der Benutzer in diesem Sub-Shop überhaupt als Benutzer angelegt?
  Im Multisite ist die Zugehörigkeit pro Subsite.
- Stimmt der URL-Slug? Der Allgaier-Shop heißt `/allgaier/`, nicht
  `/allgaier-shop/` wie die übrigen.

---

## 4. Preisfelder im Shop

WooCommerce kennt von Haus aus keinen EK. Deshalb liegen beide Preise in den
Maßfeldern der Variante:

| Feld im Shop  | WooCommerce-API      | Bedeutung |
|---------------|----------------------|-----------|
| Länge (cm)    | `dimensions.length`  | **EK**    |
| Breite (cm)   | `dimensions.width`   | **VK**    |

Fehlt ein Wert, bleibt das Preisfeld im CDH-Auftrag leer. `diagnose.py` meldet
solche Artikel mit ⚠.

---

## 5. WEX-Format

### Adressblöcke

Die häufigste Fehlerquelle. Beide Blöcke haben eine feste Bedeutung:

| Block        | Inhalt                          | Quelle in WooCommerce |
|--------------|----------------------------------|-----------------------|
| `<Sender>`   | Hauptkunde, gehört zur DatevNo   | **Rechnungsadresse**  |
| `<Delivery>` | Wohin die Ware geht              | **Versandadresse**    |

Erfassen die Besteller keine Rechnungsadresse — typisch für Mitarbeiter-
shops wie Ensinger — bleiben die WooCommerce-Felder leer und der Sender
ginge ohne Anschrift an CDH. Dagegen gibt es `sender_address` pro Shop:

```yaml
    sender_address:
      name1:    Ensinger GmbH
      street:   Rudolf-Diesel-Str. 8
      postcode: '71154'
      city:     Nufringen
      country:  DE
```

Die Werte stammen aus dem CDH-Kundenstammsatz zur jeweiligen DatevNo und
überschreiben die Rechnungsadresse aus dem Shop.

`Name2` bleibt im Sender leer. Stünde dort der Besteller, würde CDH bei jeder
Bestellung den Ansprechpartner im Kundenstammsatz überschreiben. Der Name des
Empfängers steht im Delivery-Block.

`ModeOfShippment` trägt den Lieferort — bei Allgaier und Ensinger ist das die
Versandart im Shop, also der Standort (z.B. „Bondorf", „Cham").

### Preis-Tags

```xml
<SellingPrice>53.59</SellingPrice>   <!-- VK -->
<BuyingPrice>33.64</BuyingPrice>     <!-- EK -->
```

**Nicht** `<PurchasePrice>`. Das Tag existiert im WEX-Standard nicht und wird
von CDH kommentarlos ignoriert — der EK bleibt dann leer, ohne Fehlermeldung.

### Variantentext

`DescriptionText2` enthält Farbe und Größe, in dieser Reihenfolge:
`mindful blue, M`. Liegt nur eines der beiden vor, steht nur das dort
(`4XL` bei Allgaier). Die Sortierung erfolgt nach Bedeutung, nicht nach der
Reihenfolge in den WooCommerce-Metadaten — die ist nicht garantiert.

In der Excel steht dieselbe Information beschriftet:
`Farbe: mindful blue, Größe: M`.

### Bestellnummer

Bei `order_no_with_name: true` enthält `<OrderNo>` zusätzlich den Empfänger:

```xml
<OrderNo>3939 Anna Weber</OrderNo>
```

Nur im Standard-Modus. Im Sammel-Modus steht dort bereits `3935+3936+…`.

---

## 6. Die zwei Betriebsmodi

### Standard-Modus

Voreinstellung. Eine Bestellung → eine WEX → ein CDH-Auftrag.

Dateiname: `orders-2026-09-14-3939.wex`

Aktive Shops: CAF, Stoll, Xond, SFS, KVSW

### Sammel-Modus (`combine_by_delivery: true`)

Für Firmenshops mit mehreren Bestellern pro Standort. Alle offenen
Bestellungen eines Laufs werden nach Lieferort gruppiert; pro Lieferort
entsteht **eine** WEX.

Dateiname: `orders-2026-09-14-allgaier-shop-Bondorf.wex`

Innerhalb des Sammel-Modus gibt es zwei Ausprägungen.

#### Mit Trennzeilen (Allgaier)

Voreinstellung. Die Zuordnung Person → Artikel bleibt im CDH-Auftrag sichtbar.

```
Trennzeile   3939 / Anna Weber
  2x  396/9554ALL-195-4XL   Safety T-Shirt
  …
Trennzeile   3940 / Tim Roth
  1x  396/8021ALL-241-4XL   Safety Sweatshirt
  …
Trennzeile   Veredelungen / bestellungsübergreifend
 14x  234/SRFX-50-AllgBr    Silberreflex Logo Allgaier
```

Trennzeilen sind Positionen ohne Artikelnummer, Menge 1, Preis 0,00. CDH
akzeptiert das leere `<ArticleNo1 />`.

#### Voll zusammengefasst (`aggregate_all_positions: true`, Ensinger)

Keine Trennzeilen pro Bestellung. Alle gleichen Artikelvarianten werden über
alle Bestellungen des Lieferorts addiert. Der CDH-Auftrag zeigt nur noch
Gesamtmengen — passend, wenn die Ware gesammelt an den Standort geht und dort
verteilt wird.

```
Trennzeile   Cham / 3 Bestellungen
  5x  ENS-STSU177-M1-C002-S   Unisex Hoody (black)
  2x  ENS-STSU177-M1-C002-L   Unisex Hoody (black)
  2x  ENS-0527-WEISS-M        Poloshirt Damen (weiß)
```

Zusammengefasst wird, wenn Artikelnummer, Variantentext **und** beide Preise
übereinstimmen. Unterschiedliche Größen haben eigene Artikelnummern und
bleiben getrennt.

**Die Zuordnung Person → Artikel fehlt in diesem Modus im CDH-Auftrag.** Sie
steht in der Excel-Datei, die weiterhin jede Position einzeln mit Empfänger
auflistet.

Bestellungen ohne Versandart landen in der Gruppe `ohne-lieferort`.

Adressen im Sammel-Modus: In **beiden** Blöcken steht die Firmenadresse. Ein
Sammelauftrag bündelt mehrere Empfänger — die Privatadresse der ersten
Bestellung wäre dort falsch. Der Standort steht in `ModeOfShippment`.

Aus demselben Grund bleibt `<Email>` im Sammel-Modus leer, außer
`sender_address` enthält `email` (Entscheidung 23.09.2026). Im
Standard-Modus steht weiter die E-Mail aus der Rechnungsadresse.

Aktive Shops: Allgaier (mit Trennzeilen), Ensinger (voll zusammengefasst)

---

## 6a. Import nur an bestimmten Tagen

Manche Shops werden gesammelt zu einem Stichtag abgerechnet. Über
`import_on_days` wird der Shop nur an den genannten Tagen im Monat
abgefragt:

```yaml
    import_on_days: [1]        # nur am Monatsersten
```

Die Prüfung steht ganz am Anfang — an anderen Tagen erfolgt keine
API-Abfrage. Im Log steht dann:

```
[Ensinger-Shop] Übersprungen: Import nur am 1. des Monats, heute ist der 17.
```

Ohne Eintrag läuft der Shop bei jedem Aufruf.

**Zu bedenken:** Fällt der Stichtag auf ein Wochenende oder klickt niemand,
wartet der Shop bis zum nächsten Termin. Verloren geht nichts — die
Bestellungen sammeln sich und kommen beim nächsten Lauf mit. Wer das
vermeiden will, trägt mehrere Tage ein (`[1, 2, 3]`); die Duplikat-
sicherung verhindert Doppelimporte.

Aktiv bei: Ensinger (`[1]`)

---

## 6b. Feste Lieferadressen

Für jeden Lieferort lässt sich eine feste Versandadresse hinterlegen. Sie
überschreibt beim Import den `<Delivery>`-Block — unabhängig davon, was der
Besteller im Shop eingetragen hat.

Gepflegt wird das in `lieferadressen.yaml`, einer eigenen Datei **ohne
Zugangsdaten**. Der Innendienst bearbeitet sie über `Lieferadressen.exe`;
die `config.yaml` mit den API-Schlüsseln bleibt Admin-Sache.

```yaml
Ensinger-Shop:
  Cham:
    name1:    Ensinger GmbH
    name2:    z. Hd. Empfang
    street:   Wilfried-Ensinger-Straße 1
    postcode: '93413'
    city:     Cham
    country:  DE
```

Der Schlüssel muss der Versandart im Shop entsprechen; Groß-/Kleinschreibung
und Leerzeichen am Rand spielen keine Rolle. Im Standard-Modus behalten
Lieferorte ohne Eintrag die Adresse aus der Bestellung. Im Sammel-Modus
entscheidet `unknown_delivery` (Abschnitt 6c), Standard ist die
CDH-Standardadresse.

**Abgleich mit den Versandarten:** Bei Sammel-Shops und Shops mit
hinterlegten Adressen holt der Abruf die Versandarten aus den
WooCommerce-Versandzonen und meldet als Hinweis:

- Versandarten ohne feste Lieferadresse (nur Sammel-Modus),
- Adressen, zu denen es keine Versandart gibt — meist ein Tippfehler wie
  „Seewalchen" statt „Seewalchen (Österreich)". Solche Adressen greifen nie.

Hinterlegt für Ensinger: Nufringen, Cham, Rottenburg-Ergenzingen, Garbsen,
Seewalchen (AT).

### Das Adressen-Tool

`Lieferadressen.exe` liegt neben der Import-EXE. Es zeigt links die Shops
und deren Lieferorte, rechts das Formular. Beim Speichern wird die alte
Datei nach `Backup\` kopiert und unvollständige Einträge werden gemeldet.

Aus der `config.yaml` liest das Tool ausschließlich die Shop-Namen, keine
Zugangsdaten. Gebaut wird es mit:

```powershell
python -m PyInstaller --onefile --windowed --name Lieferadressen adressen_gui.py
```

`--windowed` unterdrückt das schwarze Konsolenfenster.

---

## 6c. Prüfregeln beim Abruf

Der Abruf (`abrufen`) prüft vor dem Import. Gesperrtes wird nicht
importiert und zählt im Konsolenlauf als Fehler.

Grundsatz (Entscheidung 23.09.2026): **Fehlt eine Adresse, bleibt die
Anschrift im WEX leer und CDH nimmt die Standardadresse aus dem
Kundenstamm zur DatevNo.** `DatevNo` und `ModeOfShippment` bleiben stehen.

| Regel | Folge |
|---|---|
| Kundenadresse (Sender: Firma, Straße, PLZ, Ort) unvollständig oder „BITTE EINTRAGEN" | Sender-Anschrift leer → CDH-Standardadresse, Hinweis |
| Lieferanschrift unvollständig | Delivery-Anschrift leer → CDH-Standardadresse, Hinweis |
| Sammel-Modus, Lieferort ohne feste Adresse | je `unknown_delivery`: `cdh` (Standard, Anschrift leer → CDH-Standardadresse), `firma` (Kundenadresse), `versand` (Versandadresse der ersten Bestellung), `sperren` (dieser Lieferort gesperrt) |
| EK fehlt bei einem Artikel | nur Hinweis, der EK bleibt in CDH leer |

**Noch zu bestätigen am echten CDH** (offene Frage 2): dass CDH bei leerer
Anschrift tatsächlich die Standardadresse nimmt und den Kundenstamm nicht
leer überschreibt. Erster Test mit einer Testbestellung.

---

## 7. Veredelungen

Positionen, deren Artikelnummer mit einem konfigurierten Präfix beginnt,
werden zusammengefasst, sofern Artikelnummer **und** beide Preise identisch
sind. Bei abweichenden Preisen bleiben sie getrennt.

| Präfix | Bedeutung     |
|--------|---------------|
| `004/` | Stick         |
| `316/` | Stick         |
| `006/` | Transferdruck |
| `234/` | Silberreflex  |

Gepflegt in der `config.yaml` unter `veredelung_prefixes`. Ein neuer Präfix
braucht **keinen EXE-Neubau** — die Liste wird bei jedem Start gelesen und ins
Log geschrieben.

Reichweite der Zusammenfassung:

- Standard-Modus: innerhalb einer Bestellung
- Sammel-Modus: über alle Bestellungen eines Lieferorts

Bei `aggregate_all_positions: true` entfällt der eigene Veredelungs-Block —
dort werden Veredelungen wie alle anderen Positionen behandelt und
mitaggregiert.

In der Excel-Datei wird **nicht** zusammengefasst. Die Produktion muss sehen,
welche Bestellung wie viele Stickereien braucht.

---

## 8. Schutz gegen Doppelimporte

Zwei unabhängige Quellen, beide werden vor jedem Export geprüft:

1. **WooCommerce-Meta** `_cdh_exported_at` — kommt direkt mit dem API-Abruf.
2. **Lokales `exported.log`** — TSV mit Zeitstempel, Shop, Bestell-ID,
   Bestellnummer und WEX-Dateiname.

Reihenfolge beim Export ist bewusst so gewählt:

```
WEX schreiben → exported.log → mark_exported → Statuswechsel → CDH-Import
```

Das lokale Log wird **vor** dem API-Aufruf geschrieben. Wenn WooCommerce
danach nicht erreichbar ist, greift trotzdem die lokale Sperre — die
Bestellung kommt beim nächsten Lauf nicht erneut. Ein fehlgeschlagener
`mark_exported` ist deshalb nur eine Warnung, kein Fehler.

`exported.log` wächst um rund 120 Byte pro Bestellung. Bei 100 Bestellungen
täglich sind das etwa 4 MB pro Jahr — unkritisch.

**Parallele Läufe** verhindert `running.lock` — auch über Rechner hinweg,
die Datei liegt auf V:. Sie enthält Rechner, Benutzer, PID und Startzeit
und wird über den ganzen Lauf gehalten, auch während CDH-Fenster offen
sind: Ein Heartbeat frischt sie jede Minute auf. Ein zweiter Rechner sieht
„Import läuft an PC-LAGER (m.mueller) seit 09:14". Erst nach 10 Minuten
ohne Heartbeat (Absturz) gilt die Sperre als verwaist und wird übernommen.

---

## 9. Konfiguration

Seit Welle 2 ist die Konfiguration auf **zwei Dateien** geteilt:

| Datei | Inhalt | Wer pflegt |
|---|---|---|
| `einstellungen.yaml` | alles außer Zugangsdaten (Shops + globale Optionen) | Innendienst |
| `zugang.yaml` | Consumer Key/Secret je Shop + Admin-Passwort-Hash | Admin |

`lieferadressen.yaml` bleibt eine eigene Datei (siehe 6b). Alle drei liegen
**neben** `woo_to_cdh.py` bzw. der EXE und gehören **nie** ins Repo (`.gitignore`).

**Migration aus der alten `config.yaml`:**

```powershell
python migrate_config.py --probelauf   # zeigt nur an, was entstünde (keine Schlüssel)
python migrate_config.py               # schreibt beide Dateien, sichert config.yaml nach Backup\
```

Der Probelauf gibt **keine Schlüsselwerte** aus, nur ob sie vorhanden sind.
Beim echten Lauf wandert die alte `config.yaml` nach `Backup\config_<Datum>.yaml`.

**Rückfall:** `woo_to_cdh.py`, `diagnose.py` und das Adressen-Tool lesen über
`load_config()`: bevorzugt `einstellungen.yaml` + `zugang.yaml`, und solange
eine davon fehlt, die alte `config.yaml`. Liegen alte und neue Dateien
gleichzeitig vor, gelten die neuen — und es steht eine Warnung im Log.

### Global (in `einstellungen.yaml`)

| Option                 | Bedeutung |
|------------------------|-----------|
| `cdh_import_folder`    | Zielordner für WEX-Dateien |
| `excel_export_folder`  | Zielordner für Kontroll-XLSX |
| `cdh_exe`              | Pfad zu `CDH_WEX.EXE` auf dem Arbeitsplatz |
| `veredelung_prefixes`  | Liste der Veredelungs-Präfixe |
| `status_after_export`  | Status nach Export, leer lassen für keine Änderung |
| `order_no_with_name`   | Empfängername in die Bestellnummer |
| `log_level`            | DEBUG, INFO, WARNING, ERROR |

### Pro Shop (in `einstellungen.yaml`)

Alle Felder stehen in `einstellungen.yaml` — **außer** `consumer_key` und
`consumer_secret`, die nach `zugang.yaml` wandern (dort je Shop-Name).

| Option                 | Bedeutung |
|------------------------|-----------|
| `name`                 | Anzeigename im Log |
| `enabled`              | Shop wird abgefragt |
| `url`                  | Shop-Basis-URL inkl. Slash |
| `consumer_key`         | API-Schlüssel — **liegt in `zugang.yaml`** |
| `consumer_secret`      | API-Geheimnis — **liegt in `zugang.yaml`** |
| `datev_no`             | Debitorennummer in CDH |
| `order_type`           | Belegart, bei uns durchgängig `AB` |
| `included_statuses`    | Statusfilter, Standard `[processing, on-hold]` |
| `import_on_days`       | Nur an diesen Tagen im Monat importieren |
| `combine_by_delivery`  | Sammel-Modus |
| `sender_address`       | Feste Hauptkundenadresse für den `<Sender>`-Block |
| `aggregate_all_positions` | Im Sammel-Modus alle gleichen Artikelvarianten addieren, ohne Trennzeilen |
| `extra_excel_meta`     | Zusätzliche Bestell-Meta-Felder als Excel-Spalten |
| `unknown_delivery`     | Sammel-Modus, Lieferort ohne feste Adresse: `cdh` (Standard, CDH-Standardadresse), `firma`, `versand`, `sperren` |
| `excel_summary`        | Summenblatt in der Excel (Standard aus) |
| `excel_summary_by`     | `ort` (Standard) oder `gesamt` |
| `excel_summary_veredelungen` | Veredelungen im Summenblatt (Standard an) |

Die globalen Optionen `status_after_export` und `order_no_with_name` lassen
sich pro Shop überschreiben.

### zugang.yaml

```yaml
admin:
  password:            # PBKDF2-HMAC-SHA256; salt/hash leer, bis gesetzt (GUI, Welle 5)
    algo: pbkdf2_sha256
    iterations: 200000
    salt: ''
    hash: ''
  users: []            # Windows-Benutzernamen mit Admin-Rechten
shops:
  CAF-Shop:
    consumer_key:    'ck_…'
    consumer_secret: 'cs_…'
```

### Aktuelle Shops

| Shop     | Debitor | Modus   | Statusfilter     | URL-Slug          |
|----------|---------|---------|------------------|-------------------|
| CAF      | 19541   | Einzeln | Standard         | `/caf-shop/`      |
| Stoll    | 19377   | Einzeln | Standard         | `/stoll-shop/`    |
| Xond     | 19705   | Einzeln | Standard         | `/xond-shop/`     |
| SFS      | 19616   | Einzeln | Standard         | `/sfs-shop/`      |
| Allgaier | 10698   | Sammel, Trennzeilen | nur `processing` | `/allgaier/`      |
| Ensinger | 14020   | Sammel, voll aggregiert | Standard     | `/ensinger-shop/` |
| KVSW     | 19742   | Einzeln | Standard         | `/kvsw-re-shop/`  |

---

## 10. Excel-Export

Zu jeder WEX entsteht eine gleichnamige XLSX im `excel-archiv\`. Zweck:
Kommissionierliste für die Produktion und Sicherung der Bestelldaten.

21 Spalten, identisch zum früheren Advanced-Order-Export-Plugin:

```
Datev-Nr · Bestellnummer · Bestellstatus · Auftragsdatum · Kundenhinweis ·
Firma (Fakturierung) · Adresse 1 & 2 (Abrechnung) · Postleitzahl (Abrechnung) ·
Stadt (Abrechnung) · E-Mail (Besteller) · Vorname (Empfänger) ·
Nachname (Empfänger) · Lieferort · Anzahl · Artikelnummer · Artikelname ·
Artikeltext 1 · Artikeltext 2 · VK Gesamt · VK Artikel · EK Artikel
```

Eine Zeile je Position, Kopfdaten wiederholt.

`Artikeltext 1` (Material) bleibt leer — die Bestell-API liefert das Feld
nicht mit. Dafür wäre ein zusätzlicher Produktabruf je Artikel nötig.

### Summenblatt

Mit `excel_summary: true` bekommt die Import-Excel ein zweites Blatt
„Summe": gleiche Artikelvarianten addiert, je Lieferort
(`excel_summary_by: ort`, Standard) oder über alles (`gesamt`).
Veredelungen sind dabei, außer `excel_summary_veredelungen: false`.

### Excel-Übersicht ohne Import

`write_excel_uebersicht(pfad, pruefergebnis.shops)` schreibt nach einem
Abruf eine Übersicht: ein Blatt je Shop mit allen abgerufenen Bestellungen,
dazu je Shop mit `excel_summary` ein Summenblatt. Die Bestellungen bleiben
offen — nichts in WooCommerce, nichts in `exported.log`.

### Zusätzliche Meta-Spalten

Über `extra_excel_meta` lassen sich pro Shop weitere Meta-Felder anhängen.
Sie landen **nur in der Excel**, nicht in der WEX. Die 21 Standardspalten
bleiben an ihrer Position.

```yaml
    extra_excel_meta:
      - key: teambestellung
        label: Teambestellung
      - personalnummer          # Kurzform: Key = Überschrift
```

Gesucht wird zuerst in `line_items[].meta_data` der Position, dann in
`order.meta_data`. Der Positionsfund gewinnt.

Das ist wichtig für **PPOM-Felder** (Produktoptionen): Die hängen an der
Position, nicht an der Bestellung. Eine Bestellung kann damit mehrere
Personen enthalten — jede Excel-Zeile trägt die Werte ihrer eigenen
Position.

Verglichen wird gegen `key` und `display_key`, jeweils mit und ohne
führenden Unterstrich und ohne Rücksicht auf Groß-/Kleinschreibung.
Bevorzugt wird `display_value`, also die lesbare Fassung („Ja" statt „1").

Bleibt eine Spalte über alle Positionen hinweg leer, schreibt das Skript
eine Warnung ins Log. Meist stimmt dann der Data Name nicht.

Bei Ensinger konfiguriert, aus der PPOM-Gruppe „MitarbeiterIn":

| Data Name         | Spalte                    |
|-------------------|---------------------------|
| `teambestellung`  | Teambestellung            |
| `mitarbeiterin`   | Vorname (MitarbeiterIn)   |
| `mitarbeiterin_2` | Nachname (MitarbeiterIn)  |
| `personalnummer`  | Personalnummer            |

Das PPOM-Feld `mitarbeiterin_place` ist eine reine Beschriftung und wird
nicht exportiert.

Fehlt `openpyxl`, entfällt nur die Excel-Datei. Der CDH-Import läuft normal
weiter, im Log steht eine Warnung.

---

## 11. EXE bauen und ausrollen

Nach jeder Änderung an `woo_to_cdh.py` oder `launcher.py`:

```powershell
cd C:\TEXMA\woo-cdh
python -m PyInstaller --onefile --name WOO_to_CDH `
    --hidden-import woo_to_cdh --hidden-import openpyxl launcher.py
Copy-Item -Force dist\WOO_to_CDH.exe "V:\Warenwirtschaftssystem\WooCommerce Import\"
Copy-Item -Force woo_to_cdh.py "V:\Warenwirtschaftssystem\WooCommerce Import\"
```

Ergebnis ist eine eigenständige EXE von rund 13 MB. Die Arbeitsplätze
brauchen weder Python noch Pakete.

**Nur Config geändert?** Dann reicht das Kopieren der `config.yaml`. Die EXE
liest sie bei jedem Start neu.

Voraussetzungen auf dem Entwicklungsrechner:

```powershell
pip install pyinstaller requests pyyaml openpyxl
```

PowerShell kennt `copy /Y` nicht — dort `Copy-Item -Force` verwenden.

---

## 12. Neuen Shop anbinden

1. Im Sub-Shop unter WooCommerce → Einstellungen → Erweitert → REST-API
   einen Schlüssel mit Rechten **Lesen/Schreiben** anlegen.
2. Debitorennummer in CDH nachsehen.
3. Block in `config.yaml` ergänzen, zunächst `enabled: false`.
4. Diagnose fahren:
   ```powershell
   python diagnose.py Neuer-Shop
   ```
   Das Skript liest ausschließlich und ändert weder Shop noch CDH.
5. **Altbestellungen prüfen.** Liegen im Shop schon offene Bestellungen, die
   bisher von Hand übertragen wurden, diese vorher im Shop per Sammelaktion
   auf „Abgeschlossen" setzen. Sonst kommen sie beim ersten Lauf alle mit.
6. Erst wenn Verbindung, Bestellfelder und EK/VK sauber sind:
   `enabled: true` und Config auf V: kopieren.
7. Ersten Lauf beobachten und den CDH-Auftrag gegenprüfen.

Neue Shops einzeln scharfschalten, nicht mehrere gleichzeitig. Sonst ist bei
einem Fehler unklar, welcher Shop ihn verursacht.

---

## 13. Diagnose

`diagnose.py` prüft in fünf Schritten und schreibt nichts:

1. Verbindung und Authentifizierung
2. Abruf offener Bestellungen
3. Bestellfelder gegen das erwartete Mapping
4. Positionen und Variantentexte
5. EK/VK je Artikel

```powershell
python diagnose.py                # alle Shops
python diagnose.py Allgaier-Shop  # nur einer
```

Ein ⚠ bei `shipping.company` ist bei Privatpersonen normal. Ein ⚠ bei EK/VK
bedeutet, dass im Shop Maße fehlen — dort wird der Preis in CDH leer bleiben.

Hinweis: Die Konsolen-Diagnose fragt immer `processing,on-hold` ab, unabhängig
vom `included_statuses` des Shops. Bei Allgaier können hier also Bestellungen
auftauchen, die der echte Lauf überspringt.

**Als Funktion** (für den Shop-Assistenten): `diagnose.diagnose(shop_cfg)`
liefert die fünf Punkte des Entwurfs als `Pruefpunkt(titel, stufe, text,
details)` mit `stufe` = `ok`/`warn`/`fehler`: Verbindung und Zugang ·
Bestellungen lesbar · Preise gepflegt · Varianten erkannt · Versandarten
(mit Abgleich gegen `lieferadressen.yaml`). Nutzt den Statusfilter des Shops,
schreibt nichts.

---

## 14. Fehlerbilder

| Meldung / Symptom | Ursache | Behebung |
|---|---|---|
| `config.yaml fehlt` | EXE und Config liegen in verschiedenen Ordnern | Beides in denselben Ordner legen |
| `404` bei Diagnose | Falscher URL-Slug | Shop-URL im Browser prüfen |
| `401` bei Diagnose | Schlüssel im falschen Sub-Shop erzeugt, Benutzer dort nicht angelegt, oder Caps-Snippet inaktiv | Abschnitt 3 durchgehen |
| „WEX Importer bereits ausgeführt" | Zwei CDH-Importe gleichzeitig | Darf nicht auftreten. Im Log prüfen, ob „warte auf 'Ende'" steht — sonst läuft eine alte Fassung mit `Popen` |
| Log: „CDH-Import war nach 15 Minuten noch geöffnet" | Ein CDH-Fenster wurde nicht geschlossen | Fenster schließen, genannte WEX aus dem `wex-archiv` nachholen |
| „Import läuft an … seit …" | Anderer Rechner importiert gerade (`running.lock`) | Warten. Ist der Rechner abgestürzt, wird die Sperre nach 10 Minuten ohne Heartbeat übernommen |
| Log: „Kundenadresse unvollständig … CDH nimmt die Standardadresse" | Rechnungsanschrift bzw. `sender_address` unvollständig | Kein Fehler. Soll eine feste Anschrift rausgehen: `sender_address` ergänzen |
| Log: „Lieferadresse ohne passende Versandart" | Schlüssel in `lieferadressen.yaml` passt zu keiner Versandart | Schreibweise wie im Shop übernehmen |
| EK-Felder in CDH leer | Maße im Shop nicht gepflegt | Diagnose zeigt betroffene Artikel |
| Bestellung fehlt in CDH | CDH-Fenster ohne „Ende" geschlossen | WEX aus `wex-archiv\` per Doppelklick nachholen |
| Keine Excel-Datei | `openpyxl` fehlt | `pip install openpyxl`, EXE neu bauen |
| Gelbe Zeilen „temporärer Artikel" | CDH legt unbekannte Artikelnummern an | Kein Fehler |

Das Log unter `logs\woo_to_cdh.log` enthält je Lauf Startzeit, Benutzer,
aktive Präfixe, verarbeitete Shops und alle Warnungen.

---

## 15. Sicherheit

API-Schlüssel stehen im Klartext in der `config.yaml` auf dem Netzlaufwerk.
Wer Zugriff auf V: hat, kann sie lesen.

- Schlüssel niemals in Chats, Tickets oder Konsolenausgaben teilen. Sind sie
  einmal sichtbar geworden, im Shop widerrufen und neu erzeugen.
- Beim Weitergeben der Config Platzhalter einsetzen.
- Rechte auf dem Ordner auf den Personenkreis beschränken, der den Import
  tatsächlich auslöst.

---

## 16. Offene Punkte

- **Statusfilter Ensinger** steht auf Standard (`processing` + `on-hold`),
  Allgaier nur auf `processing`. Bewusst so, gelegentlich prüfen ob noch passend.
- **Doppelte Artikelnummer im Allgaier-Shop:** `042/0248ALL-M` liegt sowohl
  auf einem Pullunder als auch auf einer Softshelljacke. Im Shop bereinigen.
- **Feldlänge `<OrderNo>` in CDH** ist unbekannt. Bei sehr langen Namen
  prüfen, ob CDH abschneidet.
- **Artikeltext 1** im Excel-Export bleibt leer, siehe Abschnitt 10.
- **`xlsx_to_wex.py`** wandelt Exporte des alten Plugins in WEX um. Reines
  Notfallwerkzeug für Nachträge, nicht Teil des laufenden Betriebs. Erwartet
  die 21-spaltige Variante mit `Datev-Nr` in Spalte 0.

---

## 17. Ausblick CDH-Ablösung

CDH soll mittelfristig durch TexOS ersetzt werden. Für die Migration relevant:

- Der Teil bis zur WEX-Erzeugung ist von CDH unabhängig. Nur
  `start_cdh_wex_import()` und das WEX-Format hängen am Altsystem.
- Ein Umbau auf ein anderes Zielsystem betrifft im Wesentlichen
  `write_cdh_wex()` und den Aufruf der Import-EXE.
- Abrufen, Preislogik, Veredelungs-Zusammenfassung, Gruppierung nach
  Lieferort, Duplikatschutz und Excel-Export bleiben unverändert nutzbar.
