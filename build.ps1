# TEXMA — WooCommerce zu CDH: Prüfen, bauen, ausrollen (Welle 8)
#
# Aufruf im Repo-Ordner:  .\build.ps1            (prüfen und bauen, nichts auf V:)
#                         .\build.ps1 -Deploy    (zusätzlich nach V: kopieren)
#
# Baut drei EXE-Dateien:
#   WOO_to_CDH.exe              Konsole, wie bisher (bleibt im Parallelbetrieb)
#   WOO_to_CDH_Oberflaeche.exe  neue Oberfläche (Import + Einstellungen), dazu
#                               der Ordner WOO_to_CDH_Oberflaeche_Dateien
#   Lieferadressen.exe          Adressen-Tool, wie bisher
#
# Bricht ab bei: roten Tests (UI-Tests sind Pflicht), ungesicherten Änderungen
# im Repo, kaputter YAML auf V:, fehlgeschlagenem Selbsttest der EXE.
# -Deploy nur von main und nur nach Bestätigung;
# die bisherigen EXE-Dateien wandern vorher nach V:\…\Backup\exe_<Zeit>\.
# Konfiguration, Zugangsdaten und Logs auf V: werden nie angefasst.
param([switch]$Deploy)
$ErrorActionPreference = "Stop"
$Target = "V:\Warenwirtschaftssystem\WooCommerce Import"
$Exes = "WOO_to_CDH.exe", "WOO_to_CDH_Oberflaeche.exe", "Lieferadressen.exe"
$GuiDaten = "WOO_to_CDH_Oberflaeche_Dateien"   # Ordner neben der Oberflaechen-EXE

Write-Host "1/5 Stand pruefen" -ForegroundColor Cyan
if (git status --porcelain) { throw "Ungesicherte Aenderungen im Repo - erst committen. Kein Build." }
$Commit = (git rev-parse --short HEAD).Trim()
$Branch = (git rev-parse --abbrev-ref HEAD).Trim()
$Stand  = "$(Get-Date -Format 'yyyy-MM-dd HH:mm') $Branch@$Commit"
Write-Host "   $Stand"
if ($Deploy) {
    if ($Branch -ne "main") { throw "Ausrollen nur von main (aktuell: $Branch)." }
}

Write-Host "2/5 Tests (UI-Tests sind Pflicht)" -ForegroundColor Cyan
$env:WOO_CDH_UI_TESTS = "pflicht"
try { python -m pytest -q } finally { Remove-Item Env:WOO_CDH_UI_TESTS }
if ($LASTEXITCODE -ne 0) { throw "Tests rot - kein Build." }

Write-Host "3/5 Konfigurationen auf V: pruefen (nur lesen)" -ForegroundColor Cyan
foreach ($f in "config.yaml", "einstellungen.yaml", "zugang.yaml", "lieferadressen.yaml") {
    $p = Join-Path $Target $f
    if (Test-Path $p) {
        python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" $p
        if ($LASTEXITCODE -ne 0) { throw "$f auf V: ist kein gueltiges YAML." }
        Write-Host "   $f ok"
    }
}

