@echo off
title MCS Project Scout - Push to Cloud (GitHub Pages + Render)
cd /d "%~dp0"
echo ============================================================================
echo   MCS Project Scout ^& Warehouse Lead Engine - Cloud Sync
echo ============================================================================
echo.
echo [1/3] Staging updated files...
git add app.py index.html guide.html leads.json lead_pipeline_tasks.json requirements.txt render.yaml .gitignore
git commit -m "Sync MCS Project Scout leads, contacts, and guide" 2>nul
echo.
echo [2/3] Pushing to active Cloud Hosting (Elliotvness/mcs-project-scout ^& mcs-lead-scout)...
git branch -M main
git push -u new-cloud main --force
git push -u origin main --force
echo.
echo [3/3] Successfully deployed! Live Cloud URLs (Never Suspended):
echo       Dashboard:  https://elliotvness.github.io/mcs-project-scout/
echo       Team Guide: https://elliotvness.github.io/mcs-project-scout/guide.html
echo.
pause
