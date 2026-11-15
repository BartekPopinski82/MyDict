@echo off
setlocal
chcp 65001 >nul
rem Test przechwytywania dzwieku: mikrofon i "Dzwiek komputera" (wynik tez w Test_dzwieku_wynik.txt)
cd /d "%~dp0.."
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
if not exist "%PY%" (
  echo Nie znaleziono srodowiska Pythona: %PY%
  pause
  exit /b 1
)
"%PY%" -c "import sounddevice, pyaudiowpatch" >nul 2>&1 || "%PY%" -m pip install sounddevice PyAudioWPatch
"%PY%" "%~dp0Test_dzwieku.py"
echo.
pause
