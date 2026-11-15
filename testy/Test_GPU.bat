@echo off
cd /d "%~dp0.."
echo Zamknij BAPO-dyktowanie przed testem (zeby nie dzielic GPU).
"%~dp0..\..\.venv\Scripts\python.exe" "%~dp0Test_GPU.py"
pause
