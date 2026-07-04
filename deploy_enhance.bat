@echo off
rem Deploys the neural ENHANCE GPU service (Real-ESRGAN + GFPGAN) to Modal.
rem First build bakes ~530 MB of weights - expect 10-20 minutes. Subsequent
rem deploys are fast.
cd /d "%~dp0"
setlocal enabledelayedexpansion

rem -- find a REAL Python (one that has pip); the first 'python' on PATH may
rem    be a stripped-down tool venv without pip --
set "PYCMD="
for %%P in ("py -3" "py" "python" "python3") do (
    if not defined PYCMD (
        %%~P -m pip --version >nul 2>nul && set "PYCMD=%%~P"
    )
)
if not defined PYCMD (
    echo Could not find a Python installation with pip.
    echo Install Python from python.org, then run this again.
    pause & exit /b 1
)
echo Using: %PYCMD%

rem -- make sure the modal package exists, then deploy via python -m --
%PYCMD% -m modal --version >nul 2>nul || (
    echo Installing the modal package...
    %PYCMD% -m pip install --upgrade modal
)
%PYCMD% -m modal deploy gpu/modal_enhance_app.py

echo.
echo ============================================================
echo  Done. If it printed an https://...modal.run URL above,
echo  the deploy worked. If it asked you to authenticate, run:
echo      %PYCMD% -m modal setup
echo  then double-click this file again.
echo ============================================================
pause
