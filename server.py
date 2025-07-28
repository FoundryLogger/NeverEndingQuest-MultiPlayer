# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0
# License: See LICENSE file in the repository root
# This software is subject to the terms of the Fair Source License.

"""
NeverEndingQuest Multiplayer Server
Copyright (c) 2024 MoonlightByte
Licensed under Fair Source License 1.0
"""

# ============================================================================
# SERVER.PY - MULTIPLAYER GAME SERVER
# ============================================================================
#
# ARCHITECTURE ROLE: Multiplayer Server - Real-Time Game State Management
#
# This module transforms the single-player NeverEndingQuest into a multiplayer
# experience by implementing a Flask-SocketIO server that manages game state
# and coordinates between multiple players.
#
# KEY RESPONSIBILITIES:
# - Real-time game state synchronization across multiple clients
# - Player action processing and validation
# - AI response broadcasting to all connected players
# - Session management and player coordination
# - Integration with existing NeverEndingQuest game logic
# - WebSocket-based real-time communication
#
# MULTIPLAYER ARCHITECTURE:
# - Server maintains authoritative game state
# - Clients send actions via WebSocket events
# - AI responses broadcast to all connected players
# - Shared conversation history and party state
# - Real-time status updates and notifications
#
# INTEGRATION WITH EXISTING CODE:
# - Reuses core game logic from main.py
# - Leverages action_handler.py for action processing
# - Maintains compatibility with existing file structure
# - Preserves all game features (combat, leveling, etc.)
# ============================================================================

import json
import os
import sys
import threading
import time
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect
from openai import OpenAI

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import existing game modules
from utils.encoding_utils import sanitize_text, safe_json_load, safe_json_dump
from utils.file_operations import safe_write_json, safe_read_json
from utils.module_path_manager import ModulePathManager
from core.ai.action_handler import process_action
from core.managers import location_manager
from core.ai.conversation_utils import update_conversation_history, update_character_data
from updates.update_character_info import normalize_character_name
from utils.enhanced_logger import debug, info, warning, error, set_script_name
from main import save_conversation_history

# Import CombatService for multiplayer combat
from core.managers.combat_service import CombatService

# Import from main.py for conversation history management
from main import save_conversation_history

# Import inventory response system
from core.managers.inventory_manager import process_inventory_response

# Import save game manager for multiplayer
from core.managers.multiplayer_save_manager import get_multiplayer_save_manager

# Import module transition manager for timeline preservation
from core.managers.multiplayer_transition_manager import get_multiplayer_transition_manager

# Import validation systems
from core.validation.dm_response_validator import DMResponseValidator

# Import configuration
try:
    from config import (
        OPENAI_API_KEY,
        OPENAI_ORG_ID,
        DM_MAIN_MODEL,
        DM_SUMMARIZATION_MODEL,
        DM_VALIDATION_MODEL,
        DM_FULL_MODEL,
        DM_MINI_MODEL,
        ENABLE_INTELLIGENT_ROUTING,
        MAX_VALIDATION_RETRIES
    )
except ImportError:
    print("ERROR: config.py not found. Please copy config_template.py to config.py and add your OpenAI API key.")
    sys.exit(1)

# Set script name for logging
set_script_name("multiplayer_server")

# Initialize Flask and SocketIO
app = Flask(__name__, template_folder='web/templates', static_folder='web/static')
app.config['SECRET_KEY'] = 'neverendingquest-multiplayer-secret-key-2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Debug function for all socket events
def debug_socket_event(event_name, data=None):
    """Debug function to log all socket events"""
    print(f"🔍 DEBUG: Socket event '{event_name}' received with data: {data}")

# Initialize OpenAI client with organization support
client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_ORG_ID:
    client_kwargs["organization"] = OPENAI_ORG_ID
client = OpenAI(**client_kwargs)

# Global game state (shared across all players)
GAME_STATE = {
    # Combat state for multiplayer
    "active_combat": None,  # CombatService instance
    "is_in_combat": False,  # Boolean flag for combat state
    "combat_players": [],   # List of players in combat
    "party_tracker": None,
    "conversation_history": [],
    "location_data": None,
    "plot_data": None,
    "module_data": None,
    "connected_players": set(),
    "game_active": False,
    "current_turn_player": None,
    "turn_order": [],
    "last_action_time": None,
    "player_sids": {},  # Nuovo: mappa sid -> player_name
    "character_sheets": {},  # Nuovo: mappa player_name -> dati del personaggio
    "character_creation": {} # Nuovo: mappa player_name -> stato di creazione personaggio
}

# Dizionario per associare SID ai nomi dei giocatori
PLAYERS_SID_MAP = {}

# Level-up sessions dictionary (player_name -> LevelUpSession)
LEVEL_UP_SESSIONS = {}

# Initialize multiplayer save manager
save_manager = get_multiplayer_save_manager()

# Initialize multiplayer transition manager
transition_manager = get_multiplayer_transition_manager()
transition_manager.set_socketio(socketio)

# Game configuration
TEMPERATURE = 0.8
MAX_PLAYERS = 4
TURN_TIMEOUT = 300  # 5 minutes per turn

