# TEXMA — WooCommerce zu CDH: Prüfen, bauen, ausrollen
# Aufruf im Repo-Ordner:  .\build.ps1            (nur bauen)
#                         .\build.ps1 -Deploy    (bauen und nach V: kopieren)
param([switch]$Deploy)
$ErrorActionPreference = "Stop"
$Target = "V:\Warenwirtschaftssystem\WooCommerce Import"

Write-Host "1/3 Tests" -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests rot — kein Build." }

Write-Host "2/3 Konfigurationen auf V: pruefen" -ForegroundColor Cyan
# config.yaml ist die alte Sammel-Datei; einstellungen.yaml + zugang.yaml sind
# die geteilten Nachfolger (Welle 2). Geprueft wird, was vorhanden ist.
foreach ($f in "config.yaml", "einstellungen.yaml", "zugang.yaml", "lieferadressen.yaml") {
    $p = Join-Path $Target $f
    if (Test-Path $p) {
        python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1], encoding='utf-8'))" $p
        if ($LASTEXITCODE -ne 0) { throw "$f auf V: ist kein gueltiges YAML." }
        Write-Host "   $f ok"
    }
}

Write-Host "3/3 Build" -ForegroundColor Cyan
python -m PyInstaller --onefile --name WOO_to_CDH --hidden-import woo_to_cdh --hidden-import openpyxl launcher.py
python -m PyInstaller --onefile --windowed --name Lieferadressen adressen_gui.py

if ($Deploy) {
    Copy-Item -Force dist\WOO_to_CDH.exe, dist\Lieferadressen.exe, woo_to_cdh.py, README.md $Target
    Write-Host "Ausgerollt nach $Target" -ForegroundColor Green
}
