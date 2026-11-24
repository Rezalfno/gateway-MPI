@echo off
cd /d "%~dp0"
py -3.11-32 -c "import struct; assert struct.calcsize('P')==4" || goto fail
py -3.11-32 -c "import paho.mqtt.client, pymodbus, serial" || goto fail
py -3.11-32 gateway.py
if errorlevel 1 goto fail
pause
exit /b 0
:fail
echo Gateway failed. Check Python x86, port 8765 and error above.
pause
exit /b 1
