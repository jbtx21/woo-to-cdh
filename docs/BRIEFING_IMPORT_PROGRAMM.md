# BRIEFING · Importprogramm mit Oberfläche

Repo: `github.com/jbtx21/woo-to-cdh` · Stand: 23.09.2026 · Welle 0 und 1 erledigt

Ziel: Die Konsolen-EXE `WOO_to_CDH.exe` wird zu einem Programm mit
Oberfläche — Bestellungen abrufen, prüfen, gezielt importieren, Einstellungen
je Shop pflegen. Der Entwurf liegt unter `docs/Entwurf_Import-Einstellungen.html`
und ist die verbindliche Vorlage für Aufbau, Texte und Verhalten.

Fachlicher Hintergrund, WEX-Format und alle bisherigen Fallstricke: `README.md`.
**Vor jeder Welle lesen.**

---

## Regeln für jede Welle

1. **Recon zuerst.** Jede Welle beginnt mit einem reinen Lesedurchgang: betroffene
   Funktionen, Tests, Aufrufer. Ergebnis kurz zusammenfassen, dann erst ändern.
2. **Tests grün.** `python -m pytest -q` vor und nach jeder Änderung.
3. **Golden-WEX unverändert**, außer die Welle ändert das Format absichtlich.
   Dann `UPDATE_GOLDEN=1`, Diff in der Zusammenfassung zeigen, **STOPP**.
4. **Neue Logik bekommt Tests**, bevor sie in die Oberfläche kommt.
5. **Abschluss jeder Welle:** Commit, Push, `git status` sauber, lokaler Branch
   synchron mit origin. Feature-Branch je Welle (`welle-01-recon` …), Merge nach
   `main` erst nach Sichtung.
6. **Nie ins Repo:** `config.yaml`, `zugang.yaml`, `einstellungen.yaml`,
   `lieferadressen.yaml`, `exported.log`, WEX- und Excel-Dateien außer
   `tests/golden/`. Echte Namen oder Anschriften in Testdaten sind tabu.
7. **Nichts auf V: ändern** ohne ausdrückliche Freigabe. V: ist Produktion.
8. **Produktionsstopp bis nach dem 01.10.2026.** Am 1. Oktober läuft der erste
   echte Ensinger-Stichtag. Bis zur Freigabe danach wird auf V: nichts
   verändert, keine Migration, kein Deploy. Entwicklung nur auf Branches.

---

## Welle 1 — Recon Produktion ✅ erledigt am 23.09.2026

Ergebnis:

- **Code auf V:** mit `build.ps1 -Deploy` aus `main` ausgerollt, identisch mit
  Welle 0. Log zeigt den Warte-Fix.
- **`exported.log`:** 5 Einträge insgesamt. Nur ein Lauf mit zwei
  CDH-Übergaben (17.09., Ensinger 2766/2767) — Testbestellungen, kein echter
  Auftrag betroffen.
- **`config.yaml` auf V:** Beim Ensinger-Block fehlten `import_on_days` und
  `sender_address` — ergänzt. Gültiges YAML.
- **`lieferadressen.yaml`:** fehlte auf V: — abgelegt.
- **Abgleich Versandarten an der Ensinger-Kasse:** „Seewalchen (Österreich)"
  statt „Seewalchen" — Schlüssel korrigiert. „Lenzing" gibt es im Shop nicht
  als Versandart. Genau solche Abweichungen soll der automatische Abgleich in
  Welle 4 finden.
- **Allgaier** lief vom 14. bis 16.09. noch im Einzelmodus (Dateinamen
  `orders-…-3939.wex`), seit 23.09. im Sammel-Modus.

## Welle 2 — Konfiguration teilen

- `einstellungen.yaml`: alles außer Zugangsdaten, Struktur je Shop wie im Entwurf.
- `zugang.yaml`: Consumer Key/Secret je Shop, Admin-Passwort als Hash
  (`hashlib.pbkdf2_hmac`, SHA-256, Salt, ≥ 200 000 Iterationen).
- `lieferadressen.yaml` bleibt eigene Datei.
- Migrationsskript `config.yaml` → beide neuen Dateien, mit Probelauf, der nur
  anzeigt, was entstehen würde.
- `woo_to_cdh.py` liest die neuen Dateien, fällt aber auf `config.yaml` zurück,
  solange die neuen fehlen.

**STOPP — Probelauf-Ausgabe zeigen. Migration auf V: nur nach Freigabe und
mit Sicherung der alten `config.yaml` in `Backup\`.**

## Welle 3 — Abruf und Import trennen

Heute erledigt `process_shop` Abruf, Bau, Markierung und CDH-Übergabe in einem
Zug. Die Oberfläche braucht zwei Schritte:

- `abrufen(einstellungen) -> Pruefergebnis` — nur lesend. Liefert je Shop die
  Einheiten (Bestellung oder Lieferort-Gruppe), Warnungen und Sperren, die
  WEX-Daten zur Vorschau. Berücksichtigt Statusfilter, Importtage (mit Option
  „trotzdem"), Duplikatschutz.
- `importieren(auswahl, fortschritt_callback, abbruch_flag)` — je Einheit:
  WEX schreiben, `exported.log`, WooCommerce-Markierung, Statuswechsel,
  `start_cdh_wex_import`. Abbruch nur zwischen zwei Einheiten.
- Die Konsolen-EXE läuft danach über genau diese beiden Funktionen weiter.
  Verhalten und Golden-WEX unverändert.

**STOPP — Sichtung Code und Testlauf der Konsolen-EXE gegen einen Testshop.**

## Welle 4 — Neue Logik

Jeweils mit Tests:

- **Prüfregeln:** ~~Kundenadresse fehlt → Shop gesperrt~~ entfällt, der Sender
  trägt nur die DatevNo (siehe CDH-Test unten). Lieferort ohne feste Adresse →
  je Einstellung CDH-Standard, Rechnungsadresse, Versandadresse oder Sperre.
  EK fehlt → nur Hinweis.
- **Versandarten** aus den WooCommerce-Versandzonen
  (`/shipping/zones`, `/shipping/zones/{id}/methods`), Abgleich mit den
  Lieferadressen: fehlende Adressen, Adressen ohne passende Versandart.
- **Excel-Übersicht** ohne Import: ein Blatt je Shop, optional Summenblatt
  (je Lieferort oder gesamt, Veredelungen wahlweise).
- **Summenblatt** auch in der Import-Excel, wenn eingeschaltet.
- **Importsperre über Rechner:** `running.lock` mit Rechnername und Zeit, über den
  ganzen Importlauf gehalten. Andere Rechner sehen „Import läuft an …".
- **Diagnose als Funktion** (aus `diagnose.py`) für den Shop-Assistenten.

Umsetzung auf `welle-04-logik` (Stand 23.09.2026, wartet auf Sichtung):
neue Shop-Optionen `unknown_delivery` (`cdh`/`firma`/`versand`/`sperren`),
`excel_summary`,
`excel_summary_by`, `excel_summary_veredelungen`. Die Sperre hält sich per
Heartbeat, damit lange Läufe mit mehreren CDH-Fenstern nicht nach 10 Minuten
als verwaist gelten. **Nach dem 01.10. auf V: nachziehen:** `006/` in
`veredelung_prefixes` der echten Konfiguration (sie überschreibt die
Standardliste), für Ensinger `excel_summary` festlegen.

**CDH-Test 23.09.2026** (Jannik, Testkunde 99999, Aufträge 57420–57422):

| Test | WEX | Ergebnis in CDH |
|---|---|---|
| 1 | Sender-Anschrift leer, Lieferung gefüllt | Auftragskopf aus dem Kundenstamm, Lieferadresse wie im WEX |
| 2 | Sender gefüllt, Lieferung leer | Sender-Anschrift nur im Auftragskopf, Kundenstamm unverändert; keine Lieferadresse, CDH liefert an den Kopf |
| 3 | Sender abweichend, Lieferung gefüllt | Sender nur im Kopf, Kundenstamm unverändert, Lieferadresse wie im WEX |

Die DatevNo ist die Kundennummer in CDH. Gibt es sie nicht, legt CDH einen
temporären Kunden an — **die DatevNo muss in CDH existieren.**

**Entscheidung 23.09.2026 — Adressen (umgesetzt):** Sender-Anschrift immer
leer, einschließlich E-Mail; nur die DatevNo wird geschickt, CDH füllt den
Kopf aus dem Kundenstamm. `sender_address` und die Prüfregel „Kundenadresse
unvollständig" entfallen; ein noch gesetztes `sender_address` erzeugt einen
Hinweis im Log und wird ignoriert. Lieferanschrift unvollständig oder
Lieferort ohne feste Adresse → Lieferanschrift leer, CDH liefert an den Kopf
(`unknown_delivery` Standard `cdh`). Golden-WEX: alle Sender-Blöcke leer,
`sammel_cdh_standard.wex` neu. **Nach dem 01.10. auf V:** `sender_address`
aus dem Ensinger-Block entfernen (sonst nur Log-Hinweis).

**STOPP — Sichtung.**

## Welle 5 — Oberfläche, Einstellungen

- `pywebview`-Fenster, HTML aus dem Entwurf, Beispieldaten durch eine
  Python-Schnittstelle (`js_api`) ersetzt.
- Tab „Einstellungen" vollständig: Lesen, Ändern, Sichern mit Backup und
  Eintrag im Änderungsverlauf (Windows-Benutzer, Zeit, Änderung).
- Admin-Modus: Passwortprüfung gegen den Hash, 10 Minuten gültig, danach
  automatisch gesperrt. Geschützt: Shop hinzufügen, Zugang erneuern,
  Debitornummer, Veredelungs-Präfixe.
- Inter lokal einbetten, kein Nachladen aus dem Netz.
- **Feste Shop-IDs statt Namen** als Schlüssel zwischen `einstellungen.yaml`
  und `zugang.yaml`. Heute ordnet `zugang.yaml` die Zugangsdaten über den
  Shop-Namen zu — wird ein Shop in der Oberfläche umbenannt, verliert er
  seinen Zugang. Jeder Shop bekommt eine unveränderliche `id` (wie im
  Entwurf: `caf`, `ensinger` …), `zugang.yaml` wird darauf umgestellt
  (Migration mit Probelauf). Der Name bleibt Anzeige und Schlüssel für
  `lieferadressen.yaml`.

Umsetzung auf `welle-05-oberflaeche` (Stand 23.09.2026, wartet auf Sichtung):
`oberflaeche.py` + `ui/index.html` (aus dem Entwurf) + `einstellungen_api.py`.
Getestet mit Chromium gegen die echte Schnittstelle, **nicht** in pywebview/
WebView2 — das zeigt erst die Sichtung am echten Rechner. Abweichungen vom
Entwurf: Kundenadresse nur noch als Hinweis (CDH-Test), Lieferort-Regel mit
Option „Kundenadresse aus CDH" (Standard), Import-Tab Platzhalter (Welle 6),
Shop hinzufügen / Zugang erneuern nur hinter dem Admin-Modus (Welle 7).
Behoben: Klick auf „Sichern" direkt nach dem Tippen der Debitornummer ging im
Entwurf verloren. `status_after_export: ''` im Shop schaltet ein globales
`completed` jetzt wirklich ab (vorher griff trotzdem der globale Wert).
**Nach dem 01.10. auf V:** `python migrate_config.py --shop-ids --probelauf`,
dann umstellen; Admin-Passwort setzen; `pip install pywebview` am Arbeitsplatz
für den ersten Start (EXE-Build in Welle 8).

**STOPP — Sichtung am echten Rechner.**

## Welle 6 — Oberfläche, Import

- Tab „Import": Abrufen, Prüfansicht, Auswahl, Bestelldetail mit
  „So geht es an CDH", Excel-Übersicht.
- Import im Hintergrund-Thread, Fortschritt an die Oberfläche, Fenster bleibt
  bedienbar. Genau ein CDH-Fenster zur Zeit — `start_cdh_wex_import` unverändert
  verwenden.
- Liste der letzten WEX-Dateien mit „Erneut an CDH übergeben".
- Hinweis bei ungesicherten Einstellungen: Import arbeitet mit dem gesicherten Stand.

Umsetzung auf `welle-06-import` (Stand 23.09.2026, wartet auf Sichtung):
`import_api.py` (Schnittstelle, Hintergrund-Thread, `running.lock` für den
ganzen Lauf), Tab „Import" in `ui/index.html` als Startseite. In
`woo_to_cdh.py` nur Ergänzungen: `importieren()` nimmt ein `Importergebnis`
mit Protokoll je Einheit an, `start_cdh_wex_import` merkt sich den Exit-Code in
`CDH_LETZTER_EXIT` (Verhalten unverändert), `praefixe_uebernehmen()`
ausgelagert. Golden-WEX unverändert. Exit ≠ 0 zeigt die Oberfläche als „bitte
in CDH prüfen", bis Frage 3 geklärt ist. „Erneut an CDH" übergibt nur eine
Datei aus dem wex-archiv, ohne Shop-Status und ohne `exported.log`.
Getestet mit Chromium gegen die echte Schnittstelle (Fake-Shop, Fake-CDH),
**nicht** in pywebview/WebView2.

**STOPP — erster echter Import nur mit einer Testbestellung, im Beisein von
Jannik. Konsolen-EXE bleibt parallel einsatzbereit.**

## Welle 7 — Shop anlegen und Zugang erneuern

- Assistent in fünf Schritten wie im Entwurf. Shop wird ausgeschaltet angelegt.
- Zugang erneuern: neue Schlüssel erst prüfen, dann ersetzen.
- **Altbestellungen abschließen** ist ein Sammel-Statuswechsel im Shop und nicht
  umkehrbar: Anzahl und Bestellnummern vorher anzeigen, ausdrückliche
  Bestätigung.

Umsetzung auf `welle-07-shop-zugang` (Stand 23.09.2026, wartet auf Sichtung):
`shop_api.py` (`ShopApi` erweitert `EinstellungenApi`), Assistent und
„Zugang erneuern" in `ui/index.html` nach dem Entwurf. Sicherungen:
- Anlegen nur mit genau den zuletzt erfolgreich geprüften Schlüsseln
  (Fingerabdruck), nur ohne ungesicherte Änderungen und mit dem Token gegen
  gleichzeitiges Sichern an einem anderen Rechner.
- Zugang wird vor dem Shop geschrieben: Bricht es dazwischen ab, bleibt kein
  Shop ohne Zugang zurück.
- Altbestellungen: nur bei ausgeschalteten Shops, nur `bestaetigt=True`, nur die
  angezeigten und noch offenen Bestellungen; Nummern im Verlauf.
- Zugang erneuern: Hat ein Shop noch keine feste id (vor der Migration), bleibt
  der Zugang unter dem Namen, sonst fände der Import ihn nicht mehr. Das hat
  ein Test aufgedeckt.
Nebenbei behoben: Blätter spielten bei jedem Neuzeichnen die Einblend-Animation
erneut ab (Import-Fortschritt flackerte alle 0,7 s).
Getestet mit Chromium gegen die echte Schnittstelle (Fake-Shop), **nicht** in
pywebview/WebView2 und **nicht** gegen einen echten Shop.

**STOPP — vor dem ersten echten Abschließen von Altbestellungen.**

## Welle 8 — Ausrollen

- `build.ps1` erweitern: neue EXE, Tests als Pflicht vor dem Build.
- Parallelbetrieb mit der Konsolen-EXE, bis mehrere echte Läufe unauffällig waren.
- Mitarbeiter-Anleitung neu, im Stil der bisherigen.

Umsetzung auf `welle-08-ausrollen` (Stand 23.09.2026, wartet auf Sichtung):
- `build.ps1` neu: sauberes Repo, Tests mit Pflicht-UI-Tests
  (`WOO_CDH_UI_TESTS=pflicht`; die UI-Tests finden Chromium jetzt auch unter
  Windows), YAML-Prüfung auf V: nur lesend, drei EXE-Dateien (neu:
  `WOO_to_CDH_Oberflaeche.exe` mit eingebautem `ui/`), Selbsttest beider
  Import-EXE (`--selbsttest`).
- Deploy nur von `main`, erst nach dem Produktionsstopp, nach Eingabe von
  `JA`, mit Sicherung der bisherigen EXE in `Backup\exe_<Zeit>\`.
  `woo_to_cdh.py` wird nicht mehr nach V: kopiert (COMPUTER-1).
- Programmstand (Datum, Branch, Commit) in der EXE, im Log bei jedem Start
  und in der Oberfläche. Im Parallelbetrieb ist so erkennbar, welches
  Programm gelaufen ist.
- `docs/ANLEITUNG_MITARBEITER.md` neu. Die bisherige Anleitung liegt nicht
  im Repo, der Stil ist deshalb an README und Oberfläche angelehnt (du-Form
  wie in der Oberfläche).
- `docs/AUSROLLEN.md`: Checkliste in der Reihenfolge Vorbereitung →
  Arbeitsplätze → Ausrollen → Konfiguration umstellen → erster echter Import →
  Parallelbetrieb, dazu der Rückweg. Beim ersten Ausrollen braucht die alte
  EXE `config.yaml`, deshalb die Konfiguration erst nach dem Ausrollen
  umstellen.
- Build mit denselben PyInstaller-Optionen unter Linux nachgestellt: beide
  EXE gebaut, Selbsttest ok; eine Oberflächen-EXE ohne `ui/` fällt im
  Selbsttest durch. **Nicht** unter Windows gebaut, PowerShell lief hier
  nicht.
- Beim Pflichtlauf aufgefallen: Vier Import-Tests aus Welle 6 waren
  wackelig (ca. jeder dritte Lauf rot). Ursache lag im Test: Eine Zeile steht
  schon auf „fertig", während der Lauf noch abschließt; der Klick auf
  „Schließen" wird dann bewusst ignoriert. Die Tests warten jetzt auf „Fertig".
  Danach 8 Läufe in Folge grün.

**STOPP — Umstellung erst nach Freigabe.**

---

## Offene Fragen an Jannik

### Sicherheitsvorfall 23.09.2026 — API-Schlüssel sichtbar geworden

Beim Recon zu Welle 2 hat `python -m pytest -q` die echten Consumer
Key/Secret aller Shops in die Konsole geschrieben: Der Test
`tests/test_konfiguration.py::test_keine_echten_schluessel_im_repo`
durchsuchte per `rglob` den ganzen Ordner (inkl. der gitignorierten,
lokalen `config.yaml`) und pytests Assertion-Rewriting gab Treffer **und**
Dateiinhalt aus. **Zu tun:** betroffene Schlüssel im jeweiligen Shop
widerrufen und neu erzeugen (README §15). Behoben in Welle 2: Test scannt
nur noch getrackte Dateien und meldet im Trefferfall ausschließlich den
Pfad, nie den Fund (neuer Test sichert das ab).

### Sicherheitsvorfall 23.09.2026 (2) — Schlüssel im Log

`woo_to_cdh.log` enthielt bei jedem API-Fehler die komplette URL samt
`consumer_key`/`consumer_secret` (requests schreibt die URL in die
Fehlermeldung, wir authentifizieren per Query-Parameter). Aufgefallen an einem
Log von COMPUTER-1 (21.–23.09., CAF, Stoll, Xond, SFS, Allgaier). **Zu tun:**
diese Schlüssel im Shop widerrufen, falls noch nicht geschehen; alte Logs auf
V: bereinigen oder löschen. Behoben auf `hotfix-schluessel-im-log` (aus
`main`): Fehler werden ohne Schlüssel weitergereicht, zusätzlich filtert der
Log-Formatter. Nach dem 01.10. zusammen mit dem nächsten Deploy ausrollen.

Vor Welle 3 klären:

1. ✅ **E-Mail im Sender-Block.** Entschieden: Bei Sammelaufträgen leer,
   außer in `sender_address` gesetzt. Umgesetzt in Welle 3, Golden-WEX
   aktualisiert.
2. ✅ **Kundenstamm:** Beantwortet durch den CDH-Test vom 23.09.2026 (Welle 4):
   Der Sender landet nur im Auftragskopf, der Kundenstamm bleibt unverändert.
   Der Sender trägt seitdem nur noch die DatevNo.
3. **Exit-Code** von `CDH_WEX.EXE` bei fehlgeschlagenem Import — noch offen.
   Die Logs bis 23.09.2026 stammen alle von Fassungen **vor** dem Warte-Fix
   („CDH-WEX-Import gestartet: …" ohne „warte auf 'Ende'", kein
   „abgeschlossen (Exit n)"). Der erste Import mit der aktuellen Fassung
   schreibt den Code ins Log. Bis dahin zeigt die Oberfläche (Welle 6) einen
   Code ungleich 0 als „bitte in CDH prüfen", nicht als Fehler.
4. ✅ **Arbeitsplätze** (Antwort 23.09.2026): `CDH_WEX.EXE` soll auf V:
   (Serverlaufwerk) liegen, nicht mehr lokal unter `C:\CDH\`. Manche Rechner
   laufen mit Windows 10. Folgen:
   - `cdh_exe` in `einstellungen.yaml` auf den Pfad auf V: setzen — besser als
     UNC-Pfad (`\\SERVER2019TEX\Verwaltung\…`), weil V: nicht auf jedem
     Rechner gleich verbunden sein muss (COMPUTER-1 startet über UNC).
     **Genauer Pfad fehlt noch.** Die Sperre „nur ein CDH-Fenster" prüft per
     `tasklist` den Programmnamen und funktioniert unabhängig vom Ort.
   - Windows 10: Die Oberfläche braucht die WebView2-Laufzeit. `oberflaeche.py`
     prüft sie beim Start und meldet sonst klar, wo es sie gibt; die
     Konsolen-EXE läuft ohne. Vor Welle 6 an den Win-10-Rechnern prüfen bzw.
     installieren (Welle 8: Teil der Ausroll-Anleitung).

**Befund aus dem Log von COMPUTER-1 (23.09.2026):** Dort startet der
Benutzer „Assistent" eine **alte Fassung** aus
`…\WooCommerce Import\wex-archiv\woo_to_cdh.py` — mit eigener, veralteter
Konfiguration (alte Schlüssel → 401 bei CAF/Stoll/Xond/SFS, falscher Slug
`/allgaier-shop/` → 404) und noch mit `Popen` (Importe parallel). Seit
mindestens 21.09. endet dort jeder Lauf mit „0 exportiert, 5 Fehler";
Bestellungen, die nur von dort aus hätten kommen sollen, fehlen. **Zu tun
(Regel 7/8, nach Freigabe):** Verknüpfung auf COMPUTER-1 auf
`WOO_to_CDH.exe` im Hauptordner umstellen, die Kopie in `wex-archiv\`
(inkl. Konfiguration mit Schlüsseln) entfernen.
5. ✅ **Admins** (Antwort 23.09.2026): Ein normales Passwort reicht, es soll
   nur verhindern, dass versehentlich etwas geändert wird. `admin.users`
   bleibt leer, jeder mit dem Passwort darf. Kein Code nötig, das war schon
   so vorgesehen.
6. **Personalnummer an der Ensinger-Kasse:** Entscheidung 23.09.2026: den
   Schlüssel per API ermitteln. Umgesetzt:
   - `python diagnose.py Ensinger-Shop --felder` listet alle Felder der letzten
     50 Bestellungen, an der Bestellung und an den Positionen. Ausgegeben werden
     nur Name, Häufigkeit und Muster (`####`), keine Werte und keine
     Schlüssel. Mögliche Personalnummer-Felder sind markiert. Nur lesend; der
     Aufruf ist nach Regel 8 erlaubt, weil er auf V: nichts verändert.
   - Neue Angabe `checkout_key` in `extra_excel_meta`: Teambestellung „Ja" →
     PPOM-Feld an der Position, sonst → Checkout-Feld an der Bestellung; ist
     das leer, doch das PPOM-Feld. Die Oberfläche behält `checkout_key`
     beim Sichern (Fehler gefunden und behoben).
   **Offen:** Die Ausgabe von `--felder` am Entwicklungsrechner erzeugen. Aus
   der Cloud-Sitzung gibt es keinen Zugang zum Shop. Danach nach dem 01.10.
   `checkout_key` bei Ensinger eintragen.
7. ✅ **Lenzing** (Antwort 23.09.2026): gleiche Lieferadresse wie
   Seewalchen, aber eine getrennte Abteilung. Umsetzung ohne Code: eigene
   Versandart „Lenzing" im Ensinger-Shop, damit im Sammel-Modus ein eigener
   CDH-Auftrag entsteht; in `lieferadressen.yaml` dieselbe Anschrift wie
   Seewalchen, die Abteilung steht in „z. Hd.". So war es in den Testdaten
   schon angelegt. Versandart anlegen und Anschrift prüfen: nach dem 01.10.
8. ✅ **Log-Text:** „5 bereits exportierte Bestellungen" erscheint bei jedem
   Shop, ist aber die Gesamtzahl. Erledigt in Welle 3: je Shop gezählt, dazu
   am Laufende „N Bestellung(en) exportiert" je Shop.
