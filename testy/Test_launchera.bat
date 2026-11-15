@echo off
setlocal
cd /d "%~dp0.."
if not exist "%~dp0logi" mkdir "%~dp0logi"
set "OUT=%~dp0logi\launcher_wyjscie.txt"
echo === STARY launcher (.stary) - wyjscie i kod bledu > "%OUT%"
copy /y "%~dp0..\Uruchom BAPO-dyktowanie.bat.stary" "%~dp0_stary_test.bat" >nul
call "%~dp0_stary_test.bat" >> "%OUT%" 2>&1
echo KOD WYJSCIA: %errorlevel% >> "%OUT%"
del "%~dp0_stary_test.bat" >nul 2>&1
echo. >> "%OUT%"
echo === Kontrola bibliotek, osobno kazda >> "%OUT%"
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
for %%M in (sounddevice pyaudiowpatch websockets psutil pynvml) do (
  "%PY%" -c "import %%M; print('%%M OK')" >> "%OUT%" 2>&1
)
echo. >> "%OUT%"
echo === NOWY launcher - wyjscie i kod bledu >> "%OUT%"
call "%~dp0..\Uruchom BAPO-dyktowanie.bat" >> "%OUT%" 2>&1
echo KOD WYJSCIA: %errorlevel% >> "%OUT%"
timeout /t 10 /nobreak >nul
tasklist /fi "imagename eq pythonw.exe" >> "%OUT%" 2>&1
copy /y "%LOCALAPPDATA%\BAPO-dyktowanie\dyktowanie.log" "%~dp0logi\dyktowanie_po_tescie.log" >nul 2>&1
echo Gotowe. Napisz do Claude: gotowe
pause
