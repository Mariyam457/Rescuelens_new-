@echo off
REM Starts RescueLens without needing "streamlit" on PATH.
REM Double-click this file, or run it from the project folder.
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m streamlit run app.py
pause
