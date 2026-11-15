@echo off
setlocal
rem Uruchamia program tak jak zwykly launcher (pythonw), czeka i kopiuje dzienniki do folderu logi\ obok.
cd /d "%~dp0.."
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
set "PYW=%~dp0..\..\.venv\Scripts\pythonw.exe"
set "LOGD=%LOCALAPPDATA%\BAPO-dyktowanie"
if not exist "%~dp0logi" mkdir "%~dp0logi"
del /q "%~dp0logi\*" >nul 2>&1
echo Uruchamiam jak normalny launcher (pythonw)...
start "" "%PYW%" "%~dp0..\BAPO_Dyktowanie.py"
echo Czekam 12 s i sprawdzam, czy proces zyje...
timeout /t 12 /nobreak >nul
tasklist /fi "imagename eq pythonw.exe" > "%~dp0logi\procesy.txt" 2>&1
"%PY%" -c "import sys;print(sys.version)" > "%~dp0logi\wersja.txt" 2>&1
copy /y "%LOGD%\*.log" "%~dp0logi\" >nul 2>&1
copy /y "%LOGD%\*.txt" "%~dp0logi\" >nul 2>&1
echo.
echo Gotowe. Dzienniki skopiowane do: %~dp0logi
echo Napisz do Claude: "gotowe". Mozesz zamknac to okno.
pause