Write-Host "4/5 Build" -ForegroundColor Cyan
Set-Content -Encoding UTF8 build_info.py "# Von build.ps1 erzeugt, nicht einchecken.`nSTAND = `"$Stand`""
try {
    # numpy zieht openpyxl nur optional nach; ohne sind die EXE deutlich kleiner.
    $Gemeinsam = "--noconfirm", "--clean", "--hidden-import", "build_info",
                 "--exclude-module", "numpy"
    python -m PyInstaller @Gemeinsam --onefile --name WOO_to_CDH `
        --hidden-import woo_to_cdh --hidden-import openpyxl launcher.py
    if ($LASTEXITCODE -ne 0) { throw "Build WOO_to_CDH fehlgeschlagen." }
    # Oberflaeche als Ordner-EXE: Als Ein-Datei-EXE wurde sie bei jedem Start
    # von V: nach %TEMP% entpackt - gemessen 26,8 s (24.09.2026).
    python -m PyInstaller @Gemeinsam --onedir --windowed --name WOO_to_CDH_Oberflaeche `
        --contents-directory $GuiDaten --add-data "ui;ui" --collect-submodules webview `
        --hidden-import openpyxl oberflaeche.py
    if ($LASTEXITCODE -ne 0) { throw "Build Oberflaeche fehlgeschlagen." }
    python -m PyInstaller @Gemeinsam --onefile --windowed --name Lieferadressen adressen_gui.py
    if ($LASTEXITCODE -ne 0) { throw "Build Lieferadressen fehlgeschlagen." }
} finally { Remove-Item -ErrorAction SilentlyContinue build_info.py }

Write-Host "5/5 Selbsttest der EXE-Dateien" -ForegroundColor Cyan
& dist\WOO_to_CDH.exe --selbsttest
if ($LASTEXITCODE -ne 0) { throw "Selbsttest WOO_to_CDH.exe fehlgeschlagen." }
# Fenster-EXE: kein Konsolen-Ausgang, darum Exit-Code und selbsttest.txt
$GuiOrdner = "dist\WOO_to_CDH_Oberflaeche"
Remove-Item -ErrorAction SilentlyContinue "$GuiOrdner\selbsttest.txt"
$p = Start-Process "$GuiOrdner\WOO_to_CDH_Oberflaeche.exe" -ArgumentList "--selbsttest" -Wait -PassThru
$Text = Get-Content -Encoding UTF8 -ErrorAction SilentlyContinue "$GuiOrdner\selbsttest.txt"
Remove-Item -ErrorAction SilentlyContinue "$GuiOrdner\selbsttest.txt"
if ($p.ExitCode -ne 0) { throw "Selbsttest Oberflaeche fehlgeschlagen: $Text" }
Write-Host "   Oberflaeche: $Text"

if ($Deploy) {
    $Antwort = Read-Host "Nach $Target ausrollen ($Stand)? Zum Bestaetigen JA eingeben"
    if ($Antwort -cne "JA") { Write-Host "Nicht ausgerollt."; exit 0 }
    $Backup = Join-Path $Target ("Backup\exe_" + (Get-Date -Format "yyyy-MM-dd_HHmmss"))
    New-Item -ItemType Directory -Force $Backup | Out-Null
    # Zuerst den Dateien-Ordner der Oberflaeche beiseite schieben: Laeuft sie
    # noch irgendwo, scheitert das, bevor irgendetwas ersetzt ist.
    $Daten = Join-Path $Target $GuiDaten
    if (Test-Path $Daten) {
        try { Move-Item $Daten (Join-Path $Backup $GuiDaten) }
        catch { throw "Die Oberflaeche ist noch an einem Rechner geoeffnet - ueberall schliessen, dann erneut. Nichts ersetzt." }
    }
    foreach ($e in $Exes) {
        $alt = Join-Path $Target $e
        if (Test-Path $alt) { Copy-Item -Force $alt $Backup }
    }
    Write-Host "   Bisherige Fassung gesichert in $Backup"
    Copy-Item -Force (Join-Path dist WOO_to_CDH.exe), (Join-Path dist Lieferadressen.exe) $Target
    Copy-Item -Force "$GuiOrdner\WOO_to_CDH_Oberflaeche.exe" $Target
    Copy-Item -Recurse -Force "$GuiOrdner\$GuiDaten" $Target
    Copy-Item -Force README.md, docs\ANLEITUNG_MITARBEITER.md $Target
    Write-Host "Ausgerollt nach $Target ($Stand)" -ForegroundColor Green
} else {
    Write-Host "Gebaut in dist\ ($Stand). Nichts auf V: veraendert." -ForegroundColor Green
}
