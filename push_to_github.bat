@echo off
title Push CleanReel to GitHub
cd /d "%~dp0"
echo(
echo ============================================================
echo   Pushing this folder to:
echo   https://github.com/MOHAMMEDFAKHRI/cleanreel
echo ============================================================
echo(

git --version >nul 2>nul
if errorlevel 1 (
  echo [X] Git is not installed / not on PATH.
  echo     Install "Git for Windows" from https://git-scm.com/download/win
  echo     then run this file again.
  pause
  exit /b
)

REM set an identity only if you don't already have one
git config user.email >nul 2>nul || git config user.email "MOHAMMEDFAKHRI@users.noreply.github.com"
git config user.name  >nul 2>nul || git config user.name  "MOHAMMEDFAKHRI"

if not exist ".git" git init
git add .
git commit -m "CleanReel: engine, API, web"
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin https://github.com/MOHAMMEDFAKHRI/cleanreel.git

echo(
echo If a GitHub sign-in window pops up, complete it (Authorize / Sign in).
echo(
git push -u origin main

echo(
if errorlevel 1 (
  echo [!] Push did not finish - copy the messages above and send them to me.
) else (
  echo ============================================================
  echo   SUCCESS - your code is now on GitHub.
  echo ============================================================
)
pause
