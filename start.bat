@echo off
cd /d "%~dp0"
where ffmpeg >nul 2>&1 || (
  echo Instaluje ffmpeg...
  winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  echo Gotowe. Zamknij to okno i uruchom start.bat jeszcze raz.
  pause & exit /b
)
if not exist .venv (
  py -3 -m venv .venv || (echo Brak Pythona. Wpisz: winget install Python.Python.3.12 & pause & exit /b)
)
.venv\Scripts\python -m pip install -q -U -r requirements.txt
.venv\Scripts\python clipper.py %*
pause
