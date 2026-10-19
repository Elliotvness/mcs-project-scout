@echo off
title MCS Lead Scout - Local FastAPI Server (Port 8000)
cd /d "%~dp0"
echo Starting MCS Project Scout on http://localhost:8000 ...
start "" "http://localhost:8000"
python app.py
pause
