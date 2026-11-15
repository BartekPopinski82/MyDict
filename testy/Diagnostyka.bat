@echo off
setlocal
rem Uruchamia BAPO-dyktowanie z widoczna konsola - przydatne, gdy cos nie dziala
cd /d "%~dp0.."
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
"%PY%" -c "import sounddevice, pyaudiowpatch, websockets" >nul 2>&1 || "%PY%" -m pip install sounddevice PyAudioWPatch "websockets>=14"
"%PY%" -c "import psutil, pynvml" >nul 2>&1 || "%PY%" -m pip install psutil nvidia-ml-py
"%PY%" "%~dp0..\BAPO_Dyktowanie.py"
echo.
echo Dziennik: %LOCALAPPDATA%\BAPO-dyktowanie\dyktowanie.log
pause