def load_validation_prompt():
    """Load the validation prompt for AI response validation"""
    try:
        with open("prompts/validation/validation_prompt.txt", "r", encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError:
        return "Validate the AI response for consistency and accuracy."

def load_system_prompt():
    """Load the main system prompt"""
    try:
        with open("prompts/system_prompt.txt", "r", encoding="utf-8") as file:
            return file.read().strip()
    except FileNotFoundError:
        return "You are a Dungeon Master for a 5th edition D&D game."

def initialize_game_state():
    """Initialize the game state by loading all necessary files"""
    global GAME_STATE
    
    try:
        # Load party tracker
        GAME_STATE["party_tracker"] = safe_json_load("party_tracker.json")
        if not GAME_STATE["party_tracker"]:
            error("FAILURE: Could not load party_tracker.json", category="initialization")
            return False
        
        # Load conversation history
        GAME_STATE["conversation_history"] = safe_json_load("modules/conversation_history/conversation_history.json") or []
        
        # Load location data
        if GAME_STATE["party_tracker"]:
            current_area_id = GAME_STATE["party_tracker"]["worldConditions"]["currentAreaId"]
            GAME_STATE["location_data"] = location_manager.get_location_info(
                GAME_STATE["party_tracker"]["worldConditions"]["currentLocation"],
                GAME_STATE["party_tracker"]["worldConditions"]["currentArea"],
                current_area_id
            )
        
        # Load plot and module data
        if GAME_STATE["party_tracker"]:
            module_name = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
            path_manager = ModulePathManager(module_name)
            
            GAME_STATE["plot_data"] = safe_json_load(path_manager.get_plot_path())
            GAME_STATE["module_data"] = safe_json_load(path_manager.get_module_file_path())
        
        info("SUCCESS: Game state initialized successfully", category="initialization")
        return True
        
    except Exception as e:
        error(f"FAILURE: Failed to initialize game state", exception=e, category="initialization")
        return False

def reload_game_state():
    """Reload game state after module creation or changes"""
    global GAME_STATE
    
    try:
        debug("RELOAD: Reloading game state after module creation", category="module_management")
        
        # Reload party tracker (might have new module info)
        GAME_STATE["party_tracker"] = safe_json_load("party_tracker.json")
        
        # Reload location data if party tracker is valid
        if GAME_STATE["party_tracker"]:
            current_area_id = GAME_STATE["party_tracker"]["worldConditions"]["currentAreaId"]
            GAME_STATE["location_data"] = location_manager.get_location_info(
                GAME_STATE["party_tracker"]["worldConditions"]["currentLocation"],
                GAME_STATE["party_tracker"]["worldConditions"]["currentArea"],
                current_area_id
            )
            
            # Reload plot and module data for current module
            module_name = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
            path_manager = ModulePathManager(module_name)
            
            GAME_STATE["plot_data"] = safe_json_load(path_manager.get_plot_path())
            GAME_STATE["module_data"] = safe_json_load(path_manager.get_module_file_path())
        
        # Broadcast updated game state to all players
        broadcast_full_game_state(message_type="system", message_content="Game state updated after module creation.")
        
        info("SUCCESS: Game state reloaded successfully", category="module_management")
        return True
        
    except Exception as e:
        error(f"FAILURE: Failed to reload game state", exception=e, category="module_management")
        return False

def ensure_main_system_prompt(conversation_history, main_system_prompt_text):
    """Ensure the main system prompt is first in the conversation history"""
    main_prompt_start = main_system_prompt_text[:50]
    
    filtered_history = []
    for msg in conversation_history:
        if msg["role"] == "system" and msg["content"].startswith(main_prompt_start):
            continue
        filtered_history.append(msg)
    
    return [{"role": "system", "content": main_system_prompt_text}] + filtered_history

def get_ai_response(conversation_history, validation_retry_count=0, action_text=None):
    """Get AI response with intelligent model routing like main.py"""
    try:
        # Import action predictor for intelligent routing
        from utils.action_predictor import predict_actions_required, extract_actual_actions, log_prediction_accuracy
        
        # Get the last user message for action prediction
        user_input = ""
        for msg in reversed(conversation_history):
            if msg.get("role") == "user":
                user_input = msg.get("content", "")
                break
        
        # Check if module creation prompt is present in user input OR if user is requesting module creation
        has_module_creation_prompt = ("You are a master storyteller, cartographer of myth" in user_input or 
                                     "I am ready to embark on a new adventure" in user_input or
                                     "create and explore a new module" in user_input or
                                     "let's create this specific adventure module" in user_input)
        
        # Predict if actions will be required (unless we're in a validation retry or module creation prompt)
        if validation_retry_count == 0 and not has_module_creation_prompt:
            prediction = predict_actions_required(user_input)
        elif has_module_creation_prompt:
            # Force full model when module creation prompt is present
            prediction = {"requires_actions": True, "reason": "Module creation prompt detected - using full model"}
        else:
            # On validation retry, force full model and skip prediction
            prediction = {"requires_actions": True, "reason": "Validation retry - using full model"}
        
        # Determine which model to use based on intelligent routing and validation retry
        if ENABLE_INTELLIGENT_ROUTING and validation_retry_count == 0 and not has_module_creation_prompt:
            # Use prediction to determine model (Phase 2 of token optimization)
            selected_model = DM_MINI_MODEL if not prediction["requires_actions"] else DM_FULL_MODEL
            
            # Log the routing decision
            routing_info = "MINI MODEL" if not prediction["requires_actions"] else "FULL MODEL"
            debug(f"MODEL ROUTING - Selected: {routing_info} (Prediction: {prediction['requires_actions']}, Reason: {prediction['reason']})", category="ai_communication")
        else:
            # Use full model (default behavior or validation retry)
            selected_model = DM_FULL_MODEL
            if validation_retry_count > 0:
                debug(f"MODEL ROUTING - VALIDATION RETRY {validation_retry_count}: Using FULL MODEL", category="ai_communication")
            else:
                debug(f"MODEL ROUTING - Intelligent routing disabled, using FULL MODEL", category="ai_communication")
        
        # Use lower temperature for inventory, level-up, and module creation actions
        temperature = TEMPERATURE
        if action_text and any(word in action_text.lower() for word in ['inventory', 'put', 'store', 'stow', 'add', 'take', 'level up', 'levelup', 'level-up', 'advance', 'create', 'module', 'adventure', 'new module', 'embark']):
            temperature = 0.2  # Lower temperature for more consistent JSON
            debug(f"Using lower temperature ({temperature}) for structured action", category="ai_communication")
        
        # Generate response with selected model
        response = client.chat.completions.create(
            model=selected_model,
            temperature=temperature,
            messages=conversation_history
        )
        content = response.choices[0].message.content.strip()
        
        # Extract actual actions from the response for accuracy tracking (only on initial attempt)
        if validation_retry_count == 0:
            actual_actions = extract_actual_actions(content)
            # Log prediction accuracy
            log_prediction_accuracy(user_input, prediction, actual_actions)
        
        return content
    except Exception as e:
        error(f"FAILURE: Failed to get AI response", exception=e, category="ai_communication")
        return None

def check_all_modules_plot_completion():
    """Check plot completion status for all available modules"""
    import os
    from utils.file_operations import safe_read_json
    
    modules_dir = "modules"
    all_modules_data = {
        "modules_checked": [],
        "all_complete": True,
        "completion_summary": {}
    }
    
    if not os.path.exists(modules_dir):
        debug("Modules directory not found", category="module_management")
        return all_modules_data
    
    # Find all available modules
    available_modules = []
    for item in os.listdir(modules_dir):
        if os.path.isdir(os.path.join(modules_dir, item)) and not item.startswith('.'):
            # Check if it has a _module.json file to confirm it's a valid module
            module_file = os.path.join(modules_dir, item, f"{item}_module.json")
            if os.path.exists(module_file):
                available_modules.append(item)
    
    # No modules found
    if not available_modules:
        return all_modules_data
    
    # Check each module
    for module_name in available_modules:
        try:
            # Load the module plot file
            plot_file = os.path.join(modules_dir, module_name, "module_plot.json")
            
            if os.path.exists(plot_file):
                plot_data = safe_read_json(plot_file)
                
                # Check for plot points
                if plot_data and "plotPoints" in plot_data and isinstance(plot_data["plotPoints"], list):
                    total_plots = len(plot_data["plotPoints"])
                    completed_plots = sum(1 for plot in plot_data["plotPoints"] if plot.get("completed", False))
                    
                    module_complete = completed_plots == total_plots and total_plots > 0
                    
                    all_modules_data["completion_summary"][module_name] = {
                        "total_plots": total_plots,
                        "completed_plots": completed_plots,
                        "is_complete": module_complete,
                        "plot_file_exists": True
                    }
                    
                    if not module_complete:
                        all_modules_data["all_complete"] = False
                        
                else:
                    debug(f"Module {module_name} has no plot data or plotPoints", category="module_management")
                    all_modules_data["completion_summary"][module_name] = {
                        "total_plots": 0,
                        "completed_plots": 0,
                        "is_complete": False,
                        "plot_file_exists": False
                    }
                    all_modules_data["all_complete"] = False
                    
            else:
                debug(f"Module {module_name} has no module_plot.json file", category="module_management")
                all_modules_data["completion_summary"][module_name] = {
                    "total_plots": 0,
                    "completed_plots": 0,
                    "is_complete": False,
                    "plot_file_exists": False
                }
                all_modules_data["all_complete"] = False
                
        except Exception as e:
            error(f"Error loading plot data for module {module_name}: {e}", category="module_management")
            all_modules_data["completion_summary"][module_name] = {
                "total_plots": 0,
                "completed_plots": 0,
                "is_complete": False,
                "plot_file_exists": False,
                "error": str(e)
            }
            all_modules_data["all_complete"] = False
    
    all_modules_data["modules_checked"] = available_modules
    
    return all_modules_data

def discover_available_modules():
    """Discover all available modules with metadata"""
    import os
    from datetime import datetime
    
    modules_dir = "modules"
    discovered_modules = []
    
    if not os.path.exists(modules_dir):
        return discovered_modules
    
    # Find all module directories
    for item in os.listdir(modules_dir):
        module_path = os.path.join(modules_dir, item)
        if not os.path.isdir(module_path) or item.startswith('.'):
            continue
            
        # Skip special directories
        if item in ['conversation_history', 'logs', 'backups', 'campaign_archives', 'campaign_summaries']:
            continue
            
        try:
            # Load module context for metadata
            context_file = os.path.join(module_path, "module_context.json")
            plot_file = os.path.join(module_path, "module_plot.json")
            
            if not os.path.exists(context_file):
                continue
                
            context_data = safe_json_load(context_file)
            if not context_data:
                debug(f"Failed to load context data for module {item}", category="module_discovery")
                continue
                
            # Validate required fields in context data
            if not context_data.get("module_name"):
                debug(f"Module {item} missing module_name in context", category="module_discovery")
                continue
                
            # Calculate completion percentage
            completion_percentage = 0
            if os.path.exists(plot_file):
                plot_data = safe_json_load(plot_file)
                if plot_data and "plotPoints" in plot_data:
                    total_points = len(plot_data["plotPoints"])
                    completed_points = sum(1 for pp in plot_data["plotPoints"] if pp.get("status") == "completed")
                    if total_points > 0:
                        completion_percentage = int((completed_points / total_points) * 100)
            
            # Get module metadata
            module_info = {
                "name": item,
                "display_name": context_data.get("module_name", item.replace("_", " ")),
                "description": get_module_description(plot_data) if 'plot_data' in locals() else "Adventure module",
                "type": determine_module_type(context_data),
                "level_range": "1-20",  # Default, could be enhanced
                "completion_percentage": completion_percentage,
                "created_date": get_module_creation_date(module_path),
                "last_played": get_module_last_played(module_path),
                "areas_count": len(context_data.get("areas", {})),
                "npcs_count": len(context_data.get("npcs", {}))
            }
            
            discovered_modules.append(module_info)
            
        except Exception as e:
            debug(f"Error processing module {item}: {str(e)}", category="module_discovery")
            continue
    
    # Sort by last played date (most recent first)
    discovered_modules.sort(key=lambda x: x.get("last_played", "1970-01-01"), reverse=True)
    
    return discovered_modules

def get_module_description(plot_data):
    """Extract description from module plot data"""
    if not plot_data:
        return "Adventure module"
        
    main_objective = plot_data.get("mainObjective", "")
    if main_objective:
        return main_objective[:100] + "..." if len(main_objective) > 100 else main_objective
        
    plot_title = plot_data.get("plotTitle", "")
    if plot_title:
        return f"Adventure: {plot_title}"
        
    return "Adventure module"

def determine_module_type(context_data):
    """Determine module type based on areas"""
    areas = context_data.get("areas", {})
    if not areas:
        return "mixed"
        
    area_types = [area.get("type", "mixed") for area in areas.values()]
    
    # If all areas are the same type, return that type
    unique_types = set(area_types)
    if len(unique_types) == 1:
        return list(unique_types)[0]
    
    # If there are dungeons, prioritize dungeon
    if "dungeon" in unique_types:
        return "dungeon"
    
    # If there are towns and wilderness, it's mixed
    if "town" in unique_types and "wilderness" in unique_types:
        return "mixed"
        
    # Default to mixed
    return "mixed"

def get_module_creation_date(module_path):
    """Get module creation date from file system"""
    try:
        context_file = os.path.join(module_path, "module_context.json")
        if os.path.exists(context_file):
            timestamp = os.path.getctime(context_file)
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except:
        pass
    return "Unknown"

def get_module_last_played(module_path):
    """Get module last played date"""
    try:
        # Check conversation history for this module
        conv_history_file = os.path.join("modules", "conversation_history", "conversation_history.json")
        if os.path.exists(conv_history_file):
            timestamp = os.path.getmtime(conv_history_file)
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        
        # Fallback to party tracker
        tracker_file = os.path.join(module_path, "party_tracker.json")
        if os.path.exists(tracker_file):
            timestamp = os.path.getmtime(tracker_file)
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except:
        pass
    return "Never"

def get_current_module_name():
    """Get the current active module name"""
    try:
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("module", "").replace(" ", "_")
        if not current_module:
            current_module = party_tracker.get("current_module", "Keep_of_Doom")
        return current_module
    except:
        return "Keep_of_Doom"

def calculate_module_completion(plot_data):
    """Calculate module completion percentage"""
    if not plot_data or "plotPoints" not in plot_data:
        return 0
        
    total_points = len(plot_data["plotPoints"])
    if total_points == 0:
        return 0
        
    completed_points = sum(1 for pp in plot_data["plotPoints"] if pp.get("status") == "completed")
    return int((completed_points / total_points) * 100)

def switch_to_module(new_module_name):
    """Switch the active module and update game state"""
    try:
        # Validate input
        if not new_module_name or not isinstance(new_module_name, str):
            error("Invalid module name provided", category="module_switching")
            return False
            
        # Sanitize module name (prevent path traversal)
        new_module_name = new_module_name.replace("..", "").replace("/", "_").replace("\\", "_")
        
        # Validate module exists
        module_path = os.path.join("modules", new_module_name)
        if not os.path.exists(module_path):
            error(f"Module {new_module_name} does not exist", category="module_switching")
            return False
            
        # Validate module has required files
        context_file = os.path.join(module_path, "module_context.json")
        if not os.path.exists(context_file):
            error(f"Module {new_module_name} is missing required context file", category="module_switching")
            return False
        
        # Update party tracker with new module
        if "party_tracker" not in GAME_STATE or GAME_STATE["party_tracker"] is None:
            GAME_STATE["party_tracker"] = {}
        
        GAME_STATE["party_tracker"]["module"] = new_module_name.replace("_", " ")
        GAME_STATE["party_tracker"]["current_module"] = new_module_name
        
        # Load new module data
        try:
            path_manager = ModulePathManager(new_module_name)
            
            # Load party tracker for new module (it's in the module directory)
            party_tracker_path = os.path.join(module_path, "party_tracker.json")
            if os.path.exists(party_tracker_path):
                party_tracker_data = safe_json_load(party_tracker_path)
                if party_tracker_data:
                    # Update current location and other module-specific data
                    GAME_STATE["party_tracker"].update(party_tracker_data)
            
            # Load module context
            context_path = os.path.join(module_path, "module_context.json")
            if os.path.exists(context_path):
                context_data = safe_json_load(context_path)
                if context_data:
                    GAME_STATE["module_data"] = context_data
            
            # Load current location data if available
            current_location = GAME_STATE["party_tracker"].get("current_location")
            if current_location:
                location_path = path_manager.get_area_file_path(current_location)
                if os.path.exists(location_path):
                    location_data = safe_json_load(location_path)
                    if location_data:
                        GAME_STATE["location_data"] = location_data
            
            info(f"Successfully switched to module: {new_module_name}", category="module_switching")
            return True
            
        except Exception as e:
            error(f"Failed to load data for module {new_module_name}: {str(e)}", category="module_switching")
            return False
        
    except Exception as e:
        error(f"Failed to switch to module {new_module_name}: {str(e)}", category="module_switching")
        return False

def validate_ai_response(primary_response, user_input, validation_prompt_text, conversation_history, party_tracker_data):
    """Validate AI response using secondary model"""
    try:
        # Get the last two messages from conversation history
        last_two_messages = conversation_history[-2:]
        while len(last_two_messages) < 2:
            last_two_messages.insert(0, {"role": "assistant", "content": "Previous context not available."})
        
        validation_conversation = [
            {"role": "system", "content": validation_prompt_text},
            {"role": "system", "content": f"User input: {user_input}"},
            last_two_messages[0],
            last_two_messages[1],
            {"role": "assistant", "content": primary_response}
        ]
        
        validation_result = client.chat.completions.create(
            model=DM_VALIDATION_MODEL,
            temperature=TEMPERATURE,
            messages=validation_conversation
        )
        
        validation_response = validation_result.choices[0].message.content.strip()
        
        try:
            validation_json = json.loads(validation_response)
            is_valid = validation_json.get("valid", False)
            return True if is_valid else validation_json.get("reason", "Validation failed")
        except json.JSONDecodeError:
            return True  # Assume valid if validation fails to parse
        
    except Exception as e:
        error(f"FAILURE: AI validation failed", exception=e, category="ai_validation")
        return True  # Assume valid on error

def process_ai_response(response, party_tracker_data, location_data, conversation_history):
    """Process AI response and execute actions"""
    try:
        # Extract JSON from response
        import re
        json_match = re.search(r'```json\n(.*?)```', response, re.DOTALL)
        if json_match:
            json_content = json_match.group(1)
        else:
            json_content = response
        
        parsed_response = json.loads(json_content)
        actions = parsed_response.get("actions", [])
        narration = parsed_response.get("narration", "")
        
        # Process actions
        for action in actions:
            result = process_action(action, party_tracker_data, location_data, conversation_history)
            if isinstance(result, dict):
                if result.get("status") == "exit":
                    return "exit"
                if result.get("status") == "restart":
                    return "restart"
        
        # Add response to conversation history
        conversation_history.append({"role": "assistant", "content": response})
        safe_write_json("modules/conversation_history/conversation_history.json", conversation_history)
        
        return {"narration": sanitize_text(narration), "actions": actions}
        
    except json.JSONDecodeError as e:
        error(f"FAILURE: Failed to parse AI response as JSON", exception=e, category="ai_processing")
        # Return raw response if JSON parsing fails
        conversation_history.append({"role": "assistant", "content": response})
        safe_write_json("modules/conversation_history/conversation_history.json", conversation_history)
        return {"narration": sanitize_text(response), "actions": []}
    except Exception as e:
        error(f"FAILURE: Error processing AI response", exception=e, category="ai_processing")
        return {"narration": "An error occurred while processing the response.", "actions": []}

def broadcast_full_game_state(message_type=None, message_content=None, message_player=None):
    """
    Costruisce l'intero stato del gioco e lo invia a tutti i client.
    Questo diventa l'UNICO modo per aggiornare l'interfaccia.
    """
    try:
        # Prepara la cronologia delle conversazioni per il client
        formatted_messages = []
        history = GAME_STATE["conversation_history"][-20:] # Invia solo gli ultimi 20 messaggi
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "assistant":
                try:
                    # Estrae la narrazione dal JSON se possibile
                    parsed = json.loads(content)
                    narration = parsed.get("narration", content)
                except:
                    narration = content
                formatted_messages.append({"type": "dm", "content": sanitize_text(narration)})
            elif role == "user":
                # Formatta correttamente l'azione del giocatore
                match = re.search(r'Player \(([^)]+)\): (.*)', content, re.DOTALL)
                if match:
                    player_name, action = match.groups()
                    formatted_messages.append({"type": "player_action", "player": player_name, "content": action})
                # Potremmo voler mostrare anche le DM Notes o i messaggi di sistema per il debug
                # else:
                #     formatted_messages.append({"type": "system", "content": sanitize_text(content)})

        # Aggiungi il nuovo messaggio istantaneo se presente (per la risposta immediata)
        if message_type and message_content:
             formatted_messages.append({"type": message_type, "player": message_player, "content": message_content})

        # Converti SID in nomi di giocatori
        connected_player_names = [PLAYERS_SID_MAP.get(sid, f'Player_{sid[:8]}') for sid in GAME_STATE["connected_players"]]
        
        state_data = {
            "messages": formatted_messages,
            "party_members": GAME_STATE["party_tracker"].get("partyMembers", []),
            "party_npcs": GAME_STATE["party_tracker"].get("partyNPCs", []),
            "current_location": GAME_STATE["party_tracker"]["worldConditions"].get("currentLocation", "Sconosciuto"),
            "current_area": GAME_STATE["party_tracker"]["worldConditions"].get("currentArea", "Sconosciuto"),
            "time": GAME_STATE["party_tracker"]["worldConditions"].get("time", ""),
            "connected_players": connected_player_names, # Usa i nomi dei giocatori, non i SID
            "current_turn_player": GAME_STATE["current_turn_player"],
            "game_active": GAME_STATE["game_active"],
            "character_sheets": GAME_STATE["character_sheets"]  # Nuovo: dati dei personaggi
        }
        
        socketio.emit('game_state_update', state_data)
        debug("SUCCESS: Stato di gioco completo inviato ai client", category="broadcasting")

    except Exception as e:
        error(f"FAILURE: Impossibile inviare l'aggiornamento di stato", exception=e, category="broadcasting")

def get_current_state_for_client():
    """Get current game state formatted for client consumption"""
    try:
        if not GAME_STATE["party_tracker"]:
            return {"error": "Game not initialized"}
        
        # Get recent conversation messages (last 10)
        recent_messages = GAME_STATE["conversation_history"][-10:] if GAME_STATE["conversation_history"] else []
        
        # Format messages for display
        formatted_messages = []
        for msg in recent_messages:
            if msg.get("role") == "assistant":
                try:
                    content = msg.get("content", "")
                    if content.startswith('{'):
                        parsed = json.loads(content)
                        narration = parsed.get("narration", content)
                    else:
                        narration = content
                    formatted_messages.append({
                        "type": "dm",
                        "content": sanitize_text(narration)
                    })
                except:
                    formatted_messages.append({
                        "type": "dm",
                        "content": sanitize_text(msg.get("content", ""))
                    })
            elif msg.get("role") == "user":
                content = msg.get("content", "")
                if "Player (" in content:
                    # Extract player name and action
                    import re
                    match = re.search(r'Player \(([^)]+)\): (.+)', content)
                    if match:
                        player_name = match.group(1)
                        action = match.group(2)
                        formatted_messages.append({
                            "type": "player_action",
                            "player": player_name,
                            "content": action
                        })
                    else:
                        formatted_messages.append({
                            "type": "system",
                            "content": sanitize_text(content)
                        })
                else:
                    formatted_messages.append({
                        "type": "system",
                        "content": sanitize_text(content)
                    })
        
        # Converti SID in nomi di giocatori
        connected_player_names = [PLAYERS_SID_MAP.get(sid, f'Player_{sid[:8]}') for sid in GAME_STATE["connected_players"]]
        
        # Get combat state if active
        combat_state = None
        if GAME_STATE["is_in_combat"] and GAME_STATE["active_combat"]:
            combat_state = GAME_STATE["active_combat"].get_current_combat_state()
        
        return {
            "messages": formatted_messages,
            "party_members": GAME_STATE["party_tracker"].get("partyMembers", []),
            "party_npcs": GAME_STATE["party_tracker"].get("partyNPCs", []),
            "current_location": GAME_STATE["party_tracker"]["worldConditions"].get("currentLocation", "Unknown"),
            "current_area": GAME_STATE["party_tracker"]["worldConditions"].get("currentArea", "Unknown"),
            "time": GAME_STATE["party_tracker"]["worldConditions"].get("time", ""),
            "day": GAME_STATE["party_tracker"]["worldConditions"].get("day", ""),
            "connected_players": connected_player_names,
            "current_turn_player": GAME_STATE["current_turn_player"],
            "game_active": GAME_STATE["game_active"],
            "character_sheets": GAME_STATE["character_sheets"],  # Nuovo: dati dei personaggi
            "combat_state": combat_state,  # Combat state information
            "is_in_combat": GAME_STATE["is_in_combat"]  # Combat mode flag
        }
        
    except Exception as e:
        error(f"FAILURE: Failed to get current state for client", exception=e, category="state_management")
        return {"error": "Failed to get game state"}



# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def index():
    """Serve the main game interface"""
    return render_template('multiplayer_interface.html')

@app.route('/api/game-state')
def get_game_state():
    """API endpoint to get current game state"""
    return jsonify(get_current_state_for_client())

@app.route('/api/modules', methods=['GET'])
def get_available_modules():
    """API endpoint to get all available modules with metadata"""
    try:
        discovered_modules = discover_available_modules()
        current_module = get_current_module_name()
        
        # Mark current module as active
        for module in discovered_modules:
            module["is_active"] = (module["name"] == current_module)
        
        response_data = {
            "modules": discovered_modules,
            "current_module": current_module,
            "total_count": len(discovered_modules)
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        error(f"Failed to get available modules: {str(e)}", category="api")
        return jsonify({"error": "Failed to retrieve modules"}), 500

@app.route('/api/switch-module', methods=['POST'])
def switch_module():
    """API endpoint to switch the active module"""
    try:
        # Validate request content type
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
            
        data = request.get_json()
        if not data or 'module_name' not in data:
            return jsonify({"error": "Module name is required"}), 400
        
        new_module_name = data['module_name']
        
        # Validate module name format
        if not isinstance(new_module_name, str) or len(new_module_name.strip()) == 0:
            return jsonify({"error": "Invalid module name format"}), 400
            
        new_module_name = new_module_name.strip()
        
        # Validate module exists
        available_modules = discover_available_modules()
        module_names = [m["name"] for m in available_modules]
        
        if new_module_name not in module_names:
            return jsonify({"error": "Module not found"}), 404
        
        # Check if we're already in this module
        current_module = get_current_module_name()
        if current_module == new_module_name:
            return jsonify({
                "success": True,
                "message": f"Already in module {new_module_name}",
                "module_name": new_module_name
            })
        
        # Safety check: prevent switching during combat or critical operations
        if GAME_STATE.get("in_combat", False):
            return jsonify({"error": "Cannot switch modules during combat"}), 409
            
        if len(GAME_STATE.get("connected_players", [])) > 1:
            # Could add additional checks for multiplayer scenarios
            pass
        
        # Switch to the new module
        success = switch_to_module(new_module_name)
        
        if success:
            # Broadcast module change to all clients
            socketio.emit('module_switched', {
                'module_name': new_module_name,
                'display_name': new_module_name.replace("_", " "),
                'previous_module': current_module
            })
            
            return jsonify({
                "success": True,
                "message": f"Switched to {new_module_name.replace('_', ' ')}",
                "module_name": new_module_name
            })
        else:
            return jsonify({"error": "Failed to switch module"}), 500
            
    except Exception as e:
        error(f"Failed to switch module: {str(e)}", category="api")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/module/<module_name>', methods=['GET'])
def get_module_details(module_name):
    """API endpoint to get detailed information about a specific module"""
    try:
        # Load module data
        module_path = os.path.join("modules", module_name)
        if not os.path.exists(module_path):
            return jsonify({"error": "Module not found"}), 404
        
        context_file = os.path.join(module_path, "module_context.json")
        plot_file = os.path.join(module_path, "module_plot.json")
        
        if not os.path.exists(context_file):
            return jsonify({"error": "Module context not found"}), 404
        
        context_data = safe_json_load(context_file)
        plot_data = safe_json_load(plot_file) if os.path.exists(plot_file) else {}
        
        # Get detailed module information
        module_details = {
            "name": module_name,
            "display_name": context_data.get("module_name", module_name.replace("_", " ")),
            "description": get_module_description(plot_data),
            "main_objective": plot_data.get("mainObjective", ""),
            "plot_title": plot_data.get("plotTitle", ""),
            "type": determine_module_type(context_data),
            "areas": context_data.get("areas", {}),
            "npcs": context_data.get("npcs", {}),
            "plot_points": plot_data.get("plotPoints", []),
            "areas_count": len(context_data.get("areas", {})),
            "npcs_count": len(context_data.get("npcs", {})),
            "plot_points_count": len(plot_data.get("plotPoints", [])),
            "completion_percentage": calculate_module_completion(plot_data),
            "validation_issues": context_data.get("validation_issues", [])
        }
        
        return jsonify(module_details)
        
    except Exception as e:
        error(f"Failed to get module details for {module_name}: {str(e)}", category="api")
        return jsonify({"error": "Failed to retrieve module details"}), 500

# ============================================================================
# SAVE/LOAD GAME API ENDPOINTS
# ============================================================================

@app.route('/api/save-game', methods=['POST'])
def create_save_game():
    """API endpoint to create a new save game"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        player_name = data.get('player_name', '')
        description = data.get('description', '')
        save_mode = data.get('save_mode', 'essential')
        
        if not player_name:
            return jsonify({"error": "Player name is required"}), 400
        
        # Update save manager with current players
        current_players = list(GAME_STATE.get("character_sheets", {}).keys())
        save_manager.set_active_players(current_players)
        
        # Set host if not already set (first player becomes host)
        if not save_manager.host_player and current_players:
            save_manager.set_host_player(current_players[0])
        
        # Create save game
        success, message = save_manager.create_save_game_thread_safe(player_name, description, save_mode)
        
        if success:
            # Notify all players via SocketIO
            socketio.emit('save_game_created', {
                'success': True,
                'message': message,
                'saved_by': player_name,
                'timestamp': datetime.now().isoformat()
            })
            
            return jsonify({
                "success": True,
                "message": message
            })
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400
            
    except Exception as e:
        error(f"Failed to create save game: {str(e)}", category="save_api")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/list-saves', methods=['GET'])
def list_save_games():
    """API endpoint to list all available save games"""
    try:
        player_name = request.args.get('player_name', '')
        
        # Get saves with permission info
        saves, can_load = save_manager.list_save_games_with_permissions(player_name)
        
        return jsonify({
            "saves": saves,
            "can_load": can_load,
            "host_player": save_manager.host_player,
            "total_count": len(saves)
        })
        
    except Exception as e:
        error(f"Failed to list save games: {str(e)}", category="save_api")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/load-save/<save_id>', methods=['POST'])
def load_save_game(save_id):
    """API endpoint to load a specific save game"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400
        
        data = request.get_json()
        player_name = data.get('player_name', '')
        
        if not player_name:
            return jsonify({"error": "Player name is required"}), 400
        
        # Load save game
        success, message = save_manager.restore_save_game_thread_safe(player_name, save_id)
        
        if success:
            # Reload game state after loading
            reload_game_state()
            
            # Notify all players via SocketIO
            socketio.emit('save_game_loaded', {
                'success': True,
                'message': message,
                'loaded_by': player_name,
                'save_id': save_id,
                'timestamp': datetime.now().isoformat()
            })
            
            return jsonify({
                "success": True,
                "message": message,
                "save_id": save_id
            })
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400
            
    except Exception as e:
        error(f"Failed to load save game: {str(e)}", category="save_api")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/delete-save/<save_id>', methods=['DELETE'])
def delete_save_game(save_id):
    """API endpoint to delete a specific save game"""
    try:
        player_name = request.args.get('player_name', '')
        
        if not player_name:
            return jsonify({"error": "Player name is required"}), 400
        
        # Check permissions (only host can delete)
        if not save_manager.can_player_save(player_name):
            return jsonify({"error": f"Only the host ({save_manager.host_player}) can delete saves"}), 403
        
        # Delete save game
        success, message = save_manager.delete_save_game(save_id)
        
        if success:
            # Notify all players via SocketIO
            socketio.emit('save_game_deleted', {
                'success': True,
                'message': message,
                'deleted_by': player_name,
                'save_id': save_id,
                'timestamp': datetime.now().isoformat()
            })
            
            return jsonify({
                "success": True,
                "message": message
            })
        else:
            return jsonify({
                "success": False,
                "error": message
            }), 400
            
    except Exception as e:
        error(f"Failed to delete save game: {str(e)}", category="save_api")
        return jsonify({"error": "Internal server error"}), 500

@app.route('/api/save-metadata/<save_id>', methods=['GET'])
def get_save_metadata(save_id):
    """API endpoint to get detailed metadata about a specific save"""
    try:
        save_info = save_manager.get_save_info(save_id)
        
        if save_info:
            return jsonify(save_info)
        else:
            return jsonify({"error": "Save not found"}), 404
            
    except Exception as e:
        error(f"Failed to get save metadata: {str(e)}", category="save_api")
        return jsonify({"error": "Internal server error"}), 500

# ============================================================================
# SOCKET.IO EVENT HANDLERS
# ============================================================================

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    sid = request.sid
    print(f"🔌 DEBUG: Client connected with SID: {sid}")
    emit('connected', {
        'message': 'Connected to NeverEndingQuest Multiplayer Server',
        'sid': sid
    })
    
    # Send current game state to the new player
    current_state = get_current_state_for_client()
    emit('game_state', current_state)
    
    debug(f"SUCCESS: Player connected with SID: {sid}", category="connection")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    sid = request.sid
    
    # Get player name from SID map
    player_name = PLAYERS_SID_MAP.get(sid, f'Player_{sid[:8]}')
    
    # Remove from connected players
    if sid in GAME_STATE["connected_players"]:
        GAME_STATE["connected_players"].remove(sid)
    
    # Remove from SID map
    if sid in PLAYERS_SID_MAP:
        del PLAYERS_SID_MAP[sid]
    
    # Update turn order if necessary
    if GAME_STATE["current_turn_player"] == player_name:
        if GAME_STATE["turn_order"]:
            current_index = GAME_STATE["turn_order"].index(player_name) if player_name in GAME_STATE["turn_order"] else -1
            if current_index >= 0:
                next_index = (current_index + 1) % len(GAME_STATE["turn_order"])
                GAME_STATE["current_turn_player"] = GAME_STATE["turn_order"][next_index]
    
    # Remove from turn order
    if player_name in GAME_STATE["turn_order"]:
        GAME_STATE["turn_order"].remove(player_name)
    
    broadcast_full_game_state() # Use the new broadcast function
    debug(f"SUCCESS: Player {player_name} disconnected with SID: {sid}", category="connection")

@socketio.on('join_game')
def handle_join_game(data):
    """Handle player joining the game"""
    sid = request.sid
    player_name = data.get('player_name', f'Player_{sid[:8]}')
    
    # Check if maximum players reached
    if len(GAME_STATE["connected_players"]) >= MAX_PLAYERS:
        emit('error', {'message': f'Game is full. Maximum {MAX_PLAYERS} players allowed.'})
        return
    
    # Add player to connected players
    GAME_STATE["connected_players"].add(sid)
    
    # Associa SID al nome del giocatore
    PLAYERS_SID_MAP[sid] = player_name
    GAME_STATE["player_sids"][sid] = player_name
    
    # Add to turn order if not already present
    if player_name not in GAME_STATE["turn_order"]:
        GAME_STATE["turn_order"].append(player_name)
    
    # Set as current turn player if no one is playing
    if not GAME_STATE["current_turn_player"]:
        GAME_STATE["current_turn_player"] = player_name
    
    # Activate game if not already active
    if not GAME_STATE["game_active"]:
        GAME_STATE["game_active"] = True
    
    # CARICA I DATI DEL PERSONAGGIO!
    try:
        if GAME_STATE["party_tracker"]:
            module_name = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
            path_manager = ModulePathManager(module_name)
            char_file = path_manager.get_character_path(normalize_character_name(player_name))
            
            # DEBUG: Aggiungiamo informazioni dettagliate
            debug(f"DEBUG: Cercando personaggio '{player_name}' in file: {char_file}", category="character_loading")
            debug(f"DEBUG: Working directory: {os.getcwd()}", category="character_loading")
            debug(f"DEBUG: File esiste: {os.path.exists(char_file)}", category="character_loading")
            debug(f"DEBUG: Percorso assoluto: {os.path.abspath(char_file)}", category="character_loading")
            
            char_data = safe_json_load(char_file)
            debug(f"DEBUG: Risultato safe_json_load: {char_data is not None}", category="character_loading")
            
            if char_data:
                GAME_STATE["character_sheets"][player_name] = char_data
                info(f"SUCCESS: Dati del personaggio per '{player_name}' caricati.", category="character_loading")
                # Personaggio esistente trovato
                emit('player_joined', {
                    'player_name': player_name,
                    'message': f'{player_name} has joined the game!',
                    'character_exists': True
                })
            else:
                warning(f"ATTENZIONE: File del personaggio per '{player_name}' non trovato.", category="character_loading")
                # Personaggio non trovato - avvia processo di creazione
                emit('character_creation_required', {
                    'player_name': player_name,
                    'message': f'Welcome {player_name}! Let\'s create your D&D character.',
                    'character_exists': False
                })
                # Inizializza lo stato di creazione personaggio
                GAME_STATE["character_creation"][player_name] = {
                    "step": "welcome",
                    "data": {}
                }
                return  # Non continuare con il broadcast finché il personaggio non è creato
        else:
            warning(f"ATTENZIONE: party_tracker non disponibile per caricare il personaggio di '{player_name}'", category="character_loading")
            emit('error', {'message': 'Game configuration error. Please contact the DM.'})
            return
    except Exception as e:
        error(f"ERRORE durante il caricamento del personaggio per '{player_name}': {e}", category="character_loading")
        emit('error', {'message': 'Error loading character data. Please try again.'})
        return
    
    # Solo se il personaggio esiste, procedi con il broadcast
    broadcast_full_game_state()
    debug(f"SUCCESS: Player {player_name} joined the game", category="game_management")

@socketio.on('player_action')
def on_player_action_event(data):
    """Questa funzione riceve l'evento dal client e lo passa al gestore della logica."""
    debug_socket_event('player_action', data)
    sid = request.sid
    player_name = data.get('player_name', f'Player_{sid[:8]}')
    action_text = data.get('text', '')
    
    if not action_text.strip():
        emit('error', {'message': 'Please provide an action to perform.'})
        return
    
    # Check if game is active
    if not GAME_STATE["game_active"]:
        emit('error', {'message': 'Game is not active. Please wait for the game to start.'})
        return
    
    # Check if player is connected
    if sid not in GAME_STATE["connected_players"]:
        emit('error', {'message': 'You are not connected to the game.'})
        return
    
    # Esegui la logica di gioco in un thread separato per non bloccare il server
    socketio.start_background_task(target=handle_player_action_logic, player_name=player_name, action_text=action_text, sid=sid)

@socketio.on('character_creation_step')
def handle_character_creation_step(data):
    """Handle character creation steps"""
    sid = request.sid
    player_name = PLAYERS_SID_MAP.get(sid)
    step = data.get('step')
    step_data = data.get('data', {})
    
    if not player_name or player_name not in GAME_STATE["character_creation"]:
        emit('error', {'message': 'Character creation not started for this player.'})
        return
    
    creation_state = GAME_STATE["character_creation"][player_name]
    creation_state["step"] = step
    creation_state["data"].update(step_data)
    
    # Gestisci i diversi step della creazione
    if step == "race_selected":
        # Razza selezionata, procedi con la classe
        emit('character_creation_step', {
            'step': 'class_selection',
            'message': f'Great choice! Now choose your class:',
            'options': [
                'Fighter', 'Wizard', 'Rogue', 'Cleric', 'Ranger', 
                'Barbarian', 'Bard', 'Paladin', 'Warlock', 'Sorcerer'
            ]
        })
    
    elif step == "class_selected":
        # Classe selezionata, procedi con il background
        emit('character_creation_step', {
            'step': 'background_selection',
            'message': f'Excellent! Now choose your background:',
            'options': [
                'Acolyte', 'Criminal', 'Folk Hero', 'Noble', 'Sage',
                'Soldier', 'Charlatan', 'Entertainer', 'Guild Artisan', 'Hermit'
            ]
        })
    
    elif step == "background_selected":
        # Background selezionato, procedi con le statistiche
        emit('character_creation_step', {
            'step': 'ability_scores',
            'message': f'Perfect! Now let\'s determine your ability scores. You can use standard array or roll for stats.',
            'options': ['Standard Array', 'Roll for Stats']
        })
    
    elif step == "ability_scores_complete":
        # Statistiche complete, finalizza il personaggio
        character_data = create_character_from_creation_data(player_name, creation_state["data"])
        if character_data:
            # Salva il personaggio
            module_name = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
            path_manager = ModulePathManager(module_name)
            char_file = path_manager.get_character_path(normalize_character_name(player_name))
            safe_json_dump(character_data, char_file)
            
            # Aggiungi al game state
            GAME_STATE["character_sheets"][player_name] = character_data
            
            # Rimuovi dallo stato di creazione
            del GAME_STATE["character_creation"][player_name]
            
            emit('character_creation_complete', {
                'message': f'Congratulations {player_name}! Your character has been created successfully.',
                'character_data': character_data
            })
            
            # Ora puoi procedere con il broadcast
            broadcast_full_game_state()
            debug(f"SUCCESS: Character creation completed for {player_name}", category="character_creation")
        else:
            emit('error', {'message': 'Error creating character. Please try again.'})

def handle_player_action_logic(player_name, action_text, sid=None):
    """
    Questa è la funzione logica completa che gestisce un turno di gioco.
    È una fusione della logica di main_game_loop e action_handler.
    """
    global GAME_STATE
    
    # 1. CHECK IF IN COMBAT MODE
    if GAME_STATE["is_in_combat"]:
        # Redirect to combat action handler
        handle_combat_action_logic(player_name, action_text)
        return
    
    # 2. VALIDAZIONE DEL TURNO
    if GAME_STATE["current_turn_player"] and GAME_STATE["current_turn_player"] != player_name:
        try:
            emit('error', {'message': f"Non è il tuo turno. Tocca a {GAME_STATE['current_turn_player']}."})
        except RuntimeError:
            # Se siamo fuori dal contesto di richiesta, usa broadcast
            broadcast_full_game_state(message_type="error", message_content=f"Non è il tuo turno. Tocca a {GAME_STATE['current_turn_player']}.")
        return

    # Invia un feedback visivo immediato che l'azione è stata ricevuta
    broadcast_full_game_state(message_type="player_action", message_content=action_text, message_player=player_name)
    
    # 2. CARICAMENTO DATI FRESCHI
    # Ricarica sempre lo stato dai file per garantire la coerenza
    GAME_STATE["party_tracker"] = safe_json_load("party_tracker.json")
    module_name = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
    path_manager = ModulePathManager(module_name)
    GAME_STATE["plot_data"] = safe_json_load(path_manager.get_plot_path())
    GAME_STATE["module_data"] = safe_json_load(path_manager.get_module_file_path())
    current_area_id = GAME_STATE["party_tracker"]["worldConditions"]["currentAreaId"]
    GAME_STATE["location_data"] = location_manager.get_location_info(
        GAME_STATE["party_tracker"]["worldConditions"]["currentLocation"],
        GAME_STATE["party_tracker"]["worldConditions"]["currentArea"],
        current_area_id
    )
    
    # 3. COSTRUZIONE DELLA DM NOTE
    # Questa è la logica complessa di main.py per costruire la nota per l'AI
    
    # Check ALL modules for plot completion before suggesting module creation
    module_creation_prompt = ""
    should_inject_creation_prompt = False
    try:
        # Use new comprehensive module completion checker
        all_modules_completion = check_all_modules_plot_completion()
        
        # Extract results
        all_modules_complete = all_modules_completion["all_complete"]
        modules_checked = all_modules_completion["modules_checked"]
        completion_summary = all_modules_completion["completion_summary"]
        
        # Print summary of all modules
        debug("=== ALL MODULES COMPLETION SUMMARY ===", category="module_management")
        for module_name, summary in completion_summary.items():
            status = "COMPLETE" if summary["is_complete"] else "INCOMPLETE"
            debug(f"{module_name}: {summary['completed_plots']}/{summary['total_plots']} plots - {status}", category="module_management")
        debug("=== END SUMMARY ===", category="module_management")
        
        # Determine if we should inject module creation prompt
        # Inject if ALL modules are complete OR if user explicitly requested module creation
        user_requesting_module_creation = ("I am ready to embark on a new adventure" in action_text or
                                          "create and explore a new module" in action_text or
                                          "let's create this specific adventure module" in action_text)
        should_inject_creation_prompt = ((all_modules_complete and len(modules_checked) > 0) or 
                                        user_requesting_module_creation)
        
        debug(f"All modules complete: {all_modules_complete}", category="module_management")
        debug(f"User requesting module creation: {user_requesting_module_creation}", category="module_management")
        debug(f"Should inject module creation prompt: {should_inject_creation_prompt}", category="module_management")
        
        # If ALL modules are complete, inject creation prompt
        if should_inject_creation_prompt:
            debug("*** MODULE CREATION PROMPT INJECTION TRIGGERED ***", category="module_management")
            debug("All available modules have completed plots - suggesting new module creation", category="module_management")
            # Load the module creation prompt
            import os
            if os.path.exists("prompts/generators/module_creation_prompt.txt"):
                with open("prompts/generators/module_creation_prompt.txt", "r", encoding="utf-8") as f:
                    module_creation_prompt = "\n\n" + f.read()
                debug(f"Module creation prompt loaded ({len(module_creation_prompt)} characters)", category="module_management")
            else:
                warning("module_creation_prompt.txt not found!", category="module_management")
        else:
            incomplete_modules = [name for name, summary in completion_summary.items() if not summary["is_complete"]]
            if incomplete_modules:
                debug(f"Module creation prompt NOT injected - incomplete modules: {incomplete_modules}", category="module_management")
            else:
                debug("Module creation prompt NOT injected - no modules found to check", category="module_management")
                
    except Exception as e:
        error(f"Error checking module completion: {e}", category="module_management")
    
    # Build DM note - exclude plot/quest info when module creation is active
    if should_inject_creation_prompt:
        # Simplified DM note for module creation - no confusing plot/quest info
        dm_note = (f"Dungeon Master Note: Current date and time: {GAME_STATE['party_tracker']['worldConditions']['time']}. "
                  f"Current location: {GAME_STATE['party_tracker']['worldConditions']['currentLocation']} in {GAME_STATE['party_tracker']['worldConditions']['currentArea']}. ")
    else:
        dm_note = f"Dungeon Master Note: Current date and time: {GAME_STATE['party_tracker']['worldConditions']['time']}. "
        dm_note += f"Current location: {GAME_STATE['party_tracker']['worldConditions']['currentLocation']} in {GAME_STATE['party_tracker']['worldConditions']['currentArea']}. "
    
    # Aggiungi informazioni sui membri del party con slot incantesimo
    party_members = GAME_STATE["party_tracker"].get("partyMembers", [])
    if party_members:
        member_names = []
        for member in party_members:
            member_name = member.get("name", "Unknown")
            member_names.append(member_name)
            
            # Carica i dati del personaggio per ottenere gli slot incantesimo
            try:
                member_data_path = f"modules/{GAME_STATE['party_tracker'].get('module', '').replace(' ', '_')}/characters/{member_name}.json"
                member_data = safe_json_load(member_data_path)
                if member_data:
                    spellcasting = member_data.get("spellcasting", {})
                    if spellcasting and "spellSlots" in spellcasting:
                        spell_slots = spellcasting["spellSlots"]
                        slot_parts = []
                        for level in range(1, 10):  # Spell levels 1-9
                            level_key = f"level{level}"
                            if level_key in spell_slots:
                                slot_data = spell_slots[level_key]
                                current = slot_data.get("current", 0)
                                maximum = slot_data.get("max", 0)
                                if maximum > 0:  # Only show levels with available slots
                                    slot_parts.append(f"L{level}:{current}/{maximum}")
                        if slot_parts:
                            member_names[-1] += f" (Spell Slots: {' '.join(slot_parts)})"
            except Exception as e:
                debug(f"Error loading spell slots for {member_name}: {e}", category="spell_system")
        
        dm_note += f"Party members: {', '.join(member_names)}. "
    
    # Add the action text and module creation prompt if needed
    if should_inject_creation_prompt:
        dm_note += (f"Player ({player_name}): {action_text}. "
                   "Consider whether the party's action trigger traps in this location. "
                   "Consider updating the plot elements on every action the player and NPCs take."
                   f"{module_creation_prompt}")
    else:
        dm_note += f"Player ({player_name}): {action_text}"
    
    # 4. AGGIORNAMENTO CRONOLOGIA E CHIAMATA AI
    GAME_STATE["conversation_history"].append({"role": "user", "content": dm_note})
    
    # Salva la cronologia prima di chiamare l'AI
    safe_write_json("modules/conversation_history/conversation_history.json", GAME_STATE["conversation_history"])
    
    ai_response_content = get_ai_response(GAME_STATE["conversation_history"], action_text=action_text)
    if not ai_response_content:
        try:
            emit('error', {'message': 'Failed to get AI response. Please try again.'})
        except RuntimeError:
            broadcast_full_game_state(message_type="error", message_content="Failed to get AI response. Please try again.")
        return
    
    # DEBUG: Log AI response for module creation requests
    if "create" in action_text.lower() and "module" in action_text.lower():
        debug(f"MODULE_CREATION_DEBUG: AI Response for module creation request:", category="module_creation")
        debug(f"MODULE_CREATION_DEBUG: {ai_response_content[:500]}...", category="module_creation")
    
    # Process inventory responses with comprehensive verification system
    ai_response_content, was_modified = process_inventory_response(ai_response_content, player_name, action_text)
    if was_modified:
        debug(f"INVENTORY: Response modified for {player_name}", category="inventory_system")
    
    # FORCE createNewModule action when module creation is requested but AI didn't generate it
    if should_inject_creation_prompt and '"action": "createNewModule"' not in ai_response_content:
        debug("FORCED MODULE CREATION: AI didn't generate createNewModule action, forcing it", category="module_management")
        
        # Try to extract module details from the user's request
        module_narrative = ""
        if "Module Name:" in action_text and "Adventure Type:" in action_text:
            # User provided explicit module details
            module_narrative = action_text
            debug("FORCED MODULE CREATION: Using user-provided module details", category="module_management")
        else:
            # Use AI's narrative content
            module_narrative = ai_response_content.strip()
            debug("FORCED MODULE CREATION: Using AI-generated narrative", category="module_management")
        
        # Force the correct JSON action
        forced_action = {
            "narration": "The threads of fate weave together, opening a path to new adventures. Your destiny calls from distant lands...",
            "actions": [
                {
                    "action": "createNewModule",
                    "parameters": {
                        "narrative": module_narrative
                    }
                }
            ]
        }
        
        ai_response_content = json.dumps(forced_action, indent=2)
        debug(f"FORCED MODULE CREATION: Converted to createNewModule action", category="module_management")
    
    # Validazione della risposta AI
    validation_prompt_text = load_validation_prompt()
    validation_result = validate_ai_response(
        ai_response_content, 
        action_text, 
        validation_prompt_text, 
        GAME_STATE["conversation_history"], 
        GAME_STATE["party_tracker"]
    )
    
    # Enhanced validation with DMResponseValidator (non-breaking addition)
    if validation_result is True:
        try:
            dm_validator = DMResponseValidator()
            dm_valid, dm_errors, dm_details = dm_validator.validate_response(ai_response_content)
            
            if not dm_valid:
                debug(f"DM_VALIDATION: Advanced validation failed - {', '.join(dm_errors)}", category="validation")
                # Log but don't block - this is additional validation
                for error in dm_errors:
                    warning(f"DM_VALIDATION: {error}", category="validation")
                
                # Notify all players of validation warnings
                socketio.emit('validation_warning', {
                    'player': player_name,
                    'errors': dm_errors,
                    'timestamp': datetime.now().isoformat(),
                    'severity': 'warning'
                })
            else:
                debug("DM_VALIDATION: Advanced validation passed", category="validation")
        except Exception as e:
            # Non-breaking - log and continue
            debug(f"DM_VALIDATION: Exception in advanced validation - {str(e)}", category="validation")
    
    if validation_result is not True:
        # Retry con il modello di validazione
        ai_response_content = get_ai_response(GAME_STATE["conversation_history"], validation_retry_count=1, action_text=action_text)
        if not ai_response_content:
            try:
                emit('error', {'message': 'Failed to get valid AI response. Please try again.'})
            except RuntimeError:
                broadcast_full_game_state(message_type="error", message_content="Failed to get valid AI response. Please try again.")
            return
        
        # Process inventory responses with comprehensive verification system on retry too
        ai_response_content, was_modified = process_inventory_response(ai_response_content, player_name, action_text)
        if was_modified:
            debug(f"INVENTORY: Response modified for {player_name} on retry", category="inventory_system")
    
    # 5. ELABORAZIONE DELLA RISPOSTA AI (IL CUORE DEL SISTEMA)
    try:
        # Extract JSON from markdown codeblocks if present
        def extract_json_from_codeblock(text):
            import re
            match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
            if match:
                return match.group(1)
            return text
        
        json_content = extract_json_from_codeblock(ai_response_content)
        
        # Try to parse as JSON
        try:
            parsed_response = json.loads(json_content)
        except json.JSONDecodeError:
            # AI generated plain text instead of JSON - wrap it in proper format
            warning("AI generated plain text instead of JSON - converting to proper format", category="ai_communication")
            
            # Check if this looks like a level-up request
            if any(word in ai_response_content.lower() for word in ['level up', 'level 2', 'hit points', 'class features']):
                debug("Converting level-up narrative to levelUp action", category="level_up")
                parsed_response = {
                    "narration": "Level-up process initiated. Please wait for the level-up interface.",
                    "actions": [
                        {
                            "action": "levelUp",
                            "parameters": {
                                "entityName": player_name,  # Use the actual player name
                                "newLevel": 2
                            }
                        }
                    ]
                }
            else:
                # Default: wrap plain text as narration only
                parsed_response = {
                    "narration": ai_response_content,
                    "actions": []
                }
        narration = parsed_response.get("narration", "Il DM descrive la scena...")
        actions = parsed_response.get("actions", [])

        # --- LEVEL-UP DETECTION: Process levelUp actions immediately before narration ---
        is_levelup_action = any(action.get("action") == "levelUp" for action in actions)
        
        if is_levelup_action:
            debug("STATE_CHANGE: levelUp action detected. Processing directly without narration.", category="level_up")
            # Process ONLY the levelUp action to start the session
            for action in actions:
                if action.get("action") == "levelUp":
                    result = process_action(
                        action,
                        GAME_STATE["party_tracker"], 
                        GAME_STATE["location_data"], 
                        GAME_STATE["conversation_history"]
                    )
                    
                    # Handle level-up session start
                    if isinstance(result, dict) and result.get("status") == "enter_levelup_mode":
                        debug("STATE_CHANGE: Entering level-up mode", category="action_processing")
                        
                        level_up_session = result.get("session")
                        if level_up_session:
                            info(f"Level-up session for {level_up_session.character_name} from level {level_up_session.current_level} to {level_up_session.new_level}", category="level_up")
                            
                            # Store the session for the specific player
                            # Use sid parameter passed from the main thread
                            if sid:
                                player_name = PLAYERS_SID_MAP.get(sid)
                            # Fall back to using the player_name parameter if sid is not available
                            LEVEL_UP_SESSIONS[player_name] = level_up_session
                            
                            # Start the level-up session and get the first message
                            try:
                                dm_response = level_up_session.start()
                                
                                # Send level-up specific UI event
                                socketio.emit('level_up_started', {
                                    'character_name': level_up_session.character_name,
                                    'current_level': level_up_session.current_level,
                                    'new_level': level_up_session.new_level,
                                    'dm_response': dm_response
                                }, to=sid)
                                
                                return  # Exit early - level-up mode started successfully
                                
                            except Exception as e:
                                error(f"LEVEL_UP: Error starting level-up session: {e}", category="level_up")
                                broadcast_full_game_state(message_type="error", message_content="Level-up session failed to start.")
                                return
                    
                    break  # Only process the first levelUp action
            return  # Exit early if levelUp was processed
        # --- END LEVEL-UP DETECTION ---

        # Invia la narrazione iniziale ai giocatori (only if not level-up)
        broadcast_full_game_state(message_type="dm", message_content=narration)

        # Processa ogni azione ricevuta dall'AI
        for action in actions:
            # DEBUG: Log all actions for module creation requests
            if "create" in action_text.lower() and "module" in action_text.lower():
                debug(f"MODULE_CREATION_DEBUG: Processing action: {action}", category="module_creation")
            
            # Check for module creation action - needs special handling for progress tracking
            if action.get("action") == "createNewModule":
                debug("MODULE_CREATION: createNewModule action detected - starting with progress tracking", category="module_management")
                
                # Notify all players that module creation is starting
                socketio.emit('module_creation_started', {
                    'message': 'Module creation in progress... This may take several minutes.',
                    'parameters': action.get("parameters", {})
                })
                
                # Process the action with progress updates
                try:
                    result = process_action(
                        action, 
                        GAME_STATE["party_tracker"], 
                        GAME_STATE["location_data"], 
                        GAME_STATE["conversation_history"]
                    )
                    
                    # Check if module creation was successful
                    if isinstance(result, dict) and result.get("success"):
                        debug("MODULE_CREATION: Module creation completed successfully", category="module_management")
                        socketio.emit('module_creation_completed', {
                            'success': True,
                            'message': 'New module created successfully! You can now explore this new adventure.',
                            'needs_dm_response': result.get("needs_dm_response", False)
                        })
                        
                        # Reload game state to reflect new module
                        reload_game_state()
                        
                    else:
                        error("MODULE_CREATION: Module creation failed", category="module_management")
                        socketio.emit('module_creation_completed', {
                            'success': False,
                            'message': 'Module creation failed. Please try again.'
                        })
                
                except Exception as e:
                    error(f"MODULE_CREATION: Error during module creation: {e}", category="module_management")
                    socketio.emit('module_creation_completed', {
                        'success': False,
                        'message': f'Module creation error: {str(e)}'
                    })
                
                continue  # Skip normal processing for module creation
            
            # Normal action processing for other actions
            result = process_action(
                action, 
                GAME_STATE["party_tracker"], 
                GAME_STATE["location_data"], 
                GAME_STATE["conversation_history"]
            )
            
            # FORCE VERIFICATION: Check if inventory updates actually succeeded
            if action.get("action") == "updateCharacterInfo" and was_modified:
                char_name = action.get("parameters", {}).get("characterName")
                changes = action.get("parameters", {}).get("changes", "")
                
                if char_name and "equipment" in changes:
                    from core.managers.inventory_manager import InventoryManager
                    inv_manager = InventoryManager()
                    
                    # Give the file system a moment to update
                    import time
                    time.sleep(0.2)
                    
                    # Parse what items should have been added
                    expected_items = []
                    if "Added" in changes and "equipment" in changes:
                        # Extract items from changes description
                        import re
                        item_matches = re.findall(r'Added ([^(]+)(?:\s*\((\d+)\))?\s+to equipment', changes)
                        for match in item_matches:
                            item_name = match[0].strip()
                            quantity = match[1] if match[1] else "1"
                            expected_items.append({"name": item_name, "quantity": quantity})
                    
                    if expected_items:
                        # Check if items were actually added
                        current_inventory = inv_manager.get_character_inventory_snapshot(char_name)
                        equipment = current_inventory.get("equipment", [])
                        
                        added_count = 0
                        for expected in expected_items:
                            for item in equipment:
                                if expected["name"].lower() in item.get("item_name", "").lower():
                                    added_count += 1
                                    break
                        
                        if added_count == len(expected_items):
                            info(f"INVENTORY_SUCCESS: All {len(expected_items)} items verified in {char_name}'s equipment", category="inventory_verification")
                        else:
                            warning(f"INVENTORY_FAILED: Only {added_count}/{len(expected_items)} items found in {char_name}'s equipment", category="inventory_verification")
                            
                            # FORCE RETRY - Use AI fallback to re-analyze and try again
                            retry_action = inv_manager.ai_fallback_analysis(ai_response_content, action_text, char_name)
                            if retry_action:
                                info(f"INVENTORY_RETRY: Attempting forced retry for {char_name}", category="inventory_verification")
                                # Process the retry action immediately
                                retry_result = process_action(
                                    retry_action["actions"][0],
                                    GAME_STATE["party_tracker"], 
                                    GAME_STATE["location_data"], 
                                    GAME_STATE["conversation_history"]
                                )
            
            # GESTIONE DEI SOTTOSISTEMI SPECIALI
            if isinstance(result, dict):
                # Caso 1: Inizio di un nuovo combattimento
                if result.get("status") == "start_combat":
                    encounter_id = result.get("encounter_id")
                    if encounter_id and not GAME_STATE["is_in_combat"]:
                        debug(f"COMBAT: Starting combat session for encounter {encounter_id}", category="combat_events")
                        if start_combat_session(encounter_id):
                            # Combat session started successfully, break out of action processing
                            break
                        else:
                            error(f"COMBAT: Failed to start combat session for encounter {encounter_id}", category="combat_events")
                            broadcast_full_game_state(message_type="error", message_content="Failed to start combat session.")
                            break
                
                # Caso 2: Il combattimento è terminato
                elif result.get("status") == "needs_post_combat_narration":
                    print("SERVER: Combattimento terminato. Richiedo narrazione post-combattimento.")
                    # La cronologia è già stata aggiornata dall'handler, quindi basta richiamare l'AI
                    post_combat_history = safe_json_load("modules/conversation_history/conversation_history.json")
                    post_combat_narration = get_ai_response(post_combat_history)
                    
                    # Processa la narrazione post-combattimento
                    if post_combat_narration:
                        try:
                            parsed_post_combat = json.loads(post_combat_narration)
                            post_narration = parsed_post_combat.get("narration", "Il combattimento è terminato.")
                            # Invia la narrazione post-combattimento
                            broadcast_full_game_state(message_type="dm", message_content=post_narration)
                            # Aggiungi alla cronologia
                            post_combat_history.append({"role": "assistant", "content": post_combat_narration})
                            save_conversation_history(post_combat_history)
                        except json.JSONDecodeError:
                            # Se non è JSON valido, invia come testo semplice
                            broadcast_full_game_state(message_type="dm", message_content=post_combat_narration)
                            post_combat_history.append({"role": "assistant", "content": post_combat_narration})
                            save_conversation_history(post_combat_history)
                    
                    break # Esce dal ciclo delle azioni
                    
                # Caso 3: Level-up handled earlier in special detection - this should not happen
                elif result.get("status") == "enter_levelup_mode":
                    warning("LEVEL_UP: levelUp action processed in normal flow - should have been caught earlier", category="level_up")
                    break
                    
        # 6. SALVATAGGIO E AGGIORNAMENTO FINALE
        # Aggiungi la risposta completa dell'AI alla cronologia
        GAME_STATE["conversation_history"].append({"role": "assistant", "content": ai_response_content})
        safe_write_json("modules/conversation_history/conversation_history.json", GAME_STATE["conversation_history"])

        # 6.1 MODULE TRANSITION PROCESSING - Check for module transitions and compress history
        try:
            # Check and process any module transitions with timeline preservation
            processed_history = transition_manager.check_and_process_module_transitions(
                GAME_STATE["conversation_history"], 
                GAME_STATE["party_tracker"]
            )
            
            # Update conversation history if it was processed (compressed)
            if len(processed_history) != len(GAME_STATE["conversation_history"]):
                GAME_STATE["conversation_history"] = processed_history
                debug(f"Module transition processed: history compressed from {len(GAME_STATE['conversation_history'])} to {len(processed_history)} messages", 
                     category="module_transitions")
        except Exception as e:
            error(f"Failed to process module transitions", exception=e, category="module_transitions")

        # 6.2 LOCATION TRANSITION PROCESSING - Check for location transitions and compress history
        try:
            from enhanced_location_transitions import check_and_process_location_transitions_multiplayer
            
            # Get current path manager for location processing
            current_module = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
            path_manager = ModulePathManager(current_module) if current_module else ModulePathManager()
            
            # Check and process any location transitions with enhanced summaries
            processed_history = check_and_process_location_transitions_multiplayer(
                GAME_STATE["conversation_history"], 
                GAME_STATE["party_tracker"],
                path_manager
            )
            
            # Update conversation history if it was processed (compressed)
            if len(processed_history) != len(GAME_STATE["conversation_history"]):
                original_length = len(GAME_STATE["conversation_history"])
                GAME_STATE["conversation_history"] = processed_history
                safe_write_json("modules/conversation_history/conversation_history.json", GAME_STATE["conversation_history"])
                debug(f"Location transition processed: history compressed from {original_length} to {len(processed_history)} messages", 
                     category="location_transitions")
                info("Enhanced location transition processing completed", category="location_transitions")
        except Exception as e:
            debug(f"Location transition processing failed (non-critical): {str(e)}", category="location_transitions")

        # 6.3 EFFECT EXPIRATION PROCESSING - Check for expired character effects
        try:
            from updates.process_effect_expirations import process_all_effect_expirations
            debug("EFFECTS: Checking for expired effects", category="effects_tracking")
            process_all_effect_expirations()
        except Exception as e:
            debug(f"EFFECTS: Failed to process effect expirations: {str(e)}", category="effects_tracking")
            # Don't break the game if effects processing fails

        # Ricarica lo stato finale dopo tutte le azioni
        GAME_STATE["party_tracker"] = safe_json_load("party_tracker.json")
        
        # 7. CAMBIO TURNO
        if GAME_STATE["turn_order"]:
            if player_name in GAME_STATE["turn_order"]:
                current_index = GAME_STATE["turn_order"].index(player_name)
                next_index = (current_index + 1) % len(GAME_STATE["turn_order"])
                GAME_STATE["current_turn_player"] = GAME_STATE["turn_order"][next_index]
            else: # Se il giocatore non è in lista, passa al primo
                 GAME_STATE["current_turn_player"] = GAME_STATE["turn_order"][0]

        # 8. INVIA AGGIORNAMENTO COMPLETO
        # Invia lo stato finale a tutti i giocatori, che vedranno la narrazione,
        # le modifiche e di chi è il turno successivo.
        broadcast_full_game_state()

    except json.JSONDecodeError:
        print(f"ERRORE: Impossibile parsare la risposta dell'AI: {ai_response_content}")
        # Gestisci l'errore, inviando la risposta grezza come narrazione
        broadcast_full_game_state(message_type="dm", message_content=ai_response_content)
        
        # Aggiungi la risposta grezza alla cronologia
        GAME_STATE["conversation_history"].append({"role": "assistant", "content": ai_response_content})
        safe_write_json("modules/conversation_history/conversation_history.json", GAME_STATE["conversation_history"])
        
        # Gestisci il cambio turno anche in caso di errore
        if GAME_STATE["turn_order"]:
            if player_name in GAME_STATE["turn_order"]:
                current_index = GAME_STATE["turn_order"].index(player_name)
                next_index = (current_index + 1) % len(GAME_STATE["turn_order"])
                GAME_STATE["current_turn_player"] = GAME_STATE["turn_order"][next_index]
            else:
                GAME_STATE["current_turn_player"] = GAME_STATE["turn_order"][0]
        
        # Invia aggiornamento finale
        broadcast_full_game_state()

@socketio.on('request_game_state')
def handle_game_state_request():
    """Handle request for current game state"""
    current_state = get_current_state_for_client()
    emit('game_state_response', current_state)

@socketio.on('request_player_data')
def handle_player_data_request(data):
    """Handle player data request from client"""
    sid = request.sid
    player_name = PLAYERS_SID_MAP.get(sid)
    data_type = data.get('dataType', 'stats')
    
    if not player_name:
        emit('error', {'message': 'Player not found.'})
        return
    
    # Se i dati del personaggio non sono caricati, prova a ricaricarli
    if player_name not in GAME_STATE["character_sheets"]:
        success = reload_character_data(player_name)
        if not success:
            emit('error', {'message': f'Character data not found for {player_name}.'})
            return
    
    if player_name in GAME_STATE["character_sheets"]:
        character_data = GAME_STATE["character_sheets"][player_name]
        
        # Filtra i dati in base al tipo richiesto
        filtered_data = {}
        
        if data_type == 'stats':
            # Dati per la tab Stats
            filtered_data = {
                'name': character_data.get('name'),
                'level': character_data.get('level', 1),
                'race': character_data.get('race'),
                'class': character_data.get('class'),
                'background': character_data.get('background'),
                'alignment': character_data.get('alignment'),
                'status': character_data.get('status'),
                'hitPoints': character_data.get('hitPoints', 0),
                'maxHitPoints': character_data.get('maxHitPoints', 0),
                'armorClass': character_data.get('armorClass', 10),
                'initiative': character_data.get('initiative', 0),
                'abilities': character_data.get('abilities', {}),
                'savingThrows': character_data.get('savingThrows', {}),
                'skills': character_data.get('skills', {}),
                'proficiencyBonus': character_data.get('proficiencyBonus', 2),
                'experience_points': character_data.get('experience_points', 0),
                'exp_required_for_next_level': character_data.get('exp_required_for_next_level', 300),
                'currency': character_data.get('currency', {'gold': 0, 'silver': 0, 'copper': 0})
            }
        elif data_type == 'inventory':
            # Dati per la tab Inventory
            filtered_data = {
                'name': character_data.get('name'),
                'equipment': character_data.get('equipment', []),
                'currency': character_data.get('currency', {'gold': 0, 'silver': 0, 'copper': 0})
            }
        elif data_type == 'spells':
            # Dati per la tab Spells
            filtered_data = {
                'name': character_data.get('name'),
                'spellcasting': character_data.get('spellcasting', {})
            }
        else:
            # Per qualsiasi altro tipo, restituisci tutti i dati
            filtered_data = character_data
        
        emit('player_data_response', {
            'dataType': data_type,
            'data': filtered_data
        })
    else:
        emit('error', {'message': f'Character data not found for {player_name}.'})

def reload_character_data(player_name):
    """Reload character data from file"""
    try:
        if GAME_STATE["party_tracker"]:
            module_name = GAME_STATE["party_tracker"].get("module", "").replace(" ", "_")
            path_manager = ModulePathManager(module_name)
            char_file = path_manager.get_character_path(normalize_character_name(player_name))
            
            debug(f"DEBUG: Ricaricando personaggio '{player_name}' da file: {char_file}", category="character_reload")
            
            char_data = safe_json_load(char_file)
            if char_data:
                GAME_STATE["character_sheets"][player_name] = char_data
                info(f"SUCCESS: Dati del personaggio per '{player_name}' ricaricati.", category="character_reload")
                return True
            else:
                warning(f"ATTENZIONE: File del personaggio per '{player_name}' non trovato durante il ricaricamento.", category="character_reload")
                return False
        else:
            warning(f"ATTENZIONE: party_tracker non disponibile per ricaricare il personaggio di '{player_name}'", category="character_reload")
            return False
    except Exception as e:
        error(f"ERRORE durante il ricaricamento del personaggio per '{player_name}': {e}", category="character_reload")
        return False

@socketio.on('reload_character_data')
def handle_reload_character_data(data):
    """Handle character data reload request from client"""
    sid = request.sid
    player_name = PLAYERS_SID_MAP.get(sid)
    
    if not player_name:
        emit('error', {'message': 'Player not found.'})
        return
    
    success = reload_character_data(player_name)
    if success:
        emit('character_data_reloaded', {
            'message': f'Character data for {player_name} has been reloaded successfully.'
        })
    else:
        emit('error', {'message': f'Failed to reload character data for {player_name}.'})

@socketio.on('request_plot_data')
def handle_plot_data_request():
    """Handle plot data request from client"""
    try:
        print("DEBUG: Received plot data request from client")
        
        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")
        print(f"DEBUG: Current module: {current_module}")
        
        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"
        print(f"DEBUG: Looking for plot file: {plot_file_path}")
        
        if os.path.exists(plot_file_path):
            print(f"DEBUG: Found plot file, loading data...")
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)
            
            print(f"DEBUG: Loaded plot data with {len(plot_data.get('plotPoints', []))} plot points")
            
            # Activate first quest if no quests are active
            plot_data = activate_first_quest_if_needed(plot_data, current_module)
            
            print(f"DEBUG: Sending plot data response to client")
            emit('plot_data_response', {
                'dataType': 'quests',
                'data': plot_data
            })
        else:
            print(f"DEBUG: Plot file not found, trying backup...")
            # Fallback to backup file
            backup_plot_path = f"modules/{current_module}/module_plot_BU.json"
            if os.path.exists(backup_plot_path):
                print(f"DEBUG: Found backup plot file, loading data...")
                with open(backup_plot_path, 'r', encoding='utf-8') as f:
                    plot_data = json.load(f)
                
                # Activate first quest if no quests are active
                plot_data = activate_first_quest_if_needed(plot_data, current_module)
                
                print(f"DEBUG: Sending backup plot data response to client")
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
            else:
                print(f"ERROR: No plot data found for module {current_module}")
                emit('error', {'message': 'Plot data not found.'})
                
    except Exception as e:
        print(f"ERROR: Error loading plot data: {e}")
        emit('error', {'message': f'Error loading plot data: {str(e)}'})

@socketio.on('chat_message')
def handle_chat_message(data):
    """Handle chat messages between players"""
    sid = request.sid
    player_name = data.get('player_name', f'Player_{sid[:8]}')
    message = data.get('message', '')
    
    if not message.strip():
        return
    
    # Broadcast chat message to all players
    socketio.emit('chat_message', {
        'player': player_name,
        'message': message,
        'timestamp': datetime.now().isoformat()
    })

# ============================================================================
# COMBAT EVENT HANDLERS
# ============================================================================

@socketio.on('combat_action')
def on_combat_action(data):
    """Handle combat actions from players"""
    sid = request.sid
    player_name = data.get('player_name')
    action_text = data.get('text', '')
    
    if not action_text.strip():
        emit('error', {'message': 'Please provide a combat action to perform.'})
        return
    
    # Check if combat is active
    if not GAME_STATE["is_in_combat"] or not GAME_STATE["active_combat"]:
        emit('error', {'message': 'No active combat session.'})
        return
    
    # Check if player is connected
    if sid not in GAME_STATE["connected_players"]:
        emit('error', {'message': 'You are not connected to the game.'})
        return
    
    # Process combat action in background thread
    socketio.start_background_task(target=handle_combat_action_logic, player_name=player_name, action_text=action_text)

@socketio.on('request_combat_state')
def handle_combat_state_request():
    """Handle requests for current combat state"""
    if GAME_STATE["is_in_combat"] and GAME_STATE["active_combat"]:
        combat_state = GAME_STATE["active_combat"].get_current_combat_state()
        emit('combat_state_update', combat_state)
    else:
        emit('combat_state_update', {"is_active": False})

@socketio.on('activate_quest')
def handle_activate_quest(data):
    """Handle manual quest activation request"""
    try:
        quest_id = data.get('quest_id')
        quest_type = data.get('quest_type', 'main')  # 'main' or 'side'
        
        print(f"DEBUG: Received quest activation request - ID: {quest_id}, Type: {quest_type}")
        
        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")
        
        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"
        
        if os.path.exists(plot_file_path):
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)
            
            # Find and activate the quest
            quest_activated = False
            for plot_point in plot_data['plotPoints']:
                if quest_type == 'main' and plot_point.get('id') == quest_id:
                    if plot_point.get('status') == 'not started':
                        plot_point['status'] = 'in progress'
                        plot_point['plotImpact'] = 'Quest activated manually'
                        quest_activated = True
                        print(f"INFO: Manually activated main quest '{plot_point.get('title', 'Unknown')}'")
                        break
                elif quest_type == 'side' and plot_point.get('sideQuests'):
                    for side_quest in plot_point['sideQuests']:
                        if side_quest.get('id') == quest_id:
                            if side_quest.get('status') == 'not started':
                                side_quest['status'] = 'available'
                                side_quest['plotImpact'] = 'Side quest activated manually'
                                quest_activated = True
                                print(f"INFO: Manually activated side quest '{side_quest.get('title', 'Unknown')}'")
                                break
                    if quest_activated:
                        break
            
            if quest_activated:
                # Save the updated plot data
                with open(plot_file_path, 'w', encoding='utf-8') as f:
                    json.dump(plot_data, f, indent=2, ensure_ascii=False)
                
                # Send updated plot data to client
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
                print(f"DEBUG: Quest activation successful, sent updated data to client")
            else:
                emit('error', {'message': f'Quest {quest_id} not found or already active'})
                print(f"DEBUG: Quest activation failed - quest not found or already active")
        else:
            emit('error', {'message': 'Plot data not found'})
            print(f"ERROR: Plot file not found for module {current_module}")
                
    except Exception as e:
        print(f"ERROR: Error activating quest: {e}")
        emit('error', {'message': f'Error activating quest: {str(e)}'})

