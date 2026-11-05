$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "=== Taskbar Volume: сборка одного exe ==="

$python = "python"
if (Test-Path ".venv\Scripts\python.exe") {
    $python = ".venv\Scripts\python.exe"
}

& $python -c "import sys; print('Python', sys.version)"
if ($LASTEXITCODE -ne 0) { throw "Python не найден" }

& $python -m pip install --disable-pip-version-check -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Не удалось установить зависимости" }

& $python -m PyInstaller --noconfirm --clean TaskbarVolume.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой" }

Write-Host ""
Write-Host "Готово: $PWD\dist\TaskbarVolume.exe"
Write-Host "Проверка: dist\TaskbarVolume.exe --diagnose"
Write-Host "Отчёт: $env:TEMP\TaskbarVolume\diagnose.txt"
