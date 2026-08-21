# Instala dependencias, crea config y ejecuta un smoke test.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host "== Instalando dependencias ==" -ForegroundColor Cyan
# cmd /c evita que PowerShell trate warnings de pip (stderr) como errores fatales
cmd /c "python -m pip install --upgrade pip 2>&1"
cmd /c "python -m pip install -r requirements.txt 2>&1"

if (-not (Test-Path "$here\config.json")) {
    Copy-Item "$here\config.example.json" "$here\config.json"
    Write-Host ""
    Write-Host "== Se creo config.json ==" -ForegroundColor Yellow
    Write-Host "Editalo (notepad config.json), pega telegram_bot_token y telegram_chat_id, luego re-ejecuta setup.ps1." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "== Probando tiendas (un ciclo, sin enviar Telegram) ==" -ForegroundColor Cyan
python smoke_test.py

Write-Host ""
Write-Host "== Como dejarlo corriendo en background ==" -ForegroundColor Green
Write-Host "  1. Doble clic en run_hidden.vbs (arranca sin ventana)"
Write-Host "  2. Para arrancar al iniciar sesion: Win+R -> shell:startup -> pega un acceso directo de run_hidden.vbs"
Write-Host "  3. Logs en watcher.log"