@socketio.on('close_quest')
def handle_close_quest(data):
    """Handle quest closure/cancellation request"""
    try:
        quest_id = data.get('quest_id')
        quest_type = data.get('quest_type', 'main')  # 'main' or 'side'

        print(f"DEBUG: Received quest closure request - ID: {quest_id}, Type: {quest_type}")

        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")

        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"

        if os.path.exists(plot_file_path):
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)

            # Find and close the quest
            quest_closed = False
            for plot_point in plot_data['plotPoints']:
                if quest_type == 'main' and plot_point.get('id') == quest_id:
                    if plot_point.get('status') in ['in progress', 'available', 'not started']:
                        plot_point['status'] = 'cancelled'
                        plot_point['plotImpact'] = 'Quest cancelled by player'
                        quest_closed = True
                        print(f"INFO: Closed main quest '{plot_point.get('title', 'Unknown')}'")
                        break
                elif quest_type == 'side' and plot_point.get('sideQuests'):
                    for side_quest in plot_point['sideQuests']:
                        if side_quest.get('id') == quest_id:
                            if side_quest.get('status') in ['available', 'not started']:
                                side_quest['status'] = 'cancelled'
                                side_quest['plotImpact'] = 'Side quest cancelled by player'
                                quest_closed = True
                                print(f"INFO: Closed side quest '{side_quest.get('title', 'Unknown')}'")
                                break
                    if quest_closed:
                        break

            if quest_closed:
                # Save the updated plot data
                with open(plot_file_path, 'w', encoding='utf-8') as f:
                    json.dump(plot_data, f, indent=2, ensure_ascii=False)

                # Send updated plot data to client
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
                print(f"DEBUG: Quest closure successful, sent updated data to client")
            else:
                emit('error', {'message': f'Quest {quest_id} not found or cannot be closed'})
                print(f"DEBUG: Quest closure failed - quest not found or cannot be closed")
        else:
            emit('error', {'message': 'Plot data not found'})
            print(f"ERROR: Plot file not found for module {current_module}")

    except Exception as e:
        print(f"ERROR: Error closing quest: {e}")
        emit('error', {'message': f'Error closing quest: {str(e)}'})

