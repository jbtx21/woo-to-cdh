# Anleitung · Shop-Bestellungen nach CDH übernehmen

Stand: Oktober 2026 · Programm: **WOO_to_CDH_Oberflaeche.exe**

Das Programm holt die neuen Bestellungen aus den Kundenshops und legt sie als
Aufträge in CDH an. Du siehst vorher, was kommt, und entscheidest, was
übernommen wird.

Programm-Ordner: `V:\Warenwirtschaftssystem\WooCommerce Import\`

---

## 1. Starten

Doppelklick auf **WOO_to_CDH_Oberflaeche.exe**. Das Programm öffnet sich auf
dem Tab **Import**.

Kommt die Meldung „WebView2 fehlt": Das betrifft einzelne Rechner mit
Windows 10. Bitte bei der IT melden. Bis dahin funktioniert der Import wie
bisher über **WOO_to_CDH.exe** (Abschnitt 7).

## 2. Bestellungen abrufen

1. Auf **Abrufen** klicken. Das Programm fragt alle Shops ab und verändert
   dabei noch nichts.
2. Die Liste zeigt je Shop die Aufträge, die entstehen würden. Bei
   Sammel-Shops (z. B. Ensinger) ist das ein Auftrag je Lieferort.
3. Ein Klick auf einen Auftrag zeigt links die Bestellung und rechts
   **So geht es an CDH**: Kunde, Lieferadresse, Positionen.

Hinweise in der Liste:

| Anzeige | Bedeutung | Was tun |
|---|---|---|
| **Shop gesperrt** | Etwas fehlt, z. B. eine feste Lieferadresse. Diese Aufträge lassen sich nicht anhaken | Bescheid geben |
| Orange Text, z. B. **EK fehlt** | Nur ein Hinweis | Kann übernommen werden, in CDH den Preis prüfen |
| **Heute nicht dran** | Der Shop wird nur an bestimmten Tagen abgerechnet | Nur nach Absprache **Trotzdem anzeigen** |

## 3. Übernehmen

1. Die gewünschten Aufträge anhaken (oder oben **Alle**).
2. Auf **Importieren** klicken.
3. Für jeden Auftrag öffnet sich CDH. **Im CDH-Fenster auf „Ende" klicken.**
   Erst danach kommt der nächste Auftrag.
4. Oben steht, wie weit das Programm ist. Das Fenster bleibt bedienbar.

**Abbrechen** hält nach dem gerade laufenden Auftrag an. Die übrigen bleiben
offen und kommen beim nächsten Abrufen wieder.

Am Ende steht bei jedem Auftrag:

| Anzeige | Bedeutung | Was tun |
|---|---|---|
| **An CDH übergeben** (grüner Haken) | In CDH angelegt, im Shop erledigt | nichts |
| **Bitte in CDH prüfen** | CDH hat eine Rückmeldung gegeben | Auftrag in CDH ansehen |
| **Nicht an CDH übergeben** | CDH ließ sich nicht starten | **Erneut an CDH** (Abschnitt 4) |
| **Übersprungen** | Wurde schon einmal übernommen | nichts |
| **Nicht bearbeitet** | Nach **Abbrechen** liegen geblieben | Beim nächsten Abrufen wieder dabei |
| **Fehler** | Etwas ist schiefgegangen | Meldung notieren, Bescheid geben |

Die Kontrolllisten (Excel) liegen danach im Ordner `excel-archiv`.

## 4. Erneut an CDH übergeben

Wurde ein CDH-Fenster ohne „Ende" geschlossen oder stand ein Auftrag auf
„Nicht an CDH übergeben": Unten unter **Letzte WEX-Dateien** beim passenden Eintrag
auf **Erneut an CDH** klicken. Das gibt die Datei nur noch einmal an CDH, im
Shop ändert sich nichts.

Vorher in CDH nachsehen, ob der Auftrag nicht doch schon da ist, sonst steht
er zweimal drin.

## 5. Excel-Übersicht ohne Import

Nach dem Abrufen erstellt **Excel** oben rechts eine Übersicht aller offenen Bestellungen, für
alle Shops oder einen. Dabei wird nichts übernommen und im Shop nichts
verändert.

## 6. Artikel pflegen (nur mit Admin-Passwort)

Im Tab **Artikel** stehen alle Artikel des oben links gewählten Shops. Einen
Preis, eine Artikelnummer oder einen Namen direkt in der Tabelle ändern.
Geänderte Felder sind blau. **Enter** springt in dieselbe Spalte der nächsten
Zeile.

Viele auf einmal: Zeilen links anhaken (ein Hauptartikel hakt seine Varianten
mit an), dann **Sammelaktion**, zum Beispiel „VK, ± Prozent, 5".

Danach **Vorschau**. Sie zeigt alt → neu und prüft alles noch einmal gegen den
Shop. Rote Punkte zuerst in der Tabelle korrigieren, orange Punkte kurz
ansehen. Wer möchte, schaltet **Auch in anderen Shops** ein: Dann ändern sich
die Preise auch in den anderen Shops mit derselben Artikelnummer. Erst mit
**… sichern** geht es in den Shop.

Etwas falsch gesichert? Oben rechts **Zurücknehmen**. Das macht die letzte
Sammeländerung rückgängig.

**Texte:** In der Spalte **Texte** öffnen sich Kurzbeschreibung und
Beschreibung. Oben steht der Text mit seinen Formatierungen (HTML), darunter
eine Vorschau, wie er ungefähr im Shop aussieht. Ein Wort in vielen Artikeln
tauschen: unten **Suchen & Ersetzen**. Auch das geht erst mit der Vorschau in
den Shop.

**Bilder:** In der Spalte **Bild** öffnet sich das Bilderfenster.
- Mit ★ wird ein Bild zum Hauptbild, mit ◀ ▶ änderst du die Reihenfolge,
  mit ✕ entfernst du es.
- Darunter stehen alle Bilder, die der Shop schon hat. Ein Klick fügt das
  Bild hinzu.
- Oder die Adresse eines Bilds im Internet eintragen.
- Bei einer Variante wählst du ein Bild aus, meist eines vom Hauptartikel.
- Schneller bei vielen Varianten: Im Bilderfenster des Hauptartikels
  stehen unter **Bilder der Varianten** alle Farben. Ein Klick auf das
  passende Bild setzt es für alle Größen dieser Farbe.
- Neue Bilder vom eigenen Rechner lädst du weiter im Shop hoch. Danach
  stehen sie hier zur Auswahl.

**Pflicht-Zubehör:** In der Spalte **Zubehör** stellst du zum Beispiel den
Stick zum Poloshirt ein.
- Es gibt Platz A und B, dazu die Menge je Stück.
- Sind mehrere Artikel angehakt, überträgt **Auf ausgewählte Artikel
  übertragen** die Regel auf alle.
- **Staffelpreise** des Zubehörs (zum Beispiel Stick ab 1, 10, 25 … Stück)
  stehen in der Spalte **Staffel**. Die üblichen Mengen sind schon
  eingetragen, nur die Preise ausfüllen. Stufen ohne Preis zählen nicht.
- „eigene Regel" bei einer Variante heißt: Dort gilt eine eigene Einstellung
  aus dem Shop.

**VK und EK** gehen an CDH, der **Verkaufspreis** ist der Preis im Shop.

## 7. Wenn etwas nicht geht

- **„Import läuft an … seit …"**: An einem anderen Rechner läuft gerade ein
  Import. Abrufen und Ansehen geht, Importieren erst, wenn er fertig ist. Es darf immer nur ein Import gleichzeitig laufen.
- **Die neue Oberfläche startet nicht**: Wie bisher **WOO_to_CDH.exe** per
  Doppelklick starten. Beide Programme arbeiten mit denselben Daten, es kommt
  nichts doppelt.
- **Einstellungen**: Änderungen an Shops (Tage, Lieferadressen, Status) im
  Tab **Einstellungen** und dann **Sichern**. Mit einem Schloss markierte
  Felder brauchen das Admin-Passwort.
- Bei Fehlern bitte die Uhrzeit notieren. Das Log liegt unter
  `logs\woo_to_cdh.log`.

**Nie** Zugangsschlüssel (beginnen mit `ck_` oder `cs_`) in E-Mails, Chats
oder Tickets kopieren.
