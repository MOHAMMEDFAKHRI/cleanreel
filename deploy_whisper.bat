@echo off
rem Deploys the speech-to-captions (Whisper) GPU service to Modal.
rem First build bakes the ~460 MB faster-whisper "small" model: expect a few
rem minutes. Prints the endpoint URL -> set on Render as WR_WHISPER_URL.
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
%PYCMD% -m modal deploy gpu/modal_whisper_app.py

echo.
echo ============================================================
echo  Done. The https://...modal.run URL above is WR_WHISPER_URL.
echo ============================================================
pause