@socketio.on('reset_all_quests')
def handle_reset_all_quests(data):
    """Handle reset all quests to initial state"""
    try:
        print(f"DEBUG: Received reset all quests request")

        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")

        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"

        if os.path.exists(plot_file_path):
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)

            # Reset all quests to 'not started'
            quests_reset = 0
            for plot_point in plot_data['plotPoints']:
                # Reset main quest
                if plot_point.get('status') != 'not started':
                    plot_point['status'] = 'not started'
                    plot_point['plotImpact'] = 'Quest reset to initial state'
                    quests_reset += 1
                    print(f"INFO: Reset main quest '{plot_point.get('title', 'Unknown')}' to not started")
                
                # Reset side quests
                if plot_point.get('sideQuests'):
                    for side_quest in plot_point['sideQuests']:
                        if side_quest.get('status') != 'not started':
                            side_quest['status'] = 'not started'
                            side_quest['plotImpact'] = 'Side quest reset to initial state'
                            quests_reset += 1
                            print(f"INFO: Reset side quest '{side_quest.get('title', 'Unknown')}' to not started")

            if quests_reset > 0:
                # Save the updated plot data
                with open(plot_file_path, 'w', encoding='utf-8') as f:
                    json.dump(plot_data, f, indent=2, ensure_ascii=False)

                # Send updated plot data to client
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
                print(f"DEBUG: Reset {quests_reset} quests successfully, sent updated data to client")
                emit('success', {'message': f'Successfully reset {quests_reset} quests to initial state'})
            else:
                emit('info', {'message': 'All quests are already in initial state'})
                print(f"DEBUG: No quests needed reset - all already in initial state")
        else:
            emit('error', {'message': 'Plot data not found'})
            print(f"ERROR: Plot file not found for module {current_module}")

    except Exception as e:
        print(f"ERROR: Error resetting quests: {e}")
        emit('error', {'message': f'Error resetting quests: {str(e)}'})

