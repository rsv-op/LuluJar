@echo off
REM LuluJar launcher for Windows.
REM
REM   run.bat                      start the browser interface
REM   run.bat extract file.rar     extract an archive
REM   run.bat doctor               show the available engines

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo LuluJar needs Python 3.8 or newer. Install it from https://python.org
  echo and make sure "Add Python to PATH" is selected.
  exit /b 1
)

set "PYTHONPATH=%CD%;%PYTHONPATH%"
python -m lulujar %*
endlocal
