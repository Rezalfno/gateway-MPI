@echo off
cd /d "%~dp0"
py -3.11-32 -c "import struct; assert struct.calcsize('P')==4; print('Python x86 OK')" || goto fail
py -3.11-32 -m pip install -r requirements.txt || goto fail
py -3.11-32 -m unittest -v test_gateway test_remote || goto fail
echo Installation and local tests completed.
pause
exit /b 0
:fail
echo Installation or test failed. Read the error above.
pause
exit /b 1
