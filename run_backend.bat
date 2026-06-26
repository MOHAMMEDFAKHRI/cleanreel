@echo off
title CleanReel - Backend API
REM Always run from the backend folder so "main:app" imports correctly.
cd /d "%~dp0backend"
echo(
echo ============================================================
echo    CleanReel - starting the backend API
echo ============================================================
echo(

set "PY="
py -3.12 -V >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3.11 -V >nul 2>nul && set "PY=py -3.11"
if not defined PY (
  echo [X] Python 3.12 not found. Install it from
  echo     https://www.python.org/downloads/release/python-31210/
  echo     (tick "Add python.exe to PATH"), then run this again.
  pause
  exit /b
)
echo Using %PY%
echo Installing/checking backend components...
%PY% -m pip install -r requirements.txt
if errorlevel 1 ( echo [!] Install failed - send me the messages above. & pause & exit /b )
echo(
echo API + test UI:  http://127.0.0.1:8000      (API docs: http://127.0.0.1:8000/docs)
echo (leave this window open; close it to stop the server)
start "" http://127.0.0.1:8000
%PY% -m uvicorn main:app --port 8000
pause
