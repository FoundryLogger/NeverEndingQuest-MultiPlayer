#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0
# License: See LICENSE file in the repository root
# This software is subject to the terms of the Fair Source License.

"""
NeverEndingQuest - Multiplayer Launcher
Copyright (c) 2024 MoonlightByte
Licensed under Fair Source License 1.0

Quick launcher for the NeverEndingQuest multiplayer server.
"""

import os
import sys
import subprocess
import webbrowser
import time
import threading

def check_dependencies():
    """Check if required dependencies are installed"""
    try:
        import flask
        import flask_socketio
        import openai
        print("✅ All dependencies are installed")
        return True
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Please run: pip install -r requirements.txt")
        return False

def check_config():
    """Check if config.py exists and is properly configured"""
    if not os.path.exists('config.py'):
        print("❌ config.py not found")
        print("Please copy config_template.py to config.py and add your OpenAI API key")
        return False
    
    try:
        from config import OPENAI_API_KEY
        if OPENAI_API_KEY == "your_openai_api_key_here":
            print("❌ Please add your OpenAI API key to config.py")
            return False
        print("✅ Configuration is valid")
        return True
    except ImportError:
        print("❌ Error importing config.py")
        return False

def check_game_files():
    """Check if essential game files exist"""
    required_files = [
        'party_tracker.json',
        'modules/conversation_history/conversation_history.json'
    ]
    
    missing_files = []
    for file_path in required_files:
        if not os.path.exists(file_path):
            missing_files.append(file_path)
    
    if missing_files:
        print(f"❌ Missing game files: {', '.join(missing_files)}")
        print("Please run the single-player game first to initialize these files")
        return False
    
    print("✅ Game files are present")
    return True

def open_browser():
    """Open browser after a short delay"""
    time.sleep(2)
    webbrowser.open('http://localhost:5000')

def main():
    """Main launcher function"""
    print("="*60)
    print("NeverEndingQuest - Multiplayer Launcher")
    print("="*60)
    
    # Check dependencies
    if not check_dependencies():
        input("\nPress Enter to exit...")
        return
    
    # Check configuration
    if not check_config():
        input("\nPress Enter to exit...")
        return
    
    # Check game files
    if not check_game_files():
        input("\nPress Enter to exit...")
        return
    
    print("\n🚀 Starting NeverEndingQuest Multiplayer Server...")
    print("📱 Server will be available at: http://localhost:5000")
    print("🌐 For network access, share your IP address with friends")
    print("="*60)
    
    # Start browser in background
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    try:
        # Import and run the server
        from server import start_server, socketio, app
        
        if start_server():
            print("✅ Server started successfully!")
            print("🎮 Players can now connect to the game")
            print("="*60)
            
            # Run the server
            socketio.run(
                app, 
                host='0.0.0.0', 
                port=5000, 
                debug=False, 
                allow_unsafe_werkzeug=True
            )
        else:
            print("❌ Failed to start server")
            input("\nPress Enter to exit...")
            
    except KeyboardInterrupt:
        print("\n🛑 Server stopped by user")
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")
        input("\nPress Enter to exit...")

if __name__ == '__main__':
    main() 