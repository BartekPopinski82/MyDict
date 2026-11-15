@echo off
setlocal
cd /d "%~dp0.."
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
"%PY%" "%~dp0Test_Mikrofony.py"
pause
