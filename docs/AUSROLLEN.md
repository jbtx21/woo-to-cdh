# Ausrollen · Checkliste (Welle 8)

Gilt erst **nach dem Produktionsstopp (nach dem 01.10.2026)** und nur nach
ausdrücklicher Freigabe (Briefing, Regeln 7 und 8). Bis dahin wird auf V:
nichts verändert. `build.ps1 -Deploy` verweigert vorher den Dienst.

Reihenfolge einhalten: Jeder Schritt ist einzeln prüfbar und lässt sich
zurücknehmen.

## A. Vorbereitung (Entwicklungsrechner)

- [ ] `main` enthält alle gesichteten Wellen, `git status` sauber.
- [ ] `pip install -r requirements-dev.txt`, `pip install playwright`,
      `python -m playwright install chromium` (UI-Tests sind beim Build Pflicht).
- [ ] `.\build.ps1` (ohne `-Deploy`) läuft durch: Tests grün, drei EXE-Dateien in
      `dist\`, beide Selbsttests „ok".
- [ ] `dist\WOO_to_CDH_Oberflaeche.exe` einmal lokal gegen eine **Kopie** der
      Konfiguration starten: Fenster öffnet sich, unten links steht der
      Programmstand.

## B. Arbeitsplätze

- [ ] Jeder Import-Arbeitsplatz: `Test-Path C:\CDH\CDH_WEX.EXE` ergibt `True`.
      Sonst meldet der Import „CDH nicht gestartet".
- [ ] Windows-10-Rechner: WebView2-Laufzeit prüfen/installieren
      (https://go.microsoft.com/fwlink/p/?LinkId=2124703). Ohne sie meldet die
      Oberfläche das beim Start; die Konsolen-EXE läuft trotzdem.
- [ ] COMPUTER-1: Verknüpfung auf `WOO_to_CDH.exe` im Hauptordner umstellen,
      alte Kopie in `wex-archiv\` (mit eigener Konfiguration und Schlüsseln)
      entfernen.
- [ ] Nach Schritt D: Verknüpfung „WooCommerce → CDH" auf `WOO_to_CDH_Oberflaeche.exe` auf den
      Desktop der Beteiligten.

## C. Ausrollen

- [ ] `.\build.ps1 -Deploy` von `main`, Bestätigung mit `JA`. Die bisherigen
      EXE-Dateien landen vorher in `Backup\exe_<Zeit>\`.
- [ ] Im Log (`logs\woo_to_cdh.log`) steht bei jedem Start der Programmstand —
      so ist erkennbar, welches Programm gelaufen ist.

## D. Konfiguration umstellen (einmalig, mit Probelauf)

- [ ] Erst wenn C erledigt ist. Die neue Konsolen-EXE liest `config.yaml` noch
      genauso; die Oberfläche startet erst nach diesem Schritt (vorher meldet
      sie „einstellungen.yaml fehlt").
- [ ] Sicherung des ganzen Ordners `V:\Warenwirtschaftssystem\WooCommerce Import\`.
- [ ] Die Befehle laufen vom Entwicklungsrechner **im Ordner auf V:**
      (`cd "V:\Warenwirtschaftssystem\WooCommerce Import"`, dann
      `python C:\TEXMA\woo-to-cdh\migrate_config.py …`).
- [ ] Falls noch `config.yaml`: `python migrate_config.py --probelauf`, Ausgabe
      prüfen, dann ohne `--probelauf`.
- [ ] Feste Shop-ids: `python migrate_config.py --shop-ids --probelauf`, dann
      ohne `--probelauf`.
- [ ] Admin-Passwort setzen: `python migrate_config.py --admin-password …`.
      `admin.users` bleibt leer (Frage 5: ein Passwort reicht, es schützt nur
      vor versehentlichen Änderungen).
- [ ] `cdh_exe` bleibt `C:\CDH\CDH_WEX.EXE` (lokale CDH-Installation, Stand
      24.09.). Die Migration übernimmt den Wert unverändert.
- [ ] Ensinger: `checkout_key` für die Personalnummer eintragen (Frage 6,
      ermittelt mit `python diagnose.py Ensinger-Shop --felder`).
- [ ] Ensinger: Lenzing-Eintrag aus `lieferadressen.yaml` entfernen. Lenzing
      läuft über die Versandart „Seewalchen", gemeinsamer Auftrag (Frage 7).
- [ ] Aus der Welle-4-Notiz: `006/` in `veredelung_prefixes`, `excel_summary`
      für Ensinger, `sender_address` im Ensinger-Block entfernen.
- [ ] API-Schlüssel aller Shops erneuern (Sicherheitsvorfälle 23.09.) — geht
      jetzt über „Zugang erneuern" in der Oberfläche; alte Schlüssel im Shop
      widerrufen.
- [ ] Alte Logs mit Schlüsseln auf V: bereinigen.

## E. Erster echter Import (STOPP aus Welle 6)

- [ ] **Nur eine Testbestellung, im Beisein von Jannik.**
- [ ] Im Log den Exit-Code von `CDH_WEX.EXE` ablesen („abgeschlossen (Exit n)")
      und Frage 3 im Briefing beantworten.
- [ ] Auftrag in CDH gegen „So geht es an CDH" prüfen.

## F. Parallelbetrieb

Beide Programme bleiben nebeneinander einsatzbereit. Doppelte Aufträge sind
ausgeschlossen:

- **Eine Sperre für beide:** `running.lock` gilt für Konsole und Oberfläche,
  über alle Rechner. Läuft ein Import, startet kein zweiter.
- **Gleiche Buchführung:** Beide schreiben `exported.log` und markieren die
  Bestellung im Shop (`_cdh_exported_at`). Was eines übernommen hat, zeigt das
  andere nicht mehr bzw. als „Übersprungen".

Umstellen erst, wenn **mehrere echte Läufe** mit der Oberfläche unauffällig
waren (Vorschlag: zwei Wochen, alle Shops mindestens einmal, auch ein
Sammel-Shop am Stichtag). Dann die Mitarbeiter-Anleitung
(`docs/ANLEITUNG_MITARBEITER.md`) verteilen.

**STOPP — Umstellung erst nach Freigabe.**

## Zurück zur alten Fassung

`Backup\exe_<Zeit>\` enthält die vorherigen EXE-Dateien: zurückkopieren.
`exported.log` bleibt unverändert, es kommt also nichts doppelt.

**Achtung beim ersten Ausrollen:** Die heute laufende EXE stammt von vor
Welle 2 und liest nur `config.yaml`. Die Migration (Schritt D) verschiebt
`config.yaml` nach `Backup\config_<Datum>.yaml`. Wer auf diese alte EXE
zurück will, muss auch `config.yaml` zurückholen — mit den dann **alten**
Schlüsseln, die nach dem Erneuern nicht mehr gelten. Deshalb: Schritt D erst,
wenn die neuen EXE-Dateien laufen (Schritt C), und die Schlüssel erst erneuern, wenn
die neue Fassung läuft. Ab dem zweiten Ausrollen lesen alte und neue EXE
dieselben Dateien (`einstellungen.yaml`, `zugang.yaml`).