def activate_first_quest_if_needed(plot_data, current_module):
    """Activate the first quest if no quests are currently active"""
    try:
        if not plot_data or 'plotPoints' not in plot_data:
            print(f"DEBUG: No plot data or plotPoints found for module {current_module}")
            return plot_data
        
        # Check if any quest is active (in progress or completed)
        has_active_quest = False
        for plot_point in plot_data['plotPoints']:
            if plot_point.get('status') in ['in progress', 'completed']:
                has_active_quest = True
                print(f"DEBUG: Found active quest '{plot_point.get('title', 'Unknown')}' with status '{plot_point.get('status')}'")
                break
        
        # Riabilita l'attivazione automatica delle quest
        if not has_active_quest and plot_data['plotPoints']:
            first_quest = plot_data['plotPoints'][0]
            if first_quest.get('status') == 'not started':
                first_quest['status'] = 'in progress'
                first_quest['plotImpact'] = 'First quest activated automatically'
                print(f"INFO: Activated first quest '{first_quest.get('title', 'Unknown')}' in module {current_module}")
                
                # Also activate the first side quest if available
                if first_quest.get('sideQuests'):
                    for side_quest in first_quest['sideQuests']:
                        if side_quest.get('status') == 'not started':
                            side_quest['status'] = 'available'
                            side_quest['plotImpact'] = 'Side quest available'
                            print(f"INFO: Activated side quest '{side_quest.get('title', 'Unknown')}'")
                            break
        else:
            print(f"DEBUG: No quest activation needed - has_active_quest: {has_active_quest}")
        
        return plot_data
    except Exception as e:
        print(f"Error activating first quest: {e}")
        return plot_data

def create_character_from_creation_data(player_name, creation_data):
    """Create a complete character from creation data"""
    try:
        from utils.character_creation_template import get_default_character_template, get_starting_equipment
        
        race = creation_data.get('race', 'Human')
        character_class = creation_data.get('class', 'Fighter')
        background = creation_data.get('background', 'Folk Hero')
        abilities = creation_data.get('abilities', {
            'strength': 10, 'dexterity': 10, 'constitution': 10,
            'intelligence': 10, 'wisdom': 10, 'charisma': 10
        })
        
        # Get schema-compliant template
        character_data = get_default_character_template(
            name=player_name,
            character_class=character_class,
            race=race,
            background=background,
            abilities=abilities
        )
        
        # Update with class-specific data
        character_data["skills"] = get_class_skills(character_class, background)
        character_data["proficiencies"] = get_class_proficiencies(character_class)
        character_data["classFeatures"] = get_class_features(character_class)
        character_data["equipment"] = get_starting_equipment(character_class, background)
        
        # Add spellcasting if applicable
        spellcasting_data = get_spellcasting_data(character_class, abilities)
        if spellcasting_data:
            character_data["spellcasting"] = spellcasting_data
        
        return character_data
        
    except Exception as e:
        error(f"ERRORE durante la creazione del personaggio per '{player_name}': {e}", category="character_creation")
        return None

