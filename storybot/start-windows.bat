@echo off
rem storybot: Telegram bot on Windows without Docker (videos up to 20 MB).
cd /d "%~dp0"

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [!] ffmpeg not found. Run:  winget install Gyan.FFmpeg   then open a NEW window.
  pause
  exit /b 1
)
if not exist .env (
  copy .env.example .env >nul
  echo [!] Created .env - put TELEGRAM_BOT_TOKEN into it, then run this file again.
  notepad .env
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Creating Python environment...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo [!] Python not found. Run:  winget install Python.Python.3.12   then open a NEW window.
    pause
    exit /b 1
  )
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
echo Bot is running. Close this window to stop it.
.venv\Scripts\python.exe -m storybot telegram
pause
