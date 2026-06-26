@echo off
title CleanReel - Watermark Remover (web app)
cd /d "%~dp0"
echo(
echo ============================================================
echo    CleanReel - starting the web app
echo ============================================================
echo(

REM Use Python 3.12 consistently (it already has torch/opencv/numpy).
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
%PY% -V
echo(
echo Installing/updating components into this Python (first time takes a few min)...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo [!] Install failed - send me the messages above.
  pause
  exit /b
)
echo(
echo Launching... your browser will open at http://127.0.0.1:7860
echo (leave this window open; close it to stop the app)
start "" http://127.0.0.1:7860
%PY% app.py
pause
