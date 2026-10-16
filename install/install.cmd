@echo off
setlocal
where python >nul 2>&1
if errorlevel 1 (
  echo [ocode] ERROR: python is not available in PATH.
  exit /b 1
)
python "%~dp0install.py" %*
set RC=%ERRORLEVEL%
echo.
echo Installer exit code: %RC%
exit /b %RC%
