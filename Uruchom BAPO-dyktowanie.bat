@echo off
setlocal
rem BAPO-dyktowanie - uruchamia program w srodowisku .venv z folderu Whisperer
cd /d "%~dp0"
set "PY=%~dp0..\.venv\Scripts\python.exe"
set "PYW=%~dp0..\.venv\Scripts\pythonw.exe"
if exist "%PY%" goto have_python
echo Nie znaleziono srodowiska Pythona: %PY%
echo Folder BAPO-dyktowanie musi lezec w folderze Whisperer obok .venv.
pause
exit /b 1

:have_python
rem Biblioteki wymagane: dzwiek i tryb strumieniowy. Brak = proba doinstalowania, ale program i tak startuje.
"%PY%" -c "import sounddevice, pyaudiowpatch, websockets" >nul 2>&1
if not errorlevel 1 goto libs_ok
echo Instaluje brakujace biblioteki - tylko za pierwszym razem...
"%PY%" -m pip install sounddevice PyAudioWPatch "websockets>=14"
if not errorlevel 1 goto libs_ok
echo.
echo UWAGA: instalacja nie powiodla sie - uruchamiam program mimo to.
timeout /t 6 >nul

:libs_ok
rem Biblioteki opcjonalne (monitor GPU/CPU w dzienniku) - nigdy nie blokuja startu.
"%PY%" -c "import psutil, pynvml" >nul 2>&1
if errorlevel 1 start "" /min "%PY%" -m pip install --quiet psutil nvidia-ml-py
start "" "%PYW%" "%~dp0BAPO_Dyktowanie.py"
exit /b 0
