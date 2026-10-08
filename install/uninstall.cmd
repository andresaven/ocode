@echo off
setlocal
python "%~dp0uninstall.py" %*
exit /b %ERRORLEVEL%
