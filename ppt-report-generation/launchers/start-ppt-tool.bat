@echo off
chcp 65001 >nul
cd /d "%~dp0.."
python ".\tools\pdf_ppt_tool.py"
if errorlevel 1 (
  echo.
  echo Tool failed to start. Please install dependencies:
  echo python -m pip install -r ".\tools\requirements.txt"
  echo.
  pause
)
