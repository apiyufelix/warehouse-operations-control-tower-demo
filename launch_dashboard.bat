@echo off
cd /d "%~dp0"
python -m streamlit run app.py
if errorlevel 1 echo Dashboard could not start. Check README.md for setup steps.
pause
