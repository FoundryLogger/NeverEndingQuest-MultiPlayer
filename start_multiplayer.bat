@echo off
echo ============================================================
echo NeverEndingQuest - Multiplayer Server Launcher
echo ============================================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH
    echo Please install Python 3.8+ and try again
    pause
    exit /b 1
)

REM Check if requirements are installed
echo Checking dependencies...
python -c "import flask, flask_socketio, openai" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
)

REM Check if config.py exists
if not exist "config.py" (
    echo ERROR: config.py not found
    echo Please copy config_template.py to config.py and add your OpenAI API key
    pause
    exit /b 1
)

REM Check if game files exist
if not exist "party_tracker.json" (
    echo ERROR: party_tracker.json not found
    echo Please run the single-player game first to initialize game files
    pause
    exit /b 1
)

echo Starting NeverEndingQuest Multiplayer Server...
echo Server will be available at: http://localhost:5000
echo For network access, share your IP address with friends
echo.
echo Press Ctrl+C to stop the server
echo ============================================================

REM Start the server
python run_multiplayer.py

pause 