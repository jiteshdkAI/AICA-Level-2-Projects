@echo off
setlocal
title ScanniX
cd /d "%~dp0"

rem --- make sure the whole zip was extracted, not just this file ---
if not exist "%~dp0aml_screener.py" goto :notextracted
if not exist "%~dp0requirements.txt" goto :notextracted
if not exist "%~dp0scannix_core.py" goto :notextracted
if not exist "%~dp0scannix_text.json" goto :notextracted
if not exist "%~dp0scannix_assets.py" goto :notextracted
goto :filesok

:notextracted
echo The app files are missing next to this launcher.
echo.
echo This usually means the file was run from inside the zip.
echo Please extract the whole zip first:
echo   1. Right-click the downloaded zip file
echo   2. Choose "Extract All..." and pick a folder
echo   3. Open that folder and double-click run_aml_screener.bat there
echo.
pause
exit /b 1

:filesok
rem --- find Python ---
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo Python is not installed.
  echo Install it from https://www.python.org/downloads/
  echo Tick "Add Python to PATH" during setup, then run this file again.
  pause
  exit /b 1
)

rem --- first-run setup ---
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo First run: setting up, please wait a minute or two...
  %PY% -m venv "%~dp0.venv"
  if errorlevel 1 (
    echo Could not create the app environment.
    pause
    exit /b 1
  )
)
rem --- install / refresh components when this version is new ---
if not exist "%~dp0.venv\installed_v6.txt" (
  echo Installing required components, please wait...
  "%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
  "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Could not install required components. Check your internet connection and try again.
    pause
    exit /b 1
  )
  echo ok> "%~dp0.venv\installed_v6.txt"
)

rem --- check the windowing component ---
"%~dp0.venv\Scripts\python.exe" -c "import tkinter" 2>nul
if errorlevel 1 (
  echo The tkinter component is missing from your Python install.
  echo Reinstall Python from https://www.python.org/downloads/ and keep "tcl/tk and IDLE" ticked.
  pause
  exit /b 1
)

if /i "%~1"=="update" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0aml_screener.py" --update
  pause
  exit /b
)
if /i "%~1"=="server" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0aml_screener.py" --server
  pause
  exit /b
)

echo Starting ScanniX...
echo The first start downloads the official sanctions lists - progress is shown at the bottom of the app window.
echo Keep this window open while you use the app.
rem --- run the app with a visible console so any error stays on screen ---
"%~dp0.venv\Scripts\python.exe" "%~dp0aml_screener.py"
if errorlevel 1 (
  echo.
  echo The app stopped with an error. Please send a photo of the message above.
  pause
)
endlocal
