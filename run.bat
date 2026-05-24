@echo off
REM AFP Viewer — Windows dev runner

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt --quiet

echo Starting AFP Viewer...
python main.py
