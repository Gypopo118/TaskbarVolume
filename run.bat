@echo off
rem Запуск из исходников без консольного окна (нужен Python 3.10+).
setlocal
cd /d "%~dp0"
set "PYTHONW=pythonw"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo pythonw.exe не найден, запускаю с консолью...
    python main.py
    exit /b %ERRORLEVEL%
)
start "" %PYTHONW% main.py
exit /b 0