def get_spellcasting_data(character_class, abilities):
    """Get spellcasting data for spellcasting classes"""
    spellcasting_classes = {
        'Wizard': {
            'ability': 'intelligence',
            'spellSaveDC': 8 + 2 + (abilities['intelligence'] - 10) // 2,  # 8 + prof + mod
            'spellAttackBonus': 2 + (abilities['intelligence'] - 10) // 2,  # prof + mod
            'spells': {
                'cantrips': ['Fire Bolt', 'Light', 'Mage Hand', 'Prestidigitation'],
                'level1': ['Magic Missile', 'Shield', 'Sleep'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 2, 'max': 2},
                'level2': {'current': 0, 'max': 0},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        },
        'Cleric': {
            'ability': 'wisdom',
            'spellSaveDC': 8 + 2 + (abilities['wisdom'] - 10) // 2,
            'spellAttackBonus': 2 + (abilities['wisdom'] - 10) // 2,
            'spells': {
                'cantrips': ['Guidance', 'Light', 'Sacred Flame', 'Thaumaturgy'],
                'level1': ['Cure Wounds', 'Detect Magic', 'Guiding Bolt', 'Healing Word'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 2, 'max': 2},
                'level2': {'current': 0, 'max': 0},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        },
        'Sorcerer': {
            'ability': 'charisma',
            'spellSaveDC': 8 + 2 + (abilities['charisma'] - 10) // 2,
            'spellAttackBonus': 2 + (abilities['charisma'] - 10) // 2,
            'spells': {
                'cantrips': ['Fire Bolt', 'Light', 'Mage Hand', 'Prestidigitation'],
                'level1': ['Burning Hands', 'Magic Missile', 'Shield'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 2, 'max': 2},
                'level2': {'current': 0, 'max': 0},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        },
        'Bard': {
            'ability': 'charisma',
            'spellSaveDC': 8 + 2 + (abilities['charisma'] - 10) // 2,
            'spellAttackBonus': 2 + (abilities['charisma'] - 10) // 2,
            'spells': {
                'cantrips': ['Blade Ward', 'Dancing Lights', 'Light', 'Vicious Mockery'],
                'level1': ['Cure Wounds', 'Detect Magic', 'Healing Word', 'Thunderwave'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 2, 'max': 2},
                'level2': {'current': 0, 'max': 0},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        },
        'Warlock': {
            'ability': 'charisma',
            'spellSaveDC': 8 + 2 + (abilities['charisma'] - 10) // 2,
            'spellAttackBonus': 2 + (abilities['charisma'] - 10) // 2,
            'spells': {
                'cantrips': ['Eldritch Blast', 'Friends', 'Mage Hand', 'Prestidigitation'],
                'level1': ['Armor of Agathys', 'Hellish Rebuke', 'Hex'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 1, 'max': 1},  # Warlock ha solo 1 slot al livello 1
                'level2': {'current': 0, 'max': 0},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        },
        'Paladin': {
            'ability': 'charisma',
            'spellSaveDC': 8 + 2 + (abilities['charisma'] - 10) // 2,
            'spellAttackBonus': 2 + (abilities['charisma'] - 10) // 2,
            'spells': {
                'cantrips': [],
                'level1': ['Bless', 'Cure Wounds', 'Detect Magic', 'Divine Favor'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 0, 'max': 0},  # Paladin non ha slot al livello 1
                'level2': {'current': 2, 'max': 2},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        },
        'Ranger': {
            'ability': 'wisdom',
            'spellSaveDC': 8 + 2 + (abilities['wisdom'] - 10) // 2,
            'spellAttackBonus': 2 + (abilities['wisdom'] - 10) // 2,
            'spells': {
                'cantrips': [],
                'level1': ['Cure Wounds', 'Detect Magic', 'Goodberry', 'Hunter\'s Mark'],
                'level2': [],
                'level3': [],
                'level4': [],
                'level5': [],
                'level6': [],
                'level7': [],
                'level8': [],
                'level9': []
            },
            'spellSlots': {
                'level1': {'current': 0, 'max': 0},  # Ranger non ha slot al livello 1
                'level2': {'current': 2, 'max': 2},
                'level3': {'current': 0, 'max': 0},
                'level4': {'current': 0, 'max': 0},
                'level5': {'current': 0, 'max': 0},
                'level6': {'current': 0, 'max': 0},
                'level7': {'current': 0, 'max': 0},
                'level8': {'current': 0, 'max': 0},
                'level9': {'current': 0, 'max': 0}
            }
        }
    }
    
    return spellcasting_classes.get(character_class, None)

def get_class_saving_throws(character_class):
    """Get saving throws for a class"""
    saving_throws = {
        'Fighter': ['strength', 'constitution'],
        'Wizard': ['intelligence', 'wisdom'],
        'Rogue': ['dexterity', 'intelligence'],
        'Cleric': ['wisdom', 'charisma'],
        'Ranger': ['strength', 'dexterity'],
        'Barbarian': ['strength', 'constitution'],
        'Bard': ['dexterity', 'charisma'],
        'Paladin': ['wisdom', 'charisma'],
        'Warlock': ['wisdom', 'charisma'],
        'Sorcerer': ['constitution', 'charisma']
    }
    return saving_throws.get(character_class, ['strength', 'dexterity'])

def get_class_skills(character_class, background):
    """Get skills for a class and background"""
    # Skills semplificati per ora
    return {
        'athletics': 2,
        'perception': 2
    }

def get_class_proficiencies(character_class):
    """Get proficiencies for a class"""
    proficiencies = {
        'Fighter': {
            'armor': ['Light', 'Medium', 'Heavy', 'Shields'],
            'weapons': ['Simple', 'Martial'],
            'tools': []
        },
        'Wizard': {
            'armor': [],
            'weapons': ['Daggers', 'Quarterstaffs'],
            'tools': []
        },
        'Rogue': {
            'armor': ['Light'],
            'weapons': ['Simple', 'Hand Crossbows', 'Longswords', 'Rapiers', 'Shortswords'],
            'tools': ['Thieves\' Tools']
        }
    }
    return proficiencies.get(character_class, {
        'armor': ['Light'],
        'weapons': ['Simple'],
        'tools': []
    })

def get_class_features(character_class):
    """Get class features for a class"""
    features = {
        'Fighter': [{
            "name": "Second Wind",
            "description": "Once per short rest, regain 1d10 + fighter level HP as a bonus action",
            "source": "Fighter feature"
        }],
        'Wizard': [{
            "name": "Spellcasting",
            "description": "You can cast wizard spells",
            "source": "Wizard feature"
        }],
        'Rogue': [{
            "name": "Sneak Attack",
            "description": "Deal extra 1d6 damage when you have advantage or an ally is within 5 feet of target",
            "source": "Rogue feature"
        }]
    }
    return features.get(character_class, [])

def get_starting_equipment_old(character_class, background):  # Deprecated - use character_creation_template.py instead
    """Get starting equipment for a class and background"""
    equipment = {
        'weapons': [],
        'armor': [],
        'items': [
            {
                "name": "Backpack",
                "type": "container",
                "description": "Contains adventuring gear"
            },
            {
                "name": "Bedroll",
                "type": "item",
                "description": "For sleeping outdoors"
            },
            {
                "name": "Rations (5 days)",
                "type": "consumable",
                "description": "Food and water for survival"
            }
        ],
        'money': {
            'copper': 0,
            'silver': 0,
            'electrum': 0,
            'gold': 10,
            'platinum': 0
        }
    }
    
    # Aggiungi equipaggiamento specifico per classe
    if character_class == 'Fighter':
        equipment['weapons'].append({
            "name": "Longsword",
            "type": "weapon",
            "damage": "1d8",
            "damageType": "slashing",
            "properties": ["versatile"],
            "versatileDamage": "1d10"
        })
        equipment['armor'].append({
            "name": "Chain Mail",
            "type": "armor",
            "armorClass": 16,
            "armorType": "Heavy"
        })
    
    return equipment

# ============================================================================
# COMBAT MANAGEMENT FUNCTIONS
# ============================================================================

def handle_combat_action_logic(player_name, action_text):
    """
    Handle combat action logic using CombatService.
    This function processes player actions during combat.
    """
    global GAME_STATE
    
    if not GAME_STATE["is_in_combat"] or not GAME_STATE["active_combat"]:
        debug("COMBAT: No active combat session", category="combat_events")
        return
    
    try:
        # Process the player's combat action
        result = GAME_STATE["active_combat"].process_player_turn(player_name, action_text)
        
        if "error" in result:
            # Broadcast error to all players
            broadcast_full_game_state(message_type="error", message_content=result["error"])
            return
        
        # Broadcast combat state update to all players
        socketio.emit('combat_state_update', result)
        
        # Check if combat has ended
        if not result.get("is_active", True):
            GAME_STATE["is_in_combat"] = False
            GAME_STATE["active_combat"] = None
            GAME_STATE["combat_players"] = []
            
            # Broadcast combat end
            socketio.emit('combat_ended', {
                'message': 'Combat has ended.',
                'final_state': result
            })
            
            # Return to normal game state
            broadcast_full_game_state(message_type="dm", message_content="Combat has ended. What would you like to do next?")
            return
        
        # If it's not a player's turn, process AI turns
        current_turn = result.get("current_turn")
        if current_turn and not is_player_turn(current_turn):
            # Process AI turns in background
            socketio.start_background_task(target=process_ai_combat_turns)
        
    except Exception as e:
        error(f"COMBAT: Error processing combat action", exception=e, category="combat_events")
        broadcast_full_game_state(message_type="error", message_content="Error processing combat action. Please try again.")

def process_ai_combat_turns():
    """
    Process AI turns in combat.
    This function runs in a background thread to handle AI actions.
    """
    global GAME_STATE
    
    if not GAME_STATE["is_in_combat"] or not GAME_STATE["active_combat"]:
        return
    
    try:
        # Process AI turns
        result = GAME_STATE["active_combat"].process_ai_turns()
        
        if "error" in result:
            broadcast_full_game_state(message_type="error", message_content=result["error"])
            return
        
        # Broadcast updated combat state
        socketio.emit('combat_state_update', result)
        
        # Check if combat has ended
        if not result.get("is_active", True):
            GAME_STATE["is_in_combat"] = False
            GAME_STATE["active_combat"] = None
            GAME_STATE["combat_players"] = []
            
            # Broadcast combat end
            socketio.emit('combat_ended', {
                'message': 'Combat has ended.',
                'final_state': result
            })
            
            # Return to normal game state
            broadcast_full_game_state(message_type="dm", message_content="Combat has ended. What would you like to do next?")
        
    except Exception as e:
        error(f"COMBAT: Error processing AI turns", exception=e, category="combat_events")
        broadcast_full_game_state(message_type="error", message_content="Error processing AI turns.")

def start_combat_session(encounter_id):
    """
    Start a new combat session using CombatService.
    This function is called when an encounter is triggered.
    """
    global GAME_STATE
    
    try:
        # Get current location data
        current_location_id = GAME_STATE["party_tracker"]["worldConditions"]["currentLocationId"]
        location_data = location_manager.get_location_info(
            GAME_STATE["party_tracker"]["worldConditions"]["currentLocation"],
            GAME_STATE["party_tracker"]["worldConditions"]["currentArea"],
            current_location_id
        )
        
        if not location_data:
            error(f"COMBAT: Failed to get location data for encounter {encounter_id}", category="combat_events")
            return False
        
        # Create CombatService instance
        combat_service = CombatService(encounter_id, GAME_STATE["party_tracker"], location_data)
        
        if not combat_service.is_active:
            error(f"COMBAT: Failed to initialize combat service for encounter {encounter_id}", category="combat_events")
            return False
        
        # Set combat state
        GAME_STATE["active_combat"] = combat_service
        GAME_STATE["is_in_combat"] = True
        GAME_STATE["combat_players"] = list(GAME_STATE["connected_players"].values())
        
        # Get initial combat state
        initial_state = combat_service.get_current_combat_state()
        
        # Broadcast combat start to all players
        socketio.emit('combat_started', {
            'message': 'Combat has begun!',
            'combat_state': initial_state
        })
        
        debug(f"COMBAT: Started combat session for encounter {encounter_id}", category="combat_events")
        return True
        
    except Exception as e:
        error(f"COMBAT: Error starting combat session", exception=e, category="combat_events")
        return False

def is_player_turn(character_name):
    """
    Check if the current turn belongs to a player character.
    """
    # Check if the character name matches any connected player
    connected_players = list(GAME_STATE["connected_players"].values())
    return character_name in connected_players

def start_server():
    """Start the multiplayer server"""
    print("="*60)
    print("NeverEndingQuest Multiplayer Server")
    print("="*60)
    print(f"Maximum players: {MAX_PLAYERS}")
    print(f"Turn timeout: {TURN_TIMEOUT} seconds")
    print("="*60)
    
    # Initialize game state
    if not initialize_game_state():
        print("ERROR: Failed to initialize game state. Please check your game files.")
        return False
    
    print("SUCCESS: Game state initialized")
    print("SUCCESS: Server ready for connections")
    print("="*60)
    
    return True

@socketio.on('reject_quest')
def handle_reject_quest(data):
    """Handle quest rejection request - permanently remove quest from system"""
    try:
        quest_id = data.get('quest_id')
        quest_type = data.get('quest_type', 'main')  # 'main' or 'side'

        print(f"DEBUG: Received quest rejection request - ID: {quest_id}, Type: {quest_type}")

        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")

        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"

        if os.path.exists(plot_file_path):
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)

            # Find and reject the quest
            quest_rejected = False
            for plot_point in plot_data['plotPoints']:
                if quest_type == 'main' and plot_point.get('id') == quest_id:
                    if plot_point.get('status') == 'not started':
                        # Mark as rejected and add to rejected quests list
                        plot_point['status'] = 'rejected'
                        plot_point['plotImpact'] = 'Quest rejected by player'
                        quest_rejected = True
                        print(f"INFO: Rejected main quest '{plot_point.get('title', 'Unknown')}'")
                        break
                elif quest_type == 'side' and plot_point.get('sideQuests'):
                    for side_quest in plot_point['sideQuests']:
                        if side_quest.get('id') == quest_id:
                            if side_quest.get('status') == 'not started':
                                side_quest['status'] = 'rejected'
                                side_quest['plotImpact'] = 'Side quest rejected by player'
                                quest_rejected = True
                                print(f"INFO: Rejected side quest '{side_quest.get('title', 'Unknown')}'")
                                break
                    if quest_rejected:
                        break

            if quest_rejected:
                # Save the updated plot data
                with open(plot_file_path, 'w', encoding='utf-8') as f:
                    json.dump(plot_data, f, indent=2, ensure_ascii=False)

                # Send updated plot data to client
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
                print(f"DEBUG: Quest rejection successful, sent updated data to client")
            else:
                emit('error', {'message': f'Quest {quest_id} not found or cannot be rejected'})
                print(f"DEBUG: Quest rejection failed - quest not found or cannot be rejected")
        else:
            emit('error', {'message': 'Plot data not found'})
            print(f"ERROR: Plot file not found for module {current_module}")

    except Exception as e:
        print(f"ERROR: Error rejecting quest: {e}")
        emit('error', {'message': f'Error rejecting quest: {str(e)}'})

@socketio.on('remove_quest')
def handle_remove_quest(data):
    """Handle quest removal request - completely remove quest from display"""
    try:
        quest_id = data.get('quest_id')
        quest_type = data.get('quest_type', 'main')  # 'main' or 'side'

        print(f"DEBUG: Received quest removal request - ID: {quest_id}, Type: {quest_type}")

        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")

        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"

        if os.path.exists(plot_file_path):
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)

            # Find and remove the quest
            quest_removed = False
            for plot_point in plot_data['plotPoints']:
                if quest_type == 'main' and plot_point.get('id') == quest_id:
                    if plot_point.get('status') in ['cancelled', 'rejected']:
                        # Mark as removed (hidden from display)
                        plot_point['status'] = 'removed'
                        plot_point['plotImpact'] = 'Quest removed by player'
                        quest_removed = True
                        print(f"INFO: Removed main quest '{plot_point.get('title', 'Unknown')}'")
                        break
                elif quest_type == 'side' and plot_point.get('sideQuests'):
                    for side_quest in plot_point['sideQuests']:
                        if side_quest.get('id') == quest_id:
                            if side_quest.get('status') in ['cancelled', 'rejected']:
                                side_quest['status'] = 'removed'
                                side_quest['plotImpact'] = 'Side quest removed by player'
                                quest_removed = True
                                print(f"INFO: Removed side quest '{side_quest.get('title', 'Unknown')}'")
                                break
                    if quest_removed:
                        break

            if quest_removed:
                # Save the updated plot data
                with open(plot_file_path, 'w', encoding='utf-8') as f:
                    json.dump(plot_data, f, indent=2, ensure_ascii=False)

                # Send updated plot data to client
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
                print(f"DEBUG: Quest removal successful, sent updated data to client")
            else:
                emit('error', {'message': f'Quest {quest_id} not found or cannot be removed'})
                print(f"DEBUG: Quest removal failed - quest not found or cannot be removed")
        else:
            emit('error', {'message': 'Plot data not found'})
            print(f"ERROR: Plot file not found for module {current_module}")

    except Exception as e:
        print(f"ERROR: Error removing quest: {e}")
        emit('error', {'message': f'Error removing quest: {str(e)}'})

@socketio.on('cleanup_rejected_quests')
def handle_cleanup_rejected_quests(data):
    """Handle cleanup of all rejected quests - remove them completely"""
    try:
        print(f"🧹 CLEANUP: Received cleanup rejected quests request")
        print(f"🧹 CLEANUP: Data received: {data}")

        # Get current module from party tracker
        party_tracker = GAME_STATE.get("party_tracker", {})
        current_module = party_tracker.get("current_module", "Keep_of_Doom")

        # Load plot data for current module
        plot_file_path = f"modules/{current_module}/module_plot.json"

        if os.path.exists(plot_file_path):
            with open(plot_file_path, 'r', encoding='utf-8') as f:
                plot_data = json.load(f)

            # Cleanup all rejected quests
            quests_cleaned = 0
            print(f"🧹 CLEANUP: Starting cleanup process...")
            for plot_point in plot_data['plotPoints']:
                print(f"🧹 CLEANUP: Checking quest '{plot_point.get('title', 'Unknown')}' with status '{plot_point.get('status')}'")
                # Cleanup main quest
                if plot_point.get('status') == 'rejected':
                    plot_point['status'] = 'removed'
                    plot_point['plotImpact'] = 'Quest removed during cleanup'
                    quests_cleaned += 1
                    print(f"🧹 CLEANUP: Cleaned up main quest '{plot_point.get('title', 'Unknown')}'")
                
                # Cleanup side quests
                if plot_point.get('sideQuests'):
                    for side_quest in plot_point['sideQuests']:
                        print(f"🧹 CLEANUP: Checking side quest '{side_quest.get('title', 'Unknown')}' with status '{side_quest.get('status')}'")
                        if side_quest.get('status') == 'rejected':
                            side_quest['status'] = 'removed'
                            side_quest['plotImpact'] = 'Side quest removed during cleanup'
                            quests_cleaned += 1
                            print(f"🧹 CLEANUP: Cleaned up side quest '{side_quest.get('title', 'Unknown')}'")

            if quests_cleaned > 0:
                # Save the updated plot data
                with open(plot_file_path, 'w', encoding='utf-8') as f:
                    json.dump(plot_data, f, indent=2, ensure_ascii=False)

                # Send updated plot data to client
                emit('plot_data_response', {
                    'dataType': 'quests',
                    'data': plot_data
                })
                print(f"🧹 CLEANUP: SUCCESS - Cleanup successful, removed {quests_cleaned} quests")
                emit('success', {'message': f'Successfully cleaned up {quests_cleaned} rejected quests'})
            else:
                emit('info', {'message': 'No rejected quests to clean up'})
                print(f"🧹 CLEANUP: INFO - No rejected quests found for cleanup")
        else:
            emit('error', {'message': 'Plot data not found'})
            print(f"🧹 CLEANUP: ERROR - Plot file not found for module {current_module}")

    except Exception as e:
        print(f"🧹 CLEANUP: ERROR - Error cleaning up rejected quests: {e}")
        emit('error', {'message': f'Error cleaning up rejected quests: {str(e)}'})

@socketio.on('clear_chat_history')
def handle_clear_chat_history(data=None):
    """Handle clearing of chat history - reset conversation history to empty"""
    debug_socket_event('clear_chat_history', data)
    try:
        print(f"🗑️ CHAT CLEAR: Received clear chat history request")
        print(f"🗑️ CHAT CLEAR: Data received: {data}")
        print(f"🗑️ CHAT CLEAR: DEBUG - Event received by server")

        # Clear the conversation history in memory
        GAME_STATE["conversation_history"] = []
        
        # Clear the conversation history file
        conversation_file_path = "modules/conversation_history/conversation_history.json"
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(conversation_file_path), exist_ok=True)
        
        # Write empty array to the file
        with open(conversation_file_path, 'w', encoding='utf-8') as f:
            json.dump([], f, indent=2, ensure_ascii=False)
        
        print(f"🗑️ CHAT CLEAR: SUCCESS - Chat history cleared successfully")
        
        # Broadcast the cleared state to all connected clients
        broadcast_full_game_state(
            message_type="chat_cleared",
            message_content="Chat history has been cleared",
            message_player="System"
        )
        
        emit('success', {'message': 'Chat history cleared successfully'})
        
    except Exception as e:
        print(f"🗑️ CHAT CLEAR: ERROR - Error clearing chat history: {e}")
        emit('error', {'message': f'Error clearing chat history: {str(e)}'})

@socketio.on('clear_combat_history')
def handle_clear_combat_history(data=None):
    """Handle clearing of combat history - reset combat conversation history"""
    debug_socket_event('clear_combat_history', data)
    try:
        print(f"⚔️ COMBAT CLEAR: Received clear combat history request")
        print(f"⚔️ COMBAT CLEAR: Data received: {data}")

        # Clear combat conversation history files
        combat_files = [
            "modules/conversation_history/combat_conversation_history.json",
            "modules/conversation_history/combat_validation_log.json"
        ]
        
        cleared_files = 0
        for file_path in combat_files:
            try:
                # Ensure the directory exists
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                # Write empty array to the file
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=2, ensure_ascii=False)
                
                cleared_files += 1
                print(f"⚔️ COMBAT CLEAR: Cleared file {file_path}")
                
            except Exception as file_error:
                print(f"⚔️ COMBAT CLEAR: WARNING - Could not clear {file_path}: {file_error}")
        
        print(f"⚔️ COMBAT CLEAR: SUCCESS - Cleared {cleared_files} combat history files")
        
        # Broadcast the cleared state to all connected clients
        broadcast_full_game_state(
            message_type="combat_cleared",
            message_content="Combat history has been cleared",
            message_player="System"
        )
        
        emit('success', {'message': f'Combat history cleared successfully ({cleared_files} files)'})
        
    except Exception as e:
        print(f"⚔️ COMBAT CLEAR: ERROR - Error clearing combat history: {e}")
        emit('error', {'message': f'Error clearing combat history: {str(e)}'})

@socketio.on('clear_all_history')
def handle_clear_all_history(data=None):
    """Handle clearing of all history files - comprehensive cleanup"""
    debug_socket_event('clear_all_history', data)
    try:
        print(f"🧹 FULL CLEAR: Received clear all history request")
        print(f"🧹 FULL CLEAR: Data received: {data}")

        # List of all history files to clear
        history_files = [
            "modules/conversation_history/conversation_history.json",
            "modules/conversation_history/chat_history.json",
            "modules/conversation_history/combat_conversation_history.json",
            "modules/conversation_history/combat_validation_log.json",
            "modules/conversation_history/second_model_history.json",
            "modules/conversation_history/third_model_history.json"
        ]
        
        # Additional files that might be in root directory
        root_files = [
            "summary_dump.json",
            "trimmed_summary_dump.json",
            "debug_encounter_update.json",
            "debug_initial_response.json",
            "debug_ai_response.json",
            "dialogue_summary.json"
        ]
        
        cleared_files = 0
        
        # Clear files with directories
        for file_path in history_files:
            try:
                # Ensure the directory exists
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                
                # Write empty array to the file
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=2, ensure_ascii=False)
                
                cleared_files += 1
                print(f"🧹 FULL CLEAR: Cleared file {file_path}")
                
            except Exception as file_error:
                print(f"🧹 FULL CLEAR: WARNING - Could not clear {file_path}: {file_error}")
        
        # Clear files in root directory
        for file_path in root_files:
            try:
                # Write empty array to the file (no directory creation needed for root files)
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump([], f, indent=2, ensure_ascii=False)
                
                cleared_files += 1
                print(f"🧹 FULL CLEAR: Cleared file {file_path}")
                
            except Exception as file_error:
                print(f"🧹 FULL CLEAR: WARNING - Could not clear {file_path}: {file_error}")
        
        # Clear the conversation history in memory
        GAME_STATE["conversation_history"] = []
        
        print(f"🧹 FULL CLEAR: SUCCESS - Cleared {cleared_files} history files")
        
        # Broadcast the cleared state to all connected clients
        broadcast_full_game_state(
            message_type="all_history_cleared",
            message_content="All history has been cleared",
            message_player="System"
        )
        
        emit('success', {'message': f'All history cleared successfully ({cleared_files} files)'})
        
    except Exception as e:
        print(f"🧹 FULL CLEAR: ERROR - Error clearing all history: {e}")
        emit('error', {'message': f'Error clearing all history: {str(e)}'})

# ============================================================================
# LEVEL-UP SYSTEM EVENT HANDLERS
# ============================================================================

@socketio.on('level_up_input')
def handle_level_up_input(data):
    """Handle level-up user input during interactive session"""
    debug_socket_event('level_up_input', data)
    try:
        sid = request.sid
        player_name = PLAYERS_SID_MAP.get(sid)
        
        if not player_name:
            emit('level_up_error', {'error': 'Player not found'})
            return
        
        level_up_session = LEVEL_UP_SESSIONS.get(player_name)
        if not level_up_session:
            emit('level_up_error', {'error': 'No active level-up session found'})
            return
        
        user_input = data.get('input', '').strip()
        if not user_input:
            emit('level_up_error', {'error': 'Empty input not allowed'})
            return
        
        info(f"Level-up input from {player_name}: {user_input}", category="level_up")
        
        # Process the input through the level-up session
        dm_response = level_up_session.handle_input(user_input)
        
        # Check if the session is complete
        if level_up_session.is_complete:
            if level_up_session.success:
                info(f"Level-up successful for {player_name}", category="level_up")
                
                # Add level-up summary to conversation history
                level_up_summary = level_up_session.summary
                GAME_STATE["conversation_history"].append({"role": "assistant", "content": level_up_summary})
                save_conversation_history(GAME_STATE["conversation_history"])
                
                # Send completion event
                emit('level_up_completed', {
                    'character_name': level_up_session.character_name,
                    'new_level': level_up_session.new_level,
                    'summary': level_up_summary,
                    'success': True
                })
                
                # Broadcast to other players (exclude the current player)
                socketio.emit('level_up_notification', {
                    'player_name': player_name,
                    'character_name': level_up_session.character_name,
                    'new_level': level_up_session.new_level,
                    'message': f"{level_up_session.character_name} has successfully advanced to level {level_up_session.new_level}!",
                    'completed': True
                }, skip_sid=sid)
                
                # Refresh character data
                reload_character_data_for_player(player_name)
                
            else:
                warning(f"Level-up failed for {player_name}: {level_up_session.summary}", category="level_up")
                emit('level_up_completed', {
                    'character_name': level_up_session.character_name,
                    'summary': level_up_session.summary,
                    'success': False
                })
            
            # Clean up the session
            del LEVEL_UP_SESSIONS[player_name]
            
        else:
            # Session continues, send next DM response
            emit('level_up_response', {
                'dm_response': dm_response,
                'is_complete': False
            })
    
    except Exception as e:
        error(f"Error handling level-up input: {e}", category="level_up")
        emit('level_up_error', {'error': f'Error processing level-up input: {str(e)}'})

@socketio.on('cancel_level_up')
def handle_cancel_level_up(data):
    """Handle cancellation of level-up session"""
    debug_socket_event('cancel_level_up', data)
    try:
        sid = request.sid
        player_name = PLAYERS_SID_MAP.get(sid)
        
        if not player_name:
            emit('level_up_error', {'error': 'Player not found'})
            return
        
        level_up_session = LEVEL_UP_SESSIONS.get(player_name)
        if not level_up_session:
            emit('level_up_error', {'error': 'No active level-up session found'})
            return
        
        info(f"Level-up cancelled by {player_name}", category="level_up")
        
        # Clean up the session
        del LEVEL_UP_SESSIONS[player_name]
        
        # Send cancellation event
        emit('level_up_cancelled', {
            'character_name': level_up_session.character_name,
            'message': 'Level-up session has been cancelled'
        })
        
        # Broadcast to other players
        socketio.emit('level_up_notification', {
            'player_name': player_name,
            'character_name': level_up_session.character_name,
            'message': f"{player_name} cancelled the level-up session",
            'cancelled': True
        }, broadcast=True, include_self=False)
        
    except Exception as e:
        error(f"Error cancelling level-up: {e}", category="level_up")
        emit('level_up_error', {'error': f'Error cancelling level-up: {str(e)}'})

# ============================================================================
# SAVE/LOAD SOCKET.IO EVENT HANDLERS
# ============================================================================

@socketio.on('save_game')
def handle_save_game(data):
    """Handle save game request via SocketIO"""
    debug_socket_event('save_game', data)
    try:
        sid = request.sid
        player_name = PLAYERS_SID_MAP.get(sid)
        
        if not player_name:
            emit('save_game_response', {'success': False, 'error': 'Player not found'})
            return
        
        description = data.get('description', '')
        save_mode = data.get('save_mode', 'essential')
        
        # Update save manager with current players
        current_players = list(GAME_STATE.get("character_sheets", {}).keys())
        save_manager.set_active_players(current_players)
        
        # Set host if not already set
        if not save_manager.host_player and current_players:
            save_manager.set_host_player(current_players[0])
        
        # Create save game
        success, message = save_manager.create_save_game_thread_safe(player_name, description, save_mode)
        
        if success:
            # Notify all players
            socketio.emit('save_game_created', {
                'success': True,
                'message': message,
                'saved_by': player_name,
                'timestamp': datetime.now().isoformat()
            })
            
            # Respond to the requesting player
            emit('save_game_response', {
                'success': True,
                'message': message
            })
        else:
            emit('save_game_response', {
                'success': False,
                'error': message
            })
            
    except Exception as e:
        error(f"Error handling save game: {e}", category="save_api")
        emit('save_game_response', {'success': False, 'error': f'Error saving game: {str(e)}'})

@socketio.on('load_game')
def handle_load_game(data):
    """Handle load game request via SocketIO"""
    debug_socket_event('load_game', data)
    try:
        sid = request.sid
        player_name = PLAYERS_SID_MAP.get(sid)
        
        if not player_name:
            emit('load_game_response', {'success': False, 'error': 'Player not found'})
            return
        
        save_id = data.get('save_id', '')
        
        if not save_id:
            emit('load_game_response', {'success': False, 'error': 'Save ID is required'})
            return
        
        # Load save game
        success, message = save_manager.restore_save_game_thread_safe(player_name, save_id)
        
        if success:
            # Reload game state after loading
            reload_game_state()
            
            # Notify all players
            socketio.emit('save_game_loaded', {
                'success': True,
                'message': message,
                'loaded_by': player_name,
                'save_id': save_id,
                'timestamp': datetime.now().isoformat()
            })
            
            # Respond to the requesting player
            emit('load_game_response', {
                'success': True,
                'message': message,
                'save_id': save_id
            })
        else:
            emit('load_game_response', {
                'success': False,
                'error': message
            })
            
    except Exception as e:
        error(f"Error handling load game: {e}", category="save_api")
        emit('load_game_response', {'success': False, 'error': f'Error loading game: {str(e)}'})

@socketio.on('list_saves')
def handle_list_saves(data):
    """Handle list saves request via SocketIO"""
    debug_socket_event('list_saves', data)
    try:
        sid = request.sid
        player_name = PLAYERS_SID_MAP.get(sid)
        
        if not player_name:
            emit('list_saves_response', {'success': False, 'error': 'Player not found'})
            return
        
        # Get saves with permission info
        saves, can_load = save_manager.list_save_games_with_permissions(player_name)
        
        emit('list_saves_response', {
            'success': True,
            'saves': saves,
            'can_load': can_load,
            'host_player': save_manager.host_player,
            'total_count': len(saves)
        })
        
    except Exception as e:
        error(f"Error listing saves: {e}", category="save_api")
        emit('list_saves_response', {'success': False, 'error': f'Error listing saves: {str(e)}'})

@socketio.on('auto_save')
def handle_auto_save(data):
    """Handle auto-save request"""
    debug_socket_event('auto_save', data)
    try:
        # Check if auto-save should trigger
        if save_manager.should_auto_save():
            success, message = save_manager.create_auto_save()
            
            if success:
                # Notify all players about auto-save
                socketio.emit('auto_save_completed', {
                    'success': True,
                    'message': message,
                    'timestamp': datetime.now().isoformat()
                })
            else:
                warning(f"Auto-save failed: {message}", category="save_api")
        
    except Exception as e:
        error(f"Error during auto-save: {e}", category="save_api")

def reload_character_data_for_player(player_name):
    """Reload character data for a specific player after level-up"""
    try:
        normalized_name = normalize_character_name(player_name)
        char_file = f"characters/{normalized_name}.json"
        char_data = safe_read_json(char_file)
        
        if char_data:
            GAME_STATE["character_sheets"][player_name] = char_data
            info(f"Character data reloaded for {player_name}", category="level_up")
            
            # Send updated character data to the player
            sid = None
            for s, p in PLAYERS_SID_MAP.items():
                if p == player_name:
                    sid = s
                    break
            
            if sid:
                socketio.emit('character_data_updated', char_data, room=sid)
        else:
            warning(f"Could not reload character data for {player_name}", category="level_up")
            
    except Exception as e:
        error(f"Error reloading character data for {player_name}: {e}", category="level_up")

if __name__ == '__main__':
    if start_server():
        # Start the server
        socketio.run(
            app, 
            host='0.0.0.0', 
            port=5000, 
            debug=False, 
            allow_unsafe_werkzeug=True
        )
    else:
        print("ERROR: Server startup failed")
        sys.exit(1) 