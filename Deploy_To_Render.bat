@echo off
title MCS Lead Scout - Push to GitHub and Render
cd /d "%~dp0"
echo ============================================================================
echo   MCS Project Scout ^& Warehouse Lead Engine - Render Deployment Sync
echo ============================================================================
echo.
echo [1/3] Staging updated leads.json, lead_pipeline_tasks.json, index.html, app.py...
git add app.py index.html leads.json lead_pipeline_tasks.json requirements.txt render.yaml .gitignore
git commit -m "Update MCS Lead Scout: 10 verified industrial leads, 5-dim scoring, contact/date editor, CoStar/cold-storage purge" 2>nul
echo.
echo [2/3] Pushing to GitHub (Elliotvness/mcs-lead-scout)...
echo       (If prompted, sign into GitHub in the browser pop-up window)
git branch -M main
git push -u origin main --force
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] Push to Elliotvness/mcs-lead-scout failed or repo does not exist yet.
    echo     If you need to create the repo first, open: https://github.com/new
    echo     Name it: mcs-lead-scout
    echo     Then re-run this script!
    pause
    exit /b 1
)
echo.
echo [3/3] Successfully pushed to GitHub! Render will auto-deploy within ~60 seconds:
echo       https://mcs-lead-scout.onrender.com
echo.
pause
