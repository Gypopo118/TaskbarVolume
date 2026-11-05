@echo off
setlocal
cd /d "%~dp0"

echo === Taskbar Volume: сборка одного exe ===

set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

%PYTHON% -c "import sys; print('Python', sys.version)" || goto :error
%PYTHON% -m pip install --disable-pip-version-check -r requirements.txt pyinstaller || goto :error
%PYTHON% -m PyInstaller --noconfirm --clean TaskbarVolume.spec || goto :error

echo.
echo Готово: %CD%\dist\TaskbarVolume.exe
echo Проверка: dist\TaskbarVolume.exe --diagnose
echo Отчёт будет в %%TEMP%%\TaskbarVolume\diagnose.txt
echo.
pause
exit /b 0

:error
echo.
echo СБОРКА НЕ УДАЛАСЬ (код %ERRORLEVEL%)
pause
exit /b 1
