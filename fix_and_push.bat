@echo off
title Fix stale git locks and push CleanReel
cd /d "%~dp0"
echo(
echo Clearing stale git lock files...
if exist ".git\HEAD.lock" del /f /q ".git\HEAD.lock"
if exist ".git\index.lock" del /f /q ".git\index.lock"
if exist ".git\objects\maintenance.lock" del /f /q ".git\objects\maintenance.lock"
if exist ".git\refs\heads\main.lock" del /f /q ".git\refs\heads\main.lock"
echo Done.
echo(

git config user.email >nul 2>nul || git config user.email "MOHAMMEDFAKHRI@users.noreply.github.com"
git config user.name  >nul 2>nul || git config user.name  "MOHAMMEDFAKHRI"

git add .
git commit -m "Webhook: gate idempotency on actual credit add (avoid poisoning events)"
git push origin main

echo(
if errorlevel 1 (
  echo [!] Push did not finish - copy the messages above and send them to me.
) else (
  echo ============================================================
  echo   SUCCESS - pushed to GitHub.
  echo ============================================================
)
pause
