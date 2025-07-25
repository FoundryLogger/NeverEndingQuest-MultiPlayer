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

# Import configuration
try:
    from config import (
        OPENAI_API_KEY,
        OPENAI_ORG_ID,
        DM_MAIN_MODEL,
        DM_SUMMARIZATION_MODEL,
        DM_VALIDATION_MODEL
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

def ensure_main_system_prompt(conversation_history, main_system_prompt_text):
    """Ensure the main system prompt is first in the conversation history"""
    main_prompt_start = main_system_prompt_text[:50]
    
    filtered_history = []
    for msg in conversation_history:
        if msg["role"] == "system" and msg["content"].startswith(main_prompt_start):
            continue
        filtered_history.append(msg)
    
    return [{"role": "system", "content": main_system_prompt_text}] + filtered_history

def get_ai_response(conversation_history, validation_retry_count=0):
    """Get AI response with validation retry logic"""
    try:
        response = client.chat.completions.create(
            model=DM_MAIN_MODEL,
            temperature=TEMPERATURE,
            messages=conversation_history
        )
        content = response.choices[0].message.content.strip()
        return content
    except Exception as e:
        error(f"FAILURE: Failed to get AI response", exception=e, category="ai_communication")
        return None

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
    socketio.start_background_task(target=handle_player_action_logic, player_name=player_name, action_text=action_text)

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

def handle_player_action_logic(player_name, action_text):
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
    
    dm_note += f"Player ({player_name}): {action_text}"
    
    # 4. AGGIORNAMENTO CRONOLOGIA E CHIAMATA AI
    GAME_STATE["conversation_history"].append({"role": "user", "content": dm_note})
    
    # Salva la cronologia prima di chiamare l'AI
    safe_write_json("modules/conversation_history/conversation_history.json", GAME_STATE["conversation_history"])
    
    ai_response_content = get_ai_response(GAME_STATE["conversation_history"])
    if not ai_response_content:
        try:
            emit('error', {'message': 'Failed to get AI response. Please try again.'})
        except RuntimeError:
            broadcast_full_game_state(message_type="error", message_content="Failed to get AI response. Please try again.")
        return
    
    # Validazione della risposta AI
    validation_prompt_text = load_validation_prompt()
    validation_result = validate_ai_response(
        ai_response_content, 
        action_text, 
        validation_prompt_text, 
        GAME_STATE["conversation_history"], 
        GAME_STATE["party_tracker"]
    )
    
    if validation_result is not True:
        # Retry con il modello di validazione
        ai_response_content = get_ai_response(GAME_STATE["conversation_history"], validation_retry_count=1)
        if not ai_response_content:
            try:
                emit('error', {'message': 'Failed to get valid AI response. Please try again.'})
            except RuntimeError:
                broadcast_full_game_state(message_type="error", message_content="Failed to get valid AI response. Please try again.")
            return
    
    # 5. ELABORAZIONE DELLA RISPOSTA AI (IL CUORE DEL SISTEMA)
    try:
        parsed_response = json.loads(ai_response_content)
        narration = parsed_response.get("narration", "Il DM descrive la scena...")
        actions = parsed_response.get("actions", [])

        # Invia la narrazione iniziale ai giocatori
        broadcast_full_game_state(message_type="dm", message_content=narration)

        # Processa ogni azione ricevuta dall'AI
        for action in actions:
            # Chiama l'action_handler importato
            result = process_action(
                action, 
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
                    
                # Caso 3: Bisogna entrare in modalità Level-Up
                elif result.get("status") == "enter_levelup_mode":
                    print("SERVER: Inizio sessione di Level-Up.")
                    # Per ora, logghiamo solo l'inizio della sessione
                    # TODO: Implementare la gestione completa del level-up
                    # Questo richiederà un sistema di "sotto-stati" del server
                    # per gestire l'interazione one-to-one con il giocatore
                    level_up_session = result.get("session")
                    if level_up_session:
                        print(f"SERVER: Level-up session for {level_up_session.entity_name} from level {level_up_session.current_level} to {level_up_session.new_level}")
                    
                    # Invia un messaggio informativo ai giocatori
                    broadcast_full_game_state(message_type="dm", message_content=f"Iniziando sessione di level-up per {level_up_session.entity_name if level_up_session else 'unknown character'}...")
                    break
                    
        # 6. SALVATAGGIO E AGGIORNAMENTO FINALE
        # Aggiungi la risposta completa dell'AI alla cronologia
        GAME_STATE["conversation_history"].append({"role": "assistant", "content": ai_response_content})
        safe_write_json("modules/conversation_history/conversation_history.json", GAME_STATE["conversation_history"])

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
                'inventory': character_data.get('inventory', []),
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
        race = creation_data.get('race', 'Human')
        character_class = creation_data.get('class', 'Fighter')
        background = creation_data.get('background', 'Folk Hero')
        abilities = creation_data.get('abilities', {
            'strength': 10, 'dexterity': 10, 'constitution': 10,
            'intelligence': 10, 'wisdom': 10, 'charisma': 10
        })
        
        # Calcola HP base basato sulla classe
        class_hp = {
            'Fighter': 10, 'Paladin': 10, 'Ranger': 10, 'Barbarian': 12,
            'Bard': 8, 'Cleric': 8, 'Druid': 8, 'Monk': 8, 'Rogue': 8, 'Warlock': 8,
            'Sorcerer': 6, 'Wizard': 6
        }
        
        base_hp = class_hp.get(character_class, 8)
        con_mod = (abilities['constitution'] - 10) // 2
        max_hp = base_hp + con_mod
        
        # Calcola le abilità di incantesimo se la classe le ha
        spellcasting_data = get_spellcasting_data(character_class, abilities)
        
        character_data = {
            "character_role": "player",
            "character_type": "player",
            "name": player_name,
            "type": "player",
            "size": "Medium",
            "level": 1,
            "race": race,
            "class": character_class,
            "alignment": "neutral good",
            "background": background,
            "status": "alive",
            "condition": "none",
            "condition_affected": [],
            "hitPoints": max_hp,
            "maxHitPoints": max_hp,
            "armorClass": 10 + (abilities['dexterity'] - 10) // 2,
            "initiative": (abilities['dexterity'] - 10) // 2,
            "speed": 30,
            "abilities": abilities,
            "savingThrows": get_class_saving_throws(character_class),
            "skills": get_class_skills(character_class, background),
            "proficiencyBonus": 2,
            "senses": {
                "darkvision": 0,
                "passivePerception": 10 + (abilities['wisdom'] - 10) // 2
            },
            "languages": ["Common"],
            "proficiencies": get_class_proficiencies(character_class),
            "damageVulnerabilities": [],
            "damageResistances": [],
            "damageImmunities": [],
            "conditionImmunities": [],
            "experience_points": 0,
            "classFeatures": get_class_features(character_class),
            "inventory": get_starting_equipment(character_class, background),
            "personality": {
                "traits": "I stand up for what I believe in.",
                "ideals": "I fight for those who cannot fight for themselves.",
                "bonds": "I protect those who cannot protect themselves.",
                "flaws": "I have a weakness for the vices of the city."
            }
        }
        
        # Aggiungi le abilità di incantesimo se la classe le ha
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

def get_starting_equipment(character_class, background):
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