@echo off
rem Deploys the TEMPORAL inpainting GPU service to Modal.
rem First build clones the model repo + bakes ~700 MB of weights: expect
rem 10-20 minutes. Prints the endpoint URL -> set on Render as WR_INPAINT_SEQ_URL.
cd /d "%~dp0"

set "PYCMD="
for %%P in ("py -3" "py" "python" "python3") do (
    if not defined PYCMD (
        %%~P -m pip --version >nul 2>nul && set "PYCMD=%%~P"
    )
)
if not defined PYCMD (
    echo Could not find a Python installation with pip.
    pause & exit /b 1
)
echo Using: %PYCMD%

%PYCMD% -m modal --version >nul 2>nul || (
    echo Installing the modal package...
    %PYCMD% -m pip install --upgrade modal
)
%PYCMD% -m modal deploy gpu/modal_propainter_app.py

echo.
echo ============================================================
echo  Done. The https://...modal.run URL above is WR_INPAINT_SEQ_URL.
echo ============================================================
pause
