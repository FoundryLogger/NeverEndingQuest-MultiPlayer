#!/bin/bash

echo "============================================================"
echo "NeverEndingQuest - Multiplayer Server Launcher"
echo "============================================================"
echo

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed"
    echo "Please install Python 3.8+ and try again"
    exit 1
fi

# Check if requirements are installed
echo "Checking dependencies..."
python3 -c "import flask, flask_socketio, openai" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Installing dependencies..."
    pip3 install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "ERROR: Failed to install dependencies"
        exit 1
    fi
fi

# Check if config.py exists
if [ ! -f "config.py" ]; then
    echo "ERROR: config.py not found"
    echo "Please copy config_template.py to config.py and add your OpenAI API key"
    exit 1
fi

# Check if game files exist
if [ ! -f "party_tracker.json" ]; then
    echo "ERROR: party_tracker.json not found"
    echo "Please run the single-player game first to initialize game files"
    exit 1
fi

echo "Starting NeverEndingQuest Multiplayer Server..."
echo "Server will be available at: http://localhost:5000"
echo "For network access, share your IP address with friends"
echo
echo "Press Ctrl+C to stop the server"
echo "============================================================"

# Start the server
python3 run_multiplayer.py 