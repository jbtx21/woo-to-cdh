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

- **Prüfregeln:** Kundenadresse fehlt → Shop gesperrt. Lieferort ohne feste
  Adresse → je Einstellung Kundenadresse, Versandadresse oder Sperre. EK fehlt →
  nur Hinweis.
- **Versandarten** aus den WooCommerce-Versandzonen
  (`/shipping/zones`, `/shipping/zones/{id}/methods`), Abgleich mit den
  Lieferadressen: fehlende Adressen, Adressen ohne passende Versandart.
- **Excel-Übersicht** ohne Import: ein Blatt je Shop, optional Summenblatt
  (je Lieferort oder gesamt, Veredelungen wahlweise).
- **Summenblatt** auch in der Import-Excel, wenn eingeschaltet.
- **Importsperre über Rechner:** `running.lock` mit Rechnername und Zeit, über den
  ganzen Importlauf gehalten. Andere Rechner sehen „Import läuft an …".
- **Diagnose als Funktion** (aus `diagnose.py`) für den Shop-Assistenten.

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

**STOPP — Sichtung am echten Rechner.**

## Welle 6 — Oberfläche, Import

- Tab „Import": Abrufen, Prüfansicht, Auswahl, Bestelldetail mit
  „So geht es an CDH", Excel-Übersicht.
- Import im Hintergrund-Thread, Fortschritt an die Oberfläche, Fenster bleibt
  bedienbar. Genau ein CDH-Fenster zur Zeit — `start_cdh_wex_import` unverändert
  verwenden.
- Liste der letzten WEX-Dateien mit „Erneut an CDH übergeben".
- Hinweis bei ungesicherten Einstellungen: Import arbeitet mit dem gesicherten Stand.

**STOPP — erster echter Import nur mit einer Testbestellung, im Beisein von
Jannik. Konsolen-EXE bleibt parallel einsatzbereit.**

## Welle 7 — Shop anlegen und Zugang erneuern

- Assistent in fünf Schritten wie im Entwurf. Shop wird ausgeschaltet angelegt.
- Zugang erneuern: neue Schlüssel erst prüfen, dann ersetzen.
- **Altbestellungen abschließen** ist ein Sammel-Statuswechsel im Shop und nicht
  umkehrbar: Anzahl und Bestellnummern vorher anzeigen, ausdrückliche
  Bestätigung.

**STOPP — vor dem ersten echten Abschließen von Altbestellungen.**

## Welle 8 — Ausrollen

- `build.ps1` erweitern: neue EXE, Tests als Pflicht vor dem Build.
- Parallelbetrieb mit der Konsolen-EXE, bis mehrere echte Läufe unauffällig waren.
- Mitarbeiter-Anleitung neu, im Stil der bisherigen.

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

Vor Welle 3 klären:

1. **E-Mail im Sender-Block.** Bei Sammelaufträgen steht dort die E-Mail der
   ersten Bestellung (sichtbar in `tests/golden/sammel_zusammengefasst.wex`).
   Gleiche Fehlerklasse wie der Personenname in `Name2`. Leer lassen, oder nur
   aus `sender_address`?
2. **Kundenstamm:** Überschreibt CDH beim WEX-Import den Kundenstammsatz aus dem
   Sender-Block, oder nur die Anschrift im Auftrag?
3. **Exit-Code** von `CDH_WEX.EXE` bei fehlgeschlagenem Import — steht seit dem
   Warte-Fix im Log. Ungleich 0 bei Fehlern, dann kann die Oberfläche
   Fehlschläge rot markieren.
4. **Arbeitsplätze:** Überall `C:\CDH\CDH_WEX.EXE`? Überall Windows 11
   (WebView2 für `pywebview`)?
5. **Admins:** Wer bekommt das Passwort?
6. **Personalnummer an der Ensinger-Kasse:** Es gibt ein Checkout-Feld
   Personalnummer an der Bestellung, zusätzlich zum PPOM-Feld am Produkt. Der
   interne Schlüssel ist unbekannt; bei Selbstbestellungen bleibt die
   Excel-Spalte deshalb leer. Schlüssel per API ermitteln, dann: Teambestellung
   „Ja" → PPOM-Feld, sonst → Checkout-Feld.
7. **Lenzing:** Soll es als eigene Versandart angelegt werden? Die feste
   Adresse steht bereit, greift aber erst mit exakt diesem Namen.
8. **Log-Text:** „5 bereits exportierte Bestellungen" erscheint bei jedem
   Shop, ist aber die Gesamtzahl. In Welle 3 je Shop zählen.
