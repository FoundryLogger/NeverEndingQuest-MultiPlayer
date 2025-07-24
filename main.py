# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0
# License: See LICENSE file in the repository root
# This software is subject to the terms of the Fair Source License.

"""
NeverEndingQuest Core Engine - Game Loop Controller
Copyright (c) 2024 MoonlightByte
Licensed under Fair Source License 1.0

This software is free for non-commercial and educational use.
Commercial competing use is prohibited for 2 years from release.
See LICENSE file for full terms.
"""

# ============================================================================
# MAIN.PY - GAME LOOP CONTROLLER
# ============================================================================
#
# ARCHITECTURE ROLE: Primary Controller in MVC Pattern
#
# This is the central orchestrator of the 5th edition Dungeon Master system, implementing
# the main game loop and coordinating all subsystems. It follows the Command Pattern
# where every game interaction is processed as a discrete action.
#
# KEY RESPONSIBILITIES:
# - Game session management and main loop execution
# - Action parsing and routing to appropriate handlers
# - AI response validation with NPC codex integration
# - Conversation history management and context compression
# - Module transition processing with timeline preservation
# - Real-time user feedback and status reporting
# - DM Note generation for authoritative current game state
# - AI-powered NPC validation system coordination
#
# DM NOTE DESIGN PHILOSOPHY:
# - AUTHORITATIVE SOURCE: DM Note contains current, dynamic game state
# - REAL-TIME DATA: Always reflects most up-to-date character information
# - AI CLARITY: Single source of truth prevents conflicting information
# - DYNAMIC FOCUS: HP, spell slots, conditions, and active effects
#
# DM NOTE CONTENT STRATEGY:
# Generated content includes:
#   - Current party status (HP, level, XP, spell slots)
#   - Active location and environmental conditions
#   - Time, date, and world state information
#   - Dynamic character states (not static reference data)
#
# INFORMATION ARCHITECTURE:
# - DM NOTES: Current state, real-time data, authoritative information
# - SYSTEM MESSAGES: Static character reference (conversation_utils.py)
# - SEPARATION PRINCIPLE: Prevents AI confusion from version conflicts
#
# ARCHITECTURAL INTEGRATION:
# - Coordinates with dm_wrapper.py for AI interactions
# - Uses action_handler.py for command processing
# - Manages state through party_tracker.json updates
# - Validates responses using multiple AI models with NPC codex verification
# - Integrates with ModulePathManager for file operations
# - Provides dynamic data to conversation_utils.py for context management
# - Leverages npc_codex_generator.py for AI-powered character validation
#
# DATA FLOW:
# User Input -> Action Processing -> AI Response -> NPC Codex Validation -> State Update -> DM Note Refresh
#
# This file embodies our "AI-First Design with Human Safety Nets" principle
# by combining powerful AI capabilities with rigorous validation layers and
# clear information architecture that prevents AI confusion.
# ============================================================================

import json
import subprocess
import os
import re
import sys
import codecs
import glob
import time
from openai import OpenAI
from datetime import datetime, timedelta
from termcolor import colored

# Import encoding utilities
from utils.encoding_utils import (
    sanitize_text,
    sanitize_dict,
    safe_json_load,
    safe_json_dump,
    fix_corrupted_location_name,
    setup_utf8_console
)

# Import other necessary modules
from core.managers.combat_manager import run_combat_simulation
from updates.plot_update import update_plot
from utils.player_stats import get_player_stat
from updates.update_world_time import update_world_time
from core.ai.conversation_utils import update_conversation_history, update_character_data
from updates.update_character_info import update_character_info
from core.managers.level_up_manager import LevelUpSession # Add this line

# Import new manager modules
from core.managers import location_manager
from utils.location_path_finder import LocationGraph
from core.ai import action_handler
from core.ai.cumulative_summary import (
    generate_enhanced_adventure_summary,
    update_journal_with_summary,
    compress_conversation_history_on_transition,
    check_and_compact_missing_summaries
)
from core.managers.status_manager import (
    status_manager, status_ready, status_processing_ai, status_validating,
    status_retrying, status_transitioning_location, status_generating_summary,
    status_updating_journal, status_compressing_history, status_updating_character,
    status_updating_party, status_updating_plot, status_advancing_time, status_saving
)

# Import atomic file operations
from utils.file_operations import safe_write_json, safe_read_json
from utils.module_path_manager import ModulePathManager
from core.managers.campaign_manager import CampaignManager

# Import training data collection
# from simple_training_collector import log_complete_interaction  # DISABLED
from utils.enhanced_logger import debug, info, warning, error, set_script_name

# Set script name for logging
set_script_name(__name__)

# Import model configurations from config.py
from config import (
    OPENAI_API_KEY,
    OPENAI_ORG_ID,
    DM_MAIN_MODEL,
    DM_SUMMARIZATION_MODEL,
    DM_VALIDATION_MODEL
)

# Initialize OpenAI client with organization support
client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_ORG_ID:
    client_kwargs["organization"] = OPENAI_ORG_ID
client = OpenAI(**client_kwargs)

# Initialize location graph for path validation
location_graph = LocationGraph()
location_graph.load_module_data()

# Temperature Configuration (remains the same)
TEMPERATURE = 0.8

SOLID_GREEN = "\033[38;2;0;180;0m"  # Slightly darker solid green for player name
LIGHT_OFF_GREEN = "\033[38;2;100;180;100m"  # More muted light green for stats
GOLD = "\033[38;2;255;215;0m"  # Gold color for status messages
RESET_COLOR = "\033[0m"

json_file = "modules/conversation_history/conversation_history.json"

needs_conversation_history_update = False

# Message combination system state variables
held_response = None
awaiting_combat_resolution = False

# Status display configuration
current_status_line = None

def display_status(message):
    """Display status message above the command prompt"""
    global current_status_line
    # Clear previous status line if exists
    if current_status_line is not None:
        print(f"\r{' ' * len(current_status_line)}\r", end='', flush=True)
    # Display new status
    status_display = f"{GOLD}[{message}]{RESET_COLOR}"
    print(f"\r{status_display}", flush=True)
    current_status_line = status_display

# Set up status callback
def status_callback(message, is_processing):
    """Callback for status manager to display status updates"""
    if is_processing:
        display_status(message)
    else:
        # Clear status when ready
        global current_status_line
        if current_status_line is not None:
            print(f"\r{' ' * len(current_status_line)}\r", end='', flush=True)
            current_status_line = None

# Register the callback
status_manager.set_callback(status_callback)

# Note: Old summarization functions removed - using cumulative summary system instead

# Add this new function near the top of the file
def exit_game():
    print("Fond farewell until we meet again!")
    exit()

def check_and_inject_return_message(conversation_history, is_combat_active=False):
    """
    Checks if a 'player has returned' message needs to be injected at startup.
    
    Args:
        conversation_history: List of conversation messages
        is_combat_active: Boolean indicating if combat is currently active (prevents duplicate injection)
        
    Returns:
        Tuple of (updated_conversation_history, was_injected)
    """
    # Skip if no conversation history (first startup)
    if not conversation_history:
        debug("STATE_CHANGE: No conversation history found, skipping return message injection", category="session_management")
        return conversation_history, False
    
    # Check if there are any user messages (game has been played before)
    user_messages = [msg for msg in conversation_history if msg.get("role") == "user"]
    if not user_messages:
        debug("STATE_CHANGE: No user messages found, skipping return message injection", category="session_management")
        return conversation_history, False
    
    # Get the last message
    last_message = conversation_history[-1] if conversation_history else None
    if not last_message:
        debug("STATE_CHANGE: No last message found, skipping return message injection", category="session_management")
        return conversation_history, False
    
    # Check if last message is already a return message
    last_content = last_message.get("content", "")
    if "Resume the game, the player has returned" in last_content:
        debug("STATE_CHANGE: Return message already present, skipping injection", category="session_management")
        return conversation_history, False
    
    # Check if we're resuming from combat - if so, inject a different tracking message
    if is_combat_active:
        # Combat manager will handle its own resume message, so we just add a tracking marker
        tracking_message = {
            "role": "user",
            "content": "[SYSTEM: Combat was interrupted and is being resumed from crash]"
        }
        conversation_history.append(tracking_message)
        debug("STATE_CHANGE: Added combat resume tracking message", category="session_management")
        
        # Also add an assistant acknowledgment to mark the recovery point
        recovery_marker = {
            "role": "assistant",
            "content": "[SYSTEM: Combat recovery initiated - continuing from last known state]"
        }
        conversation_history.append(recovery_marker)
        debug("STATE_CHANGE: Added combat recovery marker", category="session_management")
        return conversation_history, True
    
    # Normal (non-combat) resume message injection
    return_message = {
        "role": "user", 
        "content": "Dungeon Master Note: Resume the game, the player has returned."
    }
    conversation_history.append(return_message)
    debug("STATE_CHANGE: Injected 'player has returned' message at startup", category="session_management")
    return conversation_history, True

def generate_arrival_narration(departure_narration, party_tracker_data, conversation_history):
    """
    Takes the departure narration and generates a seamless arrival narration.
    """
    debug("STATE_CHANGE: Generating cinematic arrival narration...", category="narrative_generation")
    
    # Get details for the new location from the (now updated) party tracker
    new_location_name = party_tracker_data["worldConditions"]["currentLocation"]
    new_area_name = party_tracker_data["worldConditions"]["currentArea"]

    # Construct the special prompt
    arrival_prompt = f"""
    You are a master storyteller. The following text describes the party's departure from one location. Your task is to write a seamless, cinematic, and immersive description of their arrival at their destination, "{new_location_name}" in the "{new_area_name}" area.

    The arrival narration should:
    1.  Feel like a direct continuation of the departure text.
    2.  Focus on sensory details (sights, sounds, smells) of the new location.
    3.  Set the mood and atmosphere of the new environment.
    4.  Incorporate the reactions or immediate impressions of the player characters and NPCs.
    5.  Do not repeat any information from the departure text. Just write the arrival part.

    DEPARTURE NARRATION (for context):
    ---
    {departure_narration}
    ---

    Now, write the arrival narration.
    """
    
    # We can also add the most recent non-system messages for better context
    recent_context = [msg for msg in conversation_history if msg.get("role") != "system"][-5:]

    narration_request_messages = [
        {"role": "system", "content": "You are a master storyteller specializing in immersive, cinematic narrations."},
        *recent_context,
        {"role": "user", "content": arrival_prompt}
    ]

    try:
        response = client.chat.completions.create(
            model=DM_MAIN_MODEL,  # Use the main model for high-quality narration
            temperature=TEMPERATURE,
            messages=narration_request_messages
        )
        arrival_text = response.choices[0].message.content.strip()
        
        # Sometimes the AI will still wrap its response in a JSON object. We need to handle that.
        try:
            parsed_json = json.loads(arrival_text)
            arrival_text = parsed_json.get("narration", arrival_text)
        except json.JSONDecodeError:
            # It's just plain text, which is what we want.
            pass

        debug("SUCCESS: Arrival narration generated successfully.", category="narrative_generation")
        return sanitize_text(arrival_text)
    except Exception as e:
        error(f"FAILURE: Failed to generate arrival narration", exception=e, category="narrative_generation")
        return f"(The journey to {new_location_name} is uneventful.)" # Fallback text


# <--- NEW FUNCTION to blend the departure and arrival narrations --->
def generate_seamless_transition_narration(departure_narration, arrival_narration):
    """
    Takes two separate narration blocks (departure and arrival) and uses an AI
    to rewrite them into a single, cohesive, and seamless narrative.
    """
    debug("STATE_CHANGE: Blending departure and arrival narrations into a seamless whole...", category="narrative_generation")

    # If either part is empty, just return the other part to avoid weird API calls.
    if not departure_narration:
        return arrival_narration
    if not arrival_narration:
        return departure_narration

    stitching_prompt = f"""
You are a master storyteller and narrative editor. The following two text blocks describe a party's departure from one place and their subsequent arrival at another. The transition between them is abrupt because they were generated separately.

Your task is to rewrite them into a single, cohesive, and cinematic narration.
- Preserve all key details, sensory information, and character actions from both parts.
- Smooth out the transition so it feels like one continuous story beat.
- Enhance the prose where possible to create a more engaging and atmospheric experience.
- Do not add new plot points or actions; your role is to refine the existing narrative flow.

DEPARTURE NARRATION:
---
{departure_narration}
---

ARRIVAL NARRATION:
---
{arrival_narration}
---

Now, provide the rewritten, seamless narration.
"""

    try:
        response = client.chat.completions.create(
            model=DM_MAIN_MODEL,  # Use the main model for high-quality writing
            temperature=TEMPERATURE,
            messages=[
                {"role": "system", "content": "You are a master storyteller and editor, skilled at weaving separate narrative fragments into a single, seamless, and immersive piece of prose."},
                {"role": "user", "content": stitching_prompt}
            ]
        )
        seamless_narration = response.choices[0].message.content.strip()
        debug("SUCCESS: Seamless narration generated successfully.", category="narrative_generation")
        return sanitize_text(seamless_narration)
    except Exception as e:
        error(f"FAILURE: Failed to generate seamless transition narration", exception=e, category="narrative_generation")
        # Fallback to simple concatenation if the API call fails
        debug("STATE_CHANGE: Falling back to simple concatenation.", category="narrative_generation")
        return f"{departure_narration}\n\n{arrival_narration}"

# Message combination system helper functions
def detect_create_encounter(parsed_data):
    """Check if the parsed response contains a createEncounter action"""
    if not isinstance(parsed_data, dict) or "actions" not in parsed_data:
        return False
    
    actions = parsed_data.get("actions", [])
    for action in actions:
        if isinstance(action, dict) and action.get("action") == "createEncounter":
            return True
    return False

def combine_messages(first_response, second_response):
    """Combine two JSON responses into a single cohesive message"""
    try:
        # Parse both responses
        first_data = json.loads(first_response)
        second_data = json.loads(second_response)
        
        # Combine narrations
        first_narration = first_data.get("narration", "")
        second_narration = second_data.get("narration", "")
        combined_narration = f"{first_narration}\n\n{second_narration}"
        
        # Combine actions
        first_actions = first_data.get("actions", [])
        second_actions = second_data.get("actions", [])
        combined_actions = first_actions + second_actions
        
        # Create combined response
        combined_data = {
            "narration": combined_narration,
            "actions": combined_actions
        }
        
        return json.dumps(combined_data, indent=2)
        
    except json.JSONDecodeError as e:
        error(f"FAILURE: Error combining messages", exception=e, category="narrative_generation")
        # Fallback: return second response if combination fails
        return second_response
    except Exception as e:
        error(f"FAILURE: Unexpected error combining messages", exception=e, category="narrative_generation")
        return second_response

def clear_message_buffer():
    """Reset the message buffering state"""
    global held_response, awaiting_combat_resolution
    held_response = None
    awaiting_combat_resolution = False

def get_npc_stat(npc_name, stat_name, time_estimate):
    debug(f"STATE_CHANGE: get_npc_stat called for {npc_name}, stat: {stat_name}", category="npc_management")
    # Load party tracker to get correct module
    party_data = load_json_file("party_tracker.json")
    module_name = party_data.get("module", "").replace(" ", "_")
    path_manager = ModulePathManager(module_name)
    npc_file = path_manager.get_character_path(npc_name)
    try:
        with open(npc_file, "r", encoding="utf-8") as file:
            npc_stats = json.load(file)
    except FileNotFoundError:
        error(f"FAILURE: {npc_file} not found. Stat retrieval failed.", category="file_operations")
        return "NPC stat not found"
    except json.JSONDecodeError:
        error(f"FAILURE: {npc_file} has an invalid JSON format. Stat retrieval failed.", category="file_operations")
        return "NPC stat not found"

    stat_value = None
    modifier_value = None

    if npc_stats:
        if stat_name.lower() in npc_stats["abilities"]:
            stat_value = npc_stats["abilities"][stat_name.lower()]
            modifier_value = (stat_value - 10) // 2

    if stat_value is not None and modifier_value is not None:
        # Update the world time based on the time estimate (in minutes)
        update_world_time(time_estimate)

        return f"NPC's {stat_name.capitalize()}: {stat_value} (Modifier: {modifier_value})"
    else:
        return "NPC stat not found"

def parse_json_safely(text):
    # First, try to parse as-is
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from code block
    json_content = extract_json_from_codeblock(text)
    try:
        return json.loads(json_content)
    except json.JSONDecodeError:
        pass

    # If all else fails, try to find any JSON-like structure
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except json.JSONDecodeError:
        pass

    # If we still can't parse it, raise an exception
    raise json.JSONDecodeError("Unable to parse JSON from the given text", text, 0)

def create_module_validation_context(party_tracker_data, path_manager):
    """Create module data context for validation system to check location/NPC references"""
    try:
        current_area_id = party_tracker_data["worldConditions"]["currentAreaId"]
        current_location_id = party_tracker_data["worldConditions"]["currentLocationId"]
        current_module = party_tracker_data.get("module", "Unknown")
        
        validation_context = f"MODULE VALIDATION DATA:\nCurrent Module: {current_module}\nCurrent Area: {current_area_id}\nCurrent Location: {current_location_id}\n\n"
        
        # Get all valid locations in current area and location-specific NPCs
        area_file = path_manager.get_area_path(current_area_id)
        current_location_npcs = []
        area_locations_with_npcs = {}
        
        try:
            with open(area_file, "r", encoding="utf-8") as file:
                area_data = json.load(file)
            
            valid_locations = []
            for location in area_data.get("locations", []):
                loc_id = location.get("locationId", "")
                loc_name = location.get("name", "")
                if loc_id and loc_name:
                    valid_locations.append(f"{loc_id} ({loc_name})")
                    
                    # Track NPCs by location
                    location_npcs = [npc.get("name") for npc in location.get("npcs", []) if npc.get("name")]
                    if location_npcs:
                        area_locations_with_npcs[loc_id] = location_npcs
                    
                    # Collect NPCs for current location
                    if loc_id == current_location_id:
                        current_location_npcs = location_npcs.copy()  # Start with location NPCs
            
            # Add party NPCs to current location (they travel with the party)
            party_npcs = party_tracker_data.get("partyNPCs", [])
            for party_npc in party_npcs:
                npc_name = party_npc.get("name", "")
                if npc_name and npc_name not in current_location_npcs:
                    current_location_npcs.append(npc_name)
            
            validation_context += f"VALID LOCATIONS in {current_area_id}:\n"
            if valid_locations:
                validation_context += "\n".join([f"- {loc}" for loc in valid_locations])
            else:
                validation_context += "- No locations found"
            validation_context += "\n\n"
                
        except (FileNotFoundError, json.JSONDecodeError):
            validation_context += f"ERROR: Could not load area data for {current_area_id}\n\n"
        
        # Get all valid NPCs from ALL module codexes
        try:
            valid_npcs = []
            
            # Import all module codexes and merge their NPCs
            modules_dir = "modules"
            if os.path.exists(modules_dir):
                for item in os.listdir(modules_dir):
                    module_path = os.path.join(modules_dir, item)
                    if (os.path.isdir(module_path) and 
                        not item.startswith('.') and 
                        item not in ['campaign_archives', 'campaign_summaries', 'conversation_history', 'encounters', 'logs', 'backups']):
                        
                        # Check if this module has a codex file
                        codex_file = os.path.join(module_path, "npc_codex.json")
                        if os.path.exists(codex_file):
                            try:
                                with open(codex_file, "r", encoding="utf-8") as f:
                                    codex = json.load(f)
                                
                                for npc_entry in codex.get("npcs", []):
                                    if isinstance(npc_entry, dict) and "name" in npc_entry:
                                        npc_name = npc_entry["name"]
                                        source = npc_entry.get("source", "unknown")
                                        valid_npcs.append(f"{npc_name} (Module: {item})")
                            except Exception as e:
                                continue
            
            # DEBUG: Print what NPCs are being passed to validator
            # print("\n" + "="*60)
            # print("DEBUG: NPC VALIDATION CONTEXT BEING CREATED")
            # print("="*60)
            # print(f"Total NPCs found across all modules: {len(valid_npcs)}")
            # if valid_npcs:
            #     print("NPCs being passed to validator:")
            #     for npc in valid_npcs:
            #         print(f"  - {npc}")
            #         if "Kira" in npc:
            #             print(f"    ^^^ FOUND KIRA: {npc}")
            # else:
            #     print("WARNING: NO NPCs found in any module codex!")
            # print("="*60)
            # print()
            
            validation_context += "VALID CHARACTERS (All Module Codexes):\n"
            if valid_npcs:
                validation_context += "\n".join([f"- {npc}" for npc in valid_npcs])
            else:
                validation_context += "- No NPCs found in module codexes"
                
        except Exception as e:
            # Fallback to original character file method if codex fails
            # print(f"DEBUG: Exception in NPC codex loading: {e}")
            # print(f"DEBUG: Exception type: {type(e)}")
            # print("DEBUG: Falling back to character files method")
            # print(f"WARNING: NPC codex failed, falling back to character files: {e}")
            character_files = glob.glob(f"{path_manager.module_dir}/characters/*.json")
            
            valid_npcs = []
            for char_file in character_files:
                try:
                    with open(char_file, "r", encoding="utf-8") as file:
                        char_data = json.load(file)
                    char_name = char_data.get("name", "")
                    char_type = char_data.get("character_type", "unknown")
                    if char_name:
                        valid_npcs.append(f"{char_name} ({char_type})")
                except (json.JSONDecodeError, KeyError):
                    continue
            
            validation_context += "VALID CHARACTERS in module:\n"
            if valid_npcs:
                validation_context += "\n".join([f"- {npc}" for npc in valid_npcs])
            else:
                validation_context += "- No character files found"
        
        # Add location-aware NPC context
        validation_context += f"\n\nLOCATION-AWARE NPC VALIDATION:\n"
        validation_context += f"Current Location: {current_location_id}\n"
        
        if current_location_npcs:
            validation_context += f"NPCs PRESENT at current location ({current_location_id}):\n"
            validation_context += "\n".join([f"- {npc}" for npc in current_location_npcs])
            validation_context += "\n\n"
        else:
            validation_context += f"NO NPCs present at current location ({current_location_id})\n\n"
        
        if area_locations_with_npcs:
            validation_context += "NPCs at OTHER locations in this area:\n"
            for loc_id, npcs in area_locations_with_npcs.items():
                if loc_id != current_location_id:  # Don't repeat current location
                    validation_context += f"  {loc_id}: {', '.join(npcs)}\n"
            validation_context += "\n"
        
        validation_context += """ENHANCED VALIDATION RULES:
1. For interactions happening AT the current location, ONLY use NPCs from the "PRESENT at current location" list
2. For references to NPCs at OTHER locations, they must exist in the "NPCs at OTHER locations" or module character lists
3. NEVER create new NPCs - all names must exist in the provided lists
4. If an NPC is referenced incorrectly, suggest the CORRECT NPC from the current location list
5. NPCs cannot be in multiple locations simultaneously - verify location consistency

CHARACTER NAME RULES FOR updateCharacterInfo:
- ALWAYS use the FULL character name exactly as it appears in the party tracker or NPC lists
- For party NPCs, use their complete name (e.g., "Scout Kira" not "kira", "Sir Aldric" not "aldric")
- For party members, use the exact name from partyMembers list
- NEVER shorten or modify character names in action parameters
- If a character has a title or descriptor, it MUST be included (e.g., "Scout Kira", "Knight Commander Marcus")

CRITICAL: If validation fails due to wrong NPC for location, provide specific correction using NPCs actually present at the current location."""
        
        return validation_context
        
    except Exception as e:
        # print(f"DEBUG: MAJOR EXCEPTION in create_module_validation_context: {e}")
        # print(f"DEBUG: Exception type: {type(e)}")
        # import traceback
        # print(f"DEBUG: Traceback: {traceback.format_exc()}")
        return f"MODULE VALIDATION DATA: Error loading module data - {str(e)}"

def validate_ai_response(primary_response, user_input, validation_prompt_text, conversation_history, party_tracker_data):
    print("DEBUG: NPC validation running...")
    status_validating()
    # Get the last two messages from the conversation history
    last_two_messages = conversation_history[-2:]

    # Ensure we have at least two messages
    while len(last_two_messages) < 2:
        last_two_messages.insert(0, {"role": "assistant", "content": "Previous context not available."})

    # Get location data from party tracker
    current_location_id = party_tracker_data["worldConditions"]["currentLocationId"]
    current_area_id = party_tracker_data["worldConditions"]["currentAreaId"]

    # Load the area data with correct module
    module_name = party_tracker_data.get("module", "").replace(" ", "_")
    path_manager = ModulePathManager(module_name)
    area_file = path_manager.get_area_path(current_area_id)
    try:
        with open(area_file, "r", encoding="utf-8") as file:
            area_data = json.load(file)
        location_data = next((loc for loc in area_data["locations"] if loc["locationId"] == current_location_id), None)
    except (FileNotFoundError, json.JSONDecodeError):
        location_data = None

    # Create the location details message
    if location_data:
        location_details = f"Location Details: {location_data['description']} {location_data.get('dmInstructions', '')}"
    else:
        location_details = "Location Details: Not available."
    
    # Check for transitionLocation action and add path validation
    if '"action": "transitionLocation"' in primary_response:
        try:
            # Extract the destination from the AI response
            destination_match = re.search(r'"newLocation":\s*"([^"]*)"', primary_response)
            if destination_match:
                destination = destination_match.group(1).strip()
                current_origin = current_location_id
                
                # Validate we have required data
                if not destination:
                    path_info = f"Path Validation ERROR: Empty destination in transitionLocation action."
                elif not current_origin:
                    path_info = f"Path Validation ERROR: Current location ID not available in party tracker."
                elif not location_graph:
                    path_info = f"Path Validation ERROR: Location graph not initialized."
                else:
                    # Validate path using location graph
                    success, path, message = location_graph.find_path(current_origin, destination)
                    
                    if success:
                        path_info = f"The party is currently at {current_origin} and desires to travel to {destination}. The path of travel is: {' -> '.join(path)}."
                    else:
                        path_info = f"The party is currently at {current_origin} and desires to travel to {destination}. WARNING: {message}"
                
                # Add path validation to location details
                location_details += f"\n\nPath Validation: {path_info}"
            else:
                # transitionLocation detected but no newLocation parameter found
                location_details += f"\n\nPath Validation ERROR: transitionLocation action detected but destination could not be extracted."
                
        except Exception as e:
            # Catch any unexpected errors in path validation
            location_details += f"\n\nPath Validation ERROR: Failed to validate path - {str(e)}"

    # Create user input context for validation
    user_input_context = f"VALIDATION CONTEXT: The user input that triggered this assistant response was: '{user_input}'"
    
    # Create module data context for location/NPC validation
    module_data_context = create_module_validation_context(party_tracker_data, path_manager)
    
    validation_conversation = [
        {"role": "system", "content": validation_prompt_text},
        {"role": "system", "content": location_details},
        {"role": "system", "content": user_input_context},
        {"role": "system", "content": module_data_context},
        last_two_messages[0],
        last_two_messages[1],
        {"role": "assistant", "content": primary_response}
    ]
    
    # DEBUG: Log what validation AI sees for createNewModule actions
    if '"action": "createNewModule"' in primary_response:
        debug("VALIDATION: *** VALIDATION DEBUG - createNewModule detected ***", category="ai_validation")
        debug(f"VALIDATION: User input that triggered this: {user_input}", category="ai_validation")
        debug("VALIDATION: Last two messages validation AI sees:", category="ai_validation")
        for i, msg in enumerate(last_two_messages):
            debug(f"VALIDATION: Message {i+1}: {msg['role']}: {msg['content'][:100]}...", category="ai_validation")
        debug("VALIDATION: *** END VALIDATION DEBUG ***", category="ai_validation")

    max_validation_retries = 3
    for attempt in range(max_validation_retries):
        validation_result = client.chat.completions.create(
            model=DM_VALIDATION_MODEL, # Use imported model name
            temperature=TEMPERATURE,
            messages=validation_conversation
        )

        validation_response = validation_result.choices[0].message.content.strip()

        try:
            validation_json = parse_json_safely(validation_response)
            is_valid = validation_json.get("valid", False)
            reason = validation_json.get("reason", "No reason provided")

            # Log only failed validations to prompt_validation.json
            if not is_valid:
                log_entry = {
                    "prompt": validation_conversation,
                    "response": validation_response,
                    "reason": reason
                }

                with open("prompt_validation.json", "a", encoding="utf-8") as log_file:
                    json.dump(log_entry, log_file)
                    log_file.write("\n")  # Add a newline for better readability

                return reason  # Return the failure reason
            else:
                debug("SUCCESS: Validation passed successfully", category="ai_validation")
                return True  # Return True for successful validation

        except json.JSONDecodeError:
            debug(f"VALIDATION: Invalid JSON from validation model (Attempt {attempt + 1}/{max_validation_retries})", category="ai_validation")
            debug(f"VALIDATION: Problematic response: {validation_response}", category="ai_validation")
            continue  # Retry the validation

    # If we've exhausted all retries and still don't have a valid JSON response
    warning("VALIDATION: Validation model consistently produced invalid JSON. Assuming primary response is valid.", category="ai_validation")
    return True

def load_validation_prompt():
    with open("prompts/validation/validation_prompt.txt", "r", encoding="utf-8") as file:
        return file.read().strip()

def load_json_file(file_path):
    """Load a JSON file, with error handling and encoding sanitization"""
    return safe_json_load(file_path)

def remove_duplicate_npcs(party_tracker_data):
    """Remove duplicate NPCs from party tracker, keeping first occurrence.
    
    Args:
        party_tracker_data: The party tracker dictionary
        
    Returns:
        tuple: (cleaned_data, changes_made) where changes_made is boolean
    """
    if not party_tracker_data or "partyNPCs" not in party_tracker_data:
        return party_tracker_data, False
    
    original_npcs = party_tracker_data["partyNPCs"]
    seen_names = set()
    unique_npcs = []
    duplicates_removed = []
    
    for npc in original_npcs:
        npc_name = npc.get("name", "")
        if npc_name not in seen_names:
            seen_names.add(npc_name)
            unique_npcs.append(npc)
        else:
            duplicates_removed.append(npc_name)
    
    if duplicates_removed:
        debug(f"STATE_CHANGE: Removing duplicate NPCs: {duplicates_removed}", category="npc_management")
        party_tracker_data["partyNPCs"] = unique_npcs
        return party_tracker_data, True
    
    return party_tracker_data, False

def process_conversation_history(history):
    debug("STATE_CHANGE: Processing conversation history", category="conversation_management")
    for message in history:
        if message["role"] == "user" and message["content"].startswith("Leveling Dungeon Master Guidance"):
            message["content"] = "DM Guidance: Proceed with leveling up the player character or the party NPC given the 5th Edition role playing game rules. Only level the player character or party NPC one level at a time to ensure no mistakes are made. If you are leveling up a party NPC then pass all changes at once using the 'updateCharacterInfo' action. If you are leveling up a player character then you must ask the player for important decisions and choices they would have control over. After the player has provided the needed information then use the 'updateCharacterInfo' to pass all changes to the players character sheet and include the experience goal for the next level. Do not update the player's information in segements."
    
    # Apply DM note truncation to clean up bloated messages
    history = truncate_dm_notes(history)
    
    debug("SUCCESS: Conversation history processing complete", category="conversation_management")
    return history

def truncate_dm_notes(conversation_history):
    for message in conversation_history:
        if message["role"] == "user" and message["content"].startswith("Dungeon Master Note:"):
            parts = message["content"].split("Player:", 1)
            if len(parts) == 2:
                date_time = re.search(r"Current date and time: ([^.]+)", parts[0])
                if date_time:
                    message["content"] = f"Dungeon Master Note: {date_time.group(0)}. Player:{parts[1]}"
    return conversation_history

def check_and_process_location_transitions(conversation_history, party_tracker_data, path_manager):
    """
    Check if there are any unprocessed location transitions in the conversation history
    and process them to create summaries and compress the history.
    """
    # Find the most recent transition that hasn't been processed yet
    last_transition_index = None
    last_transition_content = None
    
    for i in range(len(conversation_history) - 1, -1, -1):
        msg = conversation_history[i]
        if msg.get("role") == "user" and "Location transition:" in msg.get("content", ""):
            last_transition_index = i
            last_transition_content = msg.get("content", "")
            break
    
    if last_transition_index is None:
        # No transitions found
        return conversation_history
    
    # Check if this transition has already been processed (has a summary right before it)
    if last_transition_index > 0:
        prev_msg = conversation_history[last_transition_index - 1]
        if "=== LOCATION SUMMARY ===" in prev_msg.get("content", ""):
            # This transition has already been processed
            return conversation_history
    
    # Check if there's already a summary after this transition
    # If there are regular conversation messages after the transition, we should process it
    has_conversation_after = False
    for i in range(last_transition_index + 1, len(conversation_history)):
        msg = conversation_history[i]
        # Skip system messages and DM notes
        if msg.get("role") == "assistant" or (msg.get("role") == "user" and "Dungeon Master Note:" not in msg.get("content", "")):
            has_conversation_after = True
            break
    
    if not has_conversation_after:
        # No conversation after the transition yet, wait for next round
        return conversation_history
    
    # Extract the leaving location from the transition message
    # New format: "Location transition: [from_location] (ID) to [to_location] (ID)"
    # Old format: "Location transition: [from_location] to [to_location]"
    try:
        import re
        # Try to extract with IDs first (new format)
        id_pattern = r'Location transition: (.+?) \(([A-Z]\d+)\) to (.+?) \(([A-Z]\d+)\)'
        id_match = re.match(id_pattern, last_transition_content)
        
        if id_match:
            # New format with IDs
            leaving_location_name = id_match.group(1)
            leaving_location_id = id_match.group(2)
            debug(f"STATE_CHANGE: Extracted from new format - Location: {leaving_location_name}, ID: {leaving_location_id}", category="location_transitions")
        else:
            # Fall back to old format
            parts = last_transition_content.split(" to ")
            if len(parts) == 2:
                from_part = parts[0].replace("Location transition: ", "").strip()
                leaving_location_name = from_part
                leaving_location_id = None
                debug(f"STATE_CHANGE: Extracted from old format - Location: {leaving_location_name}", category="location_transitions")
            else:
                warning("VALIDATION: Could not parse transition message format", category="location_transitions")
                return conversation_history
    except Exception as e:
        error(f"FAILURE: Error parsing transition message", exception=e, category="location_transitions")
        return conversation_history
    
    debug(f"STATE_CHANGE: Processing transition from {leaving_location_name}", category="location_transitions")
    
    try:
        # Generate enhanced adventure summary
        adventure_summary = generate_enhanced_adventure_summary(
            conversation_history,
            party_tracker_data,
            leaving_location_name
        )
        
        if adventure_summary:
            # Update journal with the summary
            update_journal_with_summary(
                adventure_summary,
                party_tracker_data,
                leaving_location_name
            )
            
            # Compress conversation history
            compressed_history = compress_conversation_history_on_transition(
                conversation_history,
                leaving_location_name
            )
            
            # Check if chunked compression is needed after creating the location summary
            try:
                from core.ai.chunked_compression_integration import check_and_perform_chunked_compression
                if check_and_perform_chunked_compression():
                    debug("SUCCESS: Chunked compression performed after location transition", category="conversation_management")
                    # Reload the compressed history
                    compressed_history = load_json_file(json_file) or compressed_history
            except Exception as e:
                error(f"FAILURE: Chunked compression check failed", exception=e, category="conversation_management")
            
            debug("SUCCESS: Location summary and compression completed", category="location_transitions")
            return compressed_history
        else:
            debug("STATE_CHANGE: No adventure summary generated", category="location_transitions")
            return conversation_history
            
    except Exception as e:
        error(f"FAILURE: Failed to process location transition", exception=e, category="location_transitions")
        import traceback
        traceback.print_exc()
        return conversation_history

def check_and_process_module_transitions(conversation_history, party_tracker_data):
    """
    Check if there are any unprocessed module transitions in the conversation history
    and process them to create summaries and compress the history.
    Mirrors the logic of check_and_process_location_transitions().
    """
    # Find the most recent transition that hasn't been processed yet
    last_transition_index = None
    last_transition_content = None
    
    for i in range(len(conversation_history) - 1, -1, -1):
        msg = conversation_history[i]
        if msg.get("role") == "user" and "Module transition:" in msg.get("content", ""):
            last_transition_index = i
            last_transition_content = msg.get("content", "")
            break
    
    if last_transition_index is None:
        # No module transitions found
        return conversation_history
    
    # Check if this transition has already been processed (has a summary right before it)
    if last_transition_index > 0:
        prev_msg = conversation_history[last_transition_index - 1]
        if prev_msg.get("role") == "user" and prev_msg.get("content", "").startswith("Module summary:"):
            # This transition has already been processed
            return conversation_history
    
    # Check if there's already conversation after this transition
    # If there are regular conversation messages after the transition, we should process it
    has_conversation_after = False
    for i in range(last_transition_index + 1, len(conversation_history)):
        msg = conversation_history[i]
        # Skip system messages and DM notes
        if msg.get("role") == "assistant" or (msg.get("role") == "user" and "Dungeon Master Note:" not in msg.get("content", "")):
            has_conversation_after = True
            break
    
    if not has_conversation_after:
        # No conversation after the transition yet, wait for next round
        return conversation_history
    
    # Extract the leaving module from the transition message
    # Format: "Module transition: [from_module] to [to_module]"
    try:
        import re
        pattern = r'Module transition: (.+?) to (.+?)$'
        match = re.match(pattern, last_transition_content)
        
        if match:
            leaving_module_name = match.group(1)
            arriving_module_name = match.group(2)
            debug(f"STATE_CHANGE: Extracted module transition - From: {leaving_module_name}, To: {arriving_module_name}", category="module_transitions")
        else:
            warning("VALIDATION: Could not parse module transition message format", category="module_transitions")
            return conversation_history
    except Exception as e:
        error(f"FAILURE: Error parsing module transition message", exception=e, category="module_transitions")
        return conversation_history
    
    debug(f"STATE_CHANGE: Processing module transition from {leaving_module_name}", category="module_transitions")
    
    try:
        # Generate module summary using similar logic to location summaries
        module_summary = generate_module_summary(
            conversation_history,
            party_tracker_data,
            leaving_module_name,
            last_transition_index
        )
        
        if module_summary:
            # Compress conversation history for module transition
            compressed_history = compress_conversation_history_on_module_transition(
                conversation_history,
                leaving_module_name,
                module_summary,
                last_transition_index
            )
            
            debug("SUCCESS: Module summary and compression completed", category="module_transitions")
            return compressed_history
        else:
            debug("STATE_CHANGE: No module summary generated", category="module_transitions")
            return conversation_history
            
    except Exception as e:
        error(f"FAILURE: Failed to process module transition", exception=e, category="module_transitions")
        import traceback
        traceback.print_exc()
        return conversation_history

def generate_module_summary(conversation_history, party_tracker_data, module_name, transition_index):
    """Generate a summary for a module transition"""
    
    # Condition 1: Look for previous module transition OR module summary first
    boundary_index = None
    
    for i in range(transition_index - 1, -1, -1):
        msg = conversation_history[i]
        content = msg.get("content", "")
        
        # Look for either previous module transition or existing module summary
        if (msg.get("role") == "user" and 
            ("Module transition:" in content or "Module summary:" in content)):
            boundary_index = i + 1  # Start after previous transition/summary
            debug(f"VALIDATION: CONDITION 1 - Found previous module marker at index {i}, boundary at {boundary_index}", category="conversation_management")
            break
    
    # Condition 2: If no previous module transition/summary, find last system message
    if boundary_index is None:
        for i in range(transition_index - 1, -1, -1):
            msg = conversation_history[i]
            if msg.get("role") == "system":
                boundary_index = i + 1  # Start after last system message
                debug(f"VALIDATION: CONDITION 2 - Found last system message at index {i}, boundary at {boundary_index}", category="conversation_management")
                break
        
        # Fallback if no system message found (shouldn't happen)
        if boundary_index is None:
            boundary_index = 0
            debug(f"VALIDATION: FALLBACK - No system message found, using boundary at {boundary_index}", category="conversation_management")
    
    # Extract ONLY the conversation from boundary to transition (actual gameplay)
    module_conversation = conversation_history[boundary_index:transition_index]
    debug(f"STATE_CHANGE: Extracting {len(module_conversation)} messages from index {boundary_index} to {transition_index} for summary", category="conversation_management")
    
    # Generate summary from ACTUAL conversation history, not plot files
    try:
        # Filter out system messages and technical messages from the conversation
        meaningful_messages = []
        for msg in module_conversation:
            content = msg.get("content", "")
            role = msg.get("role", "")
            
            # Skip technical messages but keep actual gameplay
            if (role in ["user", "assistant"] and 
                not content.startswith(("Location transition:", "Module transition:", 
                                      "Module summary:", "Dungeon Master Note:", "Error Note:"))):
                meaningful_messages.append(msg)
        
        debug(f"STATE_CHANGE: Found {len(meaningful_messages)} meaningful conversation messages to summarize", category="summary_building")
        
        # If we have substantial conversation, generate AI summary from actual gameplay
        if len(meaningful_messages) >= 3:
            try:
                from openai import OpenAI
                import config
                
                # Prepare conversation for summarization
                conversation_text = ""
                for msg in meaningful_messages[-20:]:  # Last 20 meaningful messages
                    role = "Player" if msg.get("role") == "user" else "DM"
                    content = msg.get("content", "")
                    conversation_text += f"{role}: {content}\n\n"
                
                # Generate summary using AI
                client = OpenAI(api_key=config.OPENAI_API_KEY)
                
                summary_prompt = f"""You are creating an adventure chronicle for a 5th edition session. Summarize this actual gameplay conversation from the {module_name} module into a compelling narrative story.

IMPORTANT: Only include events that actually happened in the conversation. Do not add events from other sources.

Focus on:
- Actual player actions and decisions made
- NPCs encountered and interactions that occurred  
- Locations visited and described
- Plot developments that happened
- Character relationships and moments

Write in an elevated fantasy prose style, like a chronicle or epic tale. Make it engaging but accurate to what actually occurred.

ACTUAL GAMEPLAY CONVERSATION:
{conversation_text}

Write a compelling chronicle of these actual events:"""

                response = client.chat.completions.create(
                    model=config.DM_SUMMARIZATION_MODEL,
                    messages=[
                        {"role": "system", "content": "You are an expert at creating beautiful adventure chronicles from 5th edition gameplay, focusing only on events that actually occurred."},
                        {"role": "user", "content": summary_prompt}
                    ],
                    temperature=0.7
                )
                
                ai_summary = response.choices[0].message.content.strip()
                formatted_summary = f"=== MODULE SUMMARY ===\n\n{module_name}:\n------------------------------\n{ai_summary}"
                debug(f"SUCCESS: Generated AI summary from actual conversation for {module_name}", category="summary_building")
                return formatted_summary
                
            except Exception as e:
                warning(f"FAILURE: Error generating AI summary from conversation, using fallback", category="summary_building")
        
        debug(f"STATE_CHANGE: Not enough meaningful conversation for AI summary ({len(meaningful_messages)} messages), using fallback", category="summary_building")
        
    except Exception as e:
        error(f"FAILURE: Error processing conversation for summary, using fallback", exception=e, category="summary_building")
    
    # Fallback to simple summary if no AI summary available
    meaningful_messages = [
        msg for msg in module_conversation 
        if msg.get("role") in ["user", "assistant"] and 
        not msg.get("content", "").startswith(("Location transition:", "Module transition:", "Module summary:"))
    ]
    
    if len(meaningful_messages) < 2:
        return f"Brief activities in {module_name}."
    elif len(meaningful_messages) <= 5:
        return f"Short adventure in {module_name} with several interactions."
    else:
        return f"Extended adventure in {module_name} with multiple significant events and discoveries."

def compress_conversation_history_on_module_transition(conversation_history, module_name, summary_text, transition_index):
    """Compress conversation history by replacing conversation segment with summary, preserving previous summaries"""
    
    # Find the boundary for compression - same logic as generate_module_summary
    boundary_index = None
    
    for i in range(transition_index - 1, -1, -1):
        msg = conversation_history[i]
        content = msg.get("content", "")
        
        # Look for either previous module transition or existing module summary
        if (msg.get("role") == "user" and 
            ("Module transition:" in content or "Module summary:" in content)):
            boundary_index = i + 1  # Start after previous transition/summary
            debug(f"VALIDATION: COMPRESSION - Found previous module marker at index {i}, boundary at {boundary_index}", category="conversation_management")
            break
    
    # If no previous module marker, find last system message
    if boundary_index is None:
        for i, msg in enumerate(conversation_history):
            if msg.get("role") == "system":
                boundary_index = i + 1  # Start after system message
                debug(f"VALIDATION: COMPRESSION - Found system message at index {i}, boundary at {boundary_index}", category="conversation_management")
                break
        
        if boundary_index is None:
            boundary_index = 0
            debug(f"VALIDATION: COMPRESSION - No system message found, using boundary at {boundary_index}", category="conversation_management")
    
    # Create summary message
    summary_message = {
        "role": "user",
        "content": f"Module summary: {summary_text}"
    }
    
    # Build compressed history: everything before boundary + summary + transition + everything after
    compressed_history = []
    
    # Keep everything before the boundary (includes system message + previous summaries)
    compressed_history.extend(conversation_history[:boundary_index])
    
    # Add the new summary for this module  
    compressed_history.append(summary_message)
    
    # Add transition marker and everything after
    compressed_history.extend(conversation_history[transition_index:])
    
    debug(f"SUCCESS: Compressed module conversation from {len(conversation_history)} to {len(compressed_history)} messages", category="conversation_management")
    debug(f"STATE_CHANGE: Preserved {boundary_index} messages before boundary, added 1 summary, kept {len(conversation_history) - transition_index} messages after transition", category="conversation_management")
    debug("STATE_CHANGE: Result structure: main system message + module summary + transition + new conversation", category="conversation_management")
    return compressed_history

def extract_json_from_codeblock(text):
    match = re.search(r'```json\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1)
    return text

def process_ai_response(response, party_tracker_data, location_data, conversation_history):
    global needs_conversation_history_update
    
    try:
        json_content = extract_json_from_codeblock(response)
        parsed_response = json.loads(json_content)
        actions = parsed_response.get("actions", [])
        
        # --- START OF FIX: Detect levelUp action before printing narration ---
        is_levelup_action = any(action.get("action") == "levelUp" for action in actions)

        if is_levelup_action:
            debug("STATE_CHANGE: levelUp action detected. Suppressing initial narration and starting session.", category="level_up")
            # Process ONLY the levelUp action from the list to start the session.
            # This assumes the first levelUp action is the one to process.
            for action in actions:
                if action.get("action") == "levelUp":
                    # Directly call the action handler for just this action
                    return action_handler.process_action(action, party_tracker_data, location_data, conversation_history)
            # Fallback in case the loop doesn't find it, though it should.
            return None
        # --- END OF FIX ---

        # --- NEW TRANSITION LOGIC ---
        is_transition = False
        departure_narration = ""
        # Check if the response contains a transition action
        for action in parsed_response.get("actions", []):
            if action.get("action") == "transitionLocation":
                is_transition = True
                departure_narration = parsed_response.get("narration", "")
                break
        
        # If it's a transition, handle it with the special two-step process
        if is_transition:
            debug("STATE_CHANGE: Transition action detected. Holding departure narration.", category="location_transitions")
            
            # Step 1: Process actions to update state (summary, party_tracker, etc.)
            actions_processed = False
            for action in parsed_response.get("actions", []):
                result = action_handler.process_action(action, party_tracker_data, location_data, conversation_history)
                actions_processed = True
                if isinstance(result, dict):
                    if result.get("needs_update"):
                        needs_conversation_history_update = True
                    # Check if we need to generate a DM response (e.g., after module creation)
                    if result.get("needs_dm_response"):
                        # Save current assistant response first
                        current_response = {"role": "assistant", "content": response}
                        conversation_history.append(current_response)
                        save_conversation_history(conversation_history)
                        
                        # Reload and generate new AI response
                        conversation_history = load_json_file("modules/conversation_history/conversation_history.json") or []
                        ai_response = get_ai_response(conversation_history)
                        return process_ai_response(ai_response, party_tracker_data, location_data, conversation_history)
                elif isinstance(result, bool) and result:
                    needs_conversation_history_update = True
            if actions_processed:
                party_tracker_data = load_json_file("party_tracker.json")
            
            # Step 2: Reload the state to get the NEW location context
            fresh_party_data = load_json_file("party_tracker.json")
            fresh_conversation_history = load_json_file(json_file) or []
            
            # Step 3: Generate the arrival narration using the new helper function
            arrival_narration = generate_arrival_narration(departure_narration, fresh_party_data, fresh_conversation_history)
            
            # <--- MODIFIED SECTION: Use the new seamless narration generator --->
            # Step 4: Blend the departure and arrival narrations into a single, cohesive story.
            full_narration = generate_seamless_transition_narration(departure_narration, arrival_narration)
            
            # Step 5: Display the final, polished narration
            print(colored("Dungeon Master:", "blue"), colored(full_narration, "blue"))
            # <--- END OF MODIFIED SECTION --->

            # Step 6: Add the final combined narration to history as a single, clean message.
            for i in range(len(conversation_history) - 1, -1, -1):
                if conversation_history[i].get("role") == "assistant":
                    conversation_history[i]['content'] = json.dumps({"narration": full_narration, "actions": []}) # Actions already processed
                    debug("SUCCESS: Replaced last assistant message with combined transition narration.", category="location_transitions")
                    break
            
            save_conversation_history(conversation_history)
            
            return {"role": "assistant", "content": json.dumps({"narration": full_narration, "actions": []})}
        
        # --- END NEW TRANSITION LOGIC ---

        # If not a transition or levelup, proceed with normal processing
        narration = parsed_response.get("narration", "")
        sanitized_narration = sanitize_text(narration)
        print(colored("Dungeon Master:", "blue"), colored(sanitized_narration, "blue"))

        actions_processed = False
        for action in actions:
            result = action_handler.process_action(action, party_tracker_data, location_data, conversation_history)
            actions_processed = True
            
            # --- SIGNAL-BASED SUB-SYSTEM CONTROL ---
            # Check for special signals from the action handler that indicate a sub-system has completed.
            if isinstance(result, dict) and result.get("status") == "needs_post_combat_narration":
                # This signal means combat finished and its summary was added to the history.
                # The action_handler has already:
                # 1. Run the entire combat encounter
                # 2. Added the [COMBAT CONCLUDED...] summary to conversation_history
                # 3. Returned this signal instead of a normal response
                
                debug("STATE_CHANGE: Combat resolved. Requesting post-combat narration from AI.", category="combat_events")
                
                # We must reload the history from disk to ensure we have the combat summary.
                # This is necessary because the action_handler modified and saved the history independently.
                post_combat_history = load_json_file(json_file) or conversation_history
                ai_response_after_combat = get_ai_response(post_combat_history)
                
                # Set flag to indicate we just finished combat (for XP display fix)
                process_ai_response._just_finished_combat = True
                
                # Process the AI's post-combat response by calling this function again (recursively).
                # This ensures the post-combat narration is handled just like any other turn,
                # maintaining consistency in how we process AI responses.
                return process_ai_response(ai_response_after_combat, party_tracker_data, location_data, post_combat_history)
            # --- END SIGNAL-BASED SUB-SYSTEM CONTROL ---
            
            if isinstance(result, dict):
                if result.get("status") == "exit": return "exit"
                if result.get("status") == "restart": return "restart"
                # This check is now crucial for the level up flow
                if result.get("status") == "enter_levelup_mode":
                    return result
                if result.get("status") == "needs_response":
                    # Combat summary was added to conversation history, get AI response
                    # CRITICAL FIX: Save the current response to conversation history before getting new response
                    current_response = {"role": "assistant", "content": response}
                    conversation_history.append(current_response)
                    save_conversation_history(conversation_history)
                    
                    # Now reload and get the new AI response
                    conversation_history = load_json_file("modules/conversation_history/conversation_history.json") or []
                    ai_response = get_ai_response(conversation_history)
                    return process_ai_response(ai_response, party_tracker_data, location_data, conversation_history)
                if result.get("needs_update"): needs_conversation_history_update = True
            elif result == "exit": return "exit"
            elif isinstance(result, bool) and result: needs_conversation_history_update = True

        if actions_processed:
            party_tracker_data = load_json_file("party_tracker.json")
            
            if hasattr(action_handler.process_action, 'level_up_summaries') and action_handler.process_action.level_up_summaries:
                debug(f"STATE_CHANGE: Injecting {len(action_handler.process_action.level_up_summaries)} level up summaries", category="level_up")
                
                combined_summary = "\n\n".join(action_handler.process_action.level_up_summaries)
                conversation_history.append({"role": "user", "content": combined_summary})
                save_conversation_history(conversation_history)
                
                action_handler.process_action.level_up_summaries = []
                
                ai_response = get_ai_response(conversation_history)
                return process_ai_response(ai_response, party_tracker_data, location_data, conversation_history)

        # STANDARD TURN COMPLETION: For a normal turn (no special signals or sub-systems),
        # we append the AI's response to history here in process_ai_response.
        # This centralizes history management - the main_game_loop no longer needs to handle it.
        # This ensures the history is saved atomically with the response processing,
        # preventing any possibility of the history and game state becoming out of sync.
        assistant_message = {"role": "assistant", "content": response}
        conversation_history.append(assistant_message)
        save_conversation_history(conversation_history)
        return assistant_message

    except json.JSONDecodeError as e:
        print(f"Error: Unable to parse AI response as JSON: {e}")
        print(f"Problematic response: {response}")
        sanitized_response = sanitize_text(response)
        print(colored("Dungeon Master:", "blue"), colored(sanitized_response, "blue"))
        # Even in error case, append to history
        assistant_message = {"role": "assistant", "content": response}
        conversation_history.append(assistant_message)
        save_conversation_history(conversation_history)
        return assistant_message


def save_conversation_history(history):
    try:
        safe_json_dump(history, json_file)
    except Exception as e:
        error(f"FAILURE: Failed to save conversation history", exception=e, category="file_operations")

def get_ai_response(conversation_history, validation_retry_count=0):
    status_processing_ai()
    
    # Import action predictor and config
    from utils.action_predictor import predict_actions_required, extract_actual_actions, log_prediction_accuracy
    from config import ENABLE_INTELLIGENT_ROUTING, DM_MINI_MODEL, DM_FULL_MODEL, MAX_VALIDATION_RETRIES
    
    # Get the last user message for action prediction
    user_input = ""
    for msg in reversed(conversation_history):
        if msg.get("role") == "user":
            user_input = msg.get("content", "")
            break
    
    # Check if module creation prompt is present in user input
    has_module_creation_prompt = "You are a master storyteller, cartographer of myth" in user_input
    
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
        print(f"DEBUG: MODEL ROUTING - Selected: {routing_info} (Prediction: {prediction['requires_actions']}, Reason: {prediction['reason']})")
    else:
        # Use full model (default behavior or validation retry)
        selected_model = DM_FULL_MODEL
        if validation_retry_count > 0:
            print(f"DEBUG: MODEL ROUTING - VALIDATION RETRY {validation_retry_count}: Using FULL MODEL")
        else:
            print(f"DEBUG: MODEL ROUTING - Intelligent routing disabled, using FULL MODEL")
    
    # Generate response with selected model
    response = client.chat.completions.create(
        model=selected_model,
        temperature=TEMPERATURE,
        messages=conversation_history
    )
    content = response.choices[0].message.content.strip()
    
    # Extract actual actions from the response for accuracy tracking (only on initial attempt)
    if validation_retry_count == 0:
        actual_actions = extract_actual_actions(content)
        # Log prediction accuracy
        log_prediction_accuracy(user_input, prediction, actual_actions)
    
    # The sanitization line that was here has been removed.
    # We now pass the raw, untouched JSON string to the next function.
    
    # Log training data - complete conversation history and AI response
    # DISABLED: Training data collection
    # try:
    #     log_complete_interaction(conversation_history, content)
    # except Exception as e:
    #     print(f"Warning: Could not log training data: {e}")
    
    return content

def ensure_main_system_prompt(conversation_history, main_system_prompt_text):
    """
    Ensure the main system prompt is first in the conversation history.
    This removes any existing instances of the main prompt and adds it at the beginning.
    """
    # Remove all existing system prompts that appear to be the main system prompt
    # We'll identify the main system prompt by checking if it starts with the first few words
    # of the actual system prompt content
    main_prompt_start = main_system_prompt_text[:50]  # First 50 characters as identifier
    
    # Filter out any system message that starts with our identifier
    filtered_history = []
    for msg in conversation_history:
        if msg["role"] == "system" and msg["content"].startswith(main_prompt_start):
            continue  # Skip this message as it's likely our main system prompt
        filtered_history.append(msg)
    
    # Always place the main system prompt at the beginning
    return [{"role": "system", "content": main_system_prompt_text}] + filtered_history

def order_conversation_messages(conversation_history, main_system_prompt_text):
    """Order conversation messages with main system prompt first, followed by other system prompts"""
    main_prompt = None
    other_system_prompts = []
    non_system_messages = []

    for msg in conversation_history:
        if msg["role"] == "system":
            if msg["content"].startswith(main_system_prompt_text[:50]):
                main_prompt = msg
            else:
                other_system_prompts.append(msg)
        else:
            non_system_messages.append(msg)

    # Reconstruct with proper order
    ordered_history = []
    if main_prompt:
        ordered_history.append(main_prompt)
    ordered_history.extend(other_system_prompts)
    ordered_history.extend(non_system_messages)
    
    return ordered_history

def check_all_modules_plot_completion():
    """
    Check plot completion status for ALL available modules, not just the current one.
    Returns a dictionary with completion data for all modules.
    """
    import os
    import glob
    
    # Comprehensive module plot completion check (verbose logging removed)
    
    modules_dir = "modules"
    all_modules_data = {
        "modules_checked": [],
        "all_complete": True,
        "completion_summary": {}
    }
    
    if not os.path.exists(modules_dir):
        warning("FILE_OP: No modules directory found", category="module_management")
        return all_modules_data
    
    # Find all valid module directories
    available_modules = []
    for item in os.listdir(modules_dir):
        module_path = os.path.join(modules_dir, item)
        if os.path.isdir(module_path) and not item.startswith('.') and item not in ['campaign_archives', 'campaign_summaries']:
            # Check if this directory has area JSON files (indicating it's a valid module)
            area_files = []
            
            # Check root directory (legacy structure)
            try:
                root_area_files = [f for f in os.listdir(module_path) 
                                 if os.path.isfile(os.path.join(module_path, f)) 
                                 and f.endswith('.json') 
                                 and len(f.split('.')[0]) <= 7  # Area codes like HH001, G001, SR001
                                 and not f.startswith('map_') 
                                 and not f.startswith('plot_')
                                 and not f.startswith('party_')
                                 and not f.startswith('module_')
                                 and f not in ['campaign.json', 'world_registry.json', 'module_context.json']]
                area_files.extend(root_area_files)
            except Exception as e:
                error(f"FAILURE: Error checking root area files for {item}", exception=e, category="module_management")
            
            # Check areas/ subdirectory (new structure)
            areas_subdir = os.path.join(module_path, 'areas')
            if os.path.exists(areas_subdir) and os.path.isdir(areas_subdir):
                try:
                    subdir_area_files = [f for f in os.listdir(areas_subdir) 
                                       if os.path.isfile(os.path.join(areas_subdir, f)) 
                                       and f.endswith('.json') 
                                       and len(f.split('.')[0]) <= 7  # Area codes
                                       and not f.startswith('map_') 
                                       and not f.startswith('plot_')
                                       and not f.startswith('party_')
                                       and not f.startswith('module_')]
                    area_files.extend(subdir_area_files)
                except Exception as e:
                    error(f"FAILURE: Error checking areas subdirectory for {item}", exception=e, category="module_management")
            
            if area_files:
                available_modules.append(item)
    
    # Found modules: {available_modules} (consolidated logging)
    
    # Check plot completion for each module
    for module_name in available_modules:
        module_path_manager = ModulePathManager(module_name)
        plot_file_path = module_path_manager.get_plot_path()
        
        # Checking plot completion for module '{module_name}' at {plot_file_path}
        
        try:
            plot_data = load_json_file(plot_file_path)
            
            if plot_data and "plotPoints" in plot_data:
                # Only count main plot points (PP), not side quests (SQ)
                main_plots = [p for p in plot_data["plotPoints"] if p.get("id", "").startswith("PP")]
                total_plots = len(main_plots)
                completed_plots = 0
                
                for plot_point in main_plots:
                    status = plot_point.get("status", "unknown")
                    plot_id = plot_point.get("id", "unknown")
                    
                    if status == "completed":
                        completed_plots += 1
                
                # Module is complete when all main plots (PP) are done, side quests (SQ) are optional
                module_complete = completed_plots == total_plots and total_plots > 0
                
                all_modules_data["completion_summary"][module_name] = {
                    "total_plots": total_plots,
                    "completed_plots": completed_plots,
                    "is_complete": module_complete,
                    "plot_file_exists": True
                }
                
                if not module_complete:
                    all_modules_data["all_complete"] = False
                
                # Module {module_name} completion: {completed_plots}/{total_plots} ({module_complete})
                
            else:
                debug(f"STATE_CHANGE: Module {module_name} has no plot data or plotPoints", category="module_management")
                all_modules_data["completion_summary"][module_name] = {
                    "total_plots": 0,
                    "completed_plots": 0,
                    "is_complete": False,
                    "plot_file_exists": False
                }
                all_modules_data["all_complete"] = False
                
        except Exception as e:
            error(f"FAILURE: Error loading plot data for module {module_name}", exception=e, category="module_management")
            all_modules_data["completion_summary"][module_name] = {
                "total_plots": 0,
                "completed_plots": 0,
                "is_complete": False,
                "plot_file_exists": False,
                "error": str(e)
            }
            all_modules_data["all_complete"] = False
    
    all_modules_data["modules_checked"] = available_modules
    
    # Module completion check: {len(available_modules)} modules, all complete: {all_modules_data['all_complete']}
    
    return all_modules_data

def main_game_loop():
    global needs_conversation_history_update

    # Check if first-time setup is needed
    try:
        from utils.startup_wizard import startup_required, run_startup_sequence
        
        if startup_required():
            print("[D20] Welcome to your 5th Edition Adventure! [D20]")
            print("It looks like this is your first time, or you need to set up a character.")
            print("Let's get you ready for adventure!\n")
            
            success = run_startup_sequence()
            if not success:
                print("[ERROR] Setup was cancelled or failed. Cannot start game loop.")
                return
    except Exception as e:
        error(f"FAILURE: Startup wizard failed", exception=e, category="startup")
        return

    # --- START: COMBAT RESUMPTION LOGIC ---
    party_tracker_data = load_json_file("party_tracker.json")
    combat_was_resumed = False  # Track if we resumed from combat
    
    if party_tracker_data and party_tracker_data["worldConditions"].get("activeCombatEncounter"):
        active_encounter_id = party_tracker_data["worldConditions"]["activeCombatEncounter"]
        print(colored(f"[SYSTEM] Active combat encounter '{active_encounter_id}' detected. Resuming combat...", "yellow"))
        combat_was_resumed = True  # Mark that we're resuming from combat
        
        # Load conversation history and inject combat resume markers BEFORE starting combat
        conversation_history = load_json_file(json_file) or []
        
        # Inject combat recovery tracking messages
        tracking_message = {
            "role": "user",
            "content": "[SYSTEM: Combat was interrupted and is being resumed from crash]"
        }
        conversation_history.append(tracking_message)
        
        recovery_marker = {
            "role": "assistant",
            "content": "[SYSTEM: Combat recovery initiated - continuing from last known state]"
        }
        conversation_history.append(recovery_marker)
        
        # Save the updated conversation history
        save_conversation_history(conversation_history)
        debug("STATE_CHANGE: Added combat resume tracking messages before combat restart", category="session_management")
        
        # Directly get location info for the combat manager
        current_area_id_resume = party_tracker_data["worldConditions"]["currentAreaId"]
        location_data_resume = location_manager.get_location_info(
            party_tracker_data["worldConditions"]["currentLocation"],
            party_tracker_data["worldConditions"]["currentArea"],
            current_area_id_resume
        )

        # Call run_combat_simulation directly to get the return values
        from core.managers.combat_manager import run_combat_simulation
        dialogue_summary, _ = run_combat_simulation(active_encounter_id, party_tracker_data, location_data_resume)

        print(colored("[SYSTEM] Combat resolved. Integrating summary and continuing adventure...", "yellow"))

        # After combat, reload everything to ensure state is fresh
        party_tracker_data = load_json_file("party_tracker.json")
        conversation_history = load_json_file(json_file) or []

        # ** CRITICAL FIX: Integrate the combat summary into the main conversation history **
        if dialogue_summary:
            # We create a clear, systemic message indicating combat is over.
            # This mimics the handoff from action_handler.
            combat_summary_message = f"[COMBAT CONCLUDED] The encounter has ended. The following is a summary of events:\n\n{dialogue_summary}"
            conversation_history.append({"role": "user", "content": combat_summary_message})
            debug("STATE_CHANGE: Appended combat summary to main history after resumed session.", category="session_management")
            save_conversation_history(conversation_history)

        # ** CRITICAL FIX: Get a new AI response for post-combat narration **
        # This makes the resumed flow behave exactly like the normal flow.
        ai_response_after_combat = get_ai_response(conversation_history)
        if ai_response_after_combat:
            # Process the AI's post-combat response to get the game moving again.
            # We need to load the fresh location data for this call.
            current_area_id_post_combat = party_tracker_data["worldConditions"]["currentAreaId"]
            location_data_post_combat = location_manager.get_location_info(
                party_tracker_data["worldConditions"]["currentLocation"],
                party_tracker_data["worldConditions"]["currentArea"],
                current_area_id_post_combat
            )
            process_ai_response(ai_response_after_combat, party_tracker_data, location_data_post_combat, conversation_history)
    # --- END: COMBAT RESUMPTION LOGIC ---

    validation_prompt_text = load_validation_prompt() 

    with open("prompts/system_prompt.txt", "r", encoding="utf-8") as file:
        main_system_prompt_text = file.read() 

    conversation_history = load_json_file(json_file) or []
    
    # CRITICAL: Check and inject return message BEFORE any processing
    # Don't inject if we already did it for combat resume
    if not combat_was_resumed:
        conversation_history, was_injected = check_and_inject_return_message(conversation_history, is_combat_active=False)
        if was_injected:
            save_conversation_history(conversation_history)
            # Generate AI response to the return message for startup narration
            debug("STATE_CHANGE: Generating startup narration after return message injection", category="startup")
            ai_response = get_ai_response(conversation_history)
            if ai_response:
                conversation_history.append({"role": "assistant", "content": ai_response})
                save_conversation_history(conversation_history)
                debug("SUCCESS: Startup narration added to conversation history", category="startup")
    
    party_tracker_data = load_json_file("party_tracker.json")
    
    # Verify party tracker loaded successfully
    if not party_tracker_data:
        print("[ERROR] Party tracker not found after setup. Something went wrong.")
        return
    
    # Extract module name from party tracker data first
    module_name = party_tracker_data.get("module", "").replace(" ", "_")
    debug(f"INITIALIZATION: Initializing path_manager with module: '{module_name}'", category="module_management")
    
    # Initialize path manager with the correct module name
    path_manager = ModulePathManager(module_name)
    debug(f"INITIALIZATION: Path manager initialized - module_name: '{path_manager.module_name}', module_dir: '{path_manager.module_dir}'", category="module_management")
    
    current_area_id = party_tracker_data["worldConditions"]["currentAreaId"]
    location_data = location_manager.get_location_info( 
        party_tracker_data["worldConditions"]["currentLocation"],
        party_tracker_data["worldConditions"]["currentArea"],
        current_area_id
    )

    # Use current module from party tracker for plot data  
    current_module_name = party_tracker_data.get("module", "").replace(" ", "_")
    current_path_manager = ModulePathManager(current_module_name)
    plot_data = load_json_file(current_path_manager.get_plot_path())
    debug(f"FILE_OP: Plot file path: {current_path_manager.get_plot_path()}", category="module_management")
    
    module_data = load_json_file(current_path_manager.get_module_file_path())

    conversation_history = ensure_main_system_prompt(conversation_history, main_system_prompt_text)
    debug(f"STATE_CHANGE: Before update_conversation_history - history has {len(conversation_history)} messages", category="conversation_management")
    conversation_history = update_conversation_history(conversation_history, party_tracker_data, plot_data, module_data)
    debug(f"STATE_CHANGE: After update_conversation_history - history has {len(conversation_history)} messages", category="conversation_management")
    conversation_history = update_character_data(conversation_history, party_tracker_data)
    
    # Use the new order_conversation_messages function
    conversation_history = order_conversation_messages(conversation_history, main_system_prompt_text)
    
    # Check for missing summaries at game startup
    debug("STATE_CHANGE: Checking for missing location summaries at startup", category="startup")
    conversation_history = check_and_compact_missing_summaries(
        conversation_history,
        party_tracker_data
    )
    
    save_conversation_history(conversation_history)

    initial_ai_response = get_ai_response(conversation_history)
    # Ensure location_data passed here is the one loaded for the initial state
    process_ai_response(initial_ai_response, party_tracker_data, location_data, conversation_history) 

    # Add safeguard against infinite loops in non-interactive environments
    empty_input_count = 0
    max_empty_inputs = 5
    
    while True:
        conversation_history = truncate_dm_notes(conversation_history)

        if needs_conversation_history_update:
            debug("STATE_CHANGE: Reloading conversation history from disk due to needs_conversation_history_update flag", category="conversation_management")
            # Reload conversation history from disk to get any changes made during actions
            conversation_history = load_json_file("modules/conversation_history/conversation_history.json") or []
            conversation_history = process_conversation_history(conversation_history)
            save_conversation_history(conversation_history)
            needs_conversation_history_update = False

        # Your essential cleanup script remains here, running every cycle.
        # Loop until all unprocessed location transitions are handled
        while True:
            original_length = len(conversation_history)
            conversation_history = check_and_process_location_transitions(conversation_history, party_tracker_data, path_manager)
            if len(conversation_history) == original_length:
                break  # No compression occurred, we're done
        save_conversation_history(conversation_history)
        
        # DISABLED: Module summary insertion now handled by inject_campaign_summaries with separate system messages
        # conversation_history = check_and_process_module_transitions(conversation_history, party_tracker_data)
        save_conversation_history(conversation_history)
        
        # Check for expired temporary effects
        try:
            from updates.process_effect_expirations import process_all_effect_expirations
            debug("EFFECTS: Checking for expired effects", category="effects_tracking")
            process_all_effect_expirations()
        except Exception as e:
            debug(f"EFFECTS: Failed to process effect expirations: {str(e)}", category="effects_tracking")
            # Don't break the game if effects processing fails


        # Set status to ready before accepting input
        status_ready()

        # Check if stdin is available (prevent infinite loops in non-interactive environments)
        if hasattr(sys.stdin, 'isatty') and not sys.stdin.isatty():
            warning("INITIALIZATION: Running in non-interactive environment. Stdin is not a terminal.", category="startup")
            print("Game loop stopped to prevent infinite empty input cycle.")
            print("To run interactively, ensure the program is run from a proper terminal.")
            break

        # --- Post-Combat State Refresh & UI Display ---
        # This is the core fix. After combat, we MUST reload all data from disk
        # to avoid using stale in-memory data from before the fight.
        if hasattr(process_ai_response, '_just_finished_combat') and process_ai_response._just_finished_combat:
            info("STATE_REFRESH: Post-combat state refresh triggered. Reloading data from disk.", category="game_loop")
            # Reload the party tracker first, as it's the source of truth.
            party_tracker_data = load_json_file("party_tracker.json")
            # Reset the flag so this only runs once per combat.
            process_ai_response._just_finished_combat = False
        
        # Now, get the player's name and load their character file for the UI.
        # This data will now be fresh if a refresh was just triggered.
        player_name_actual = party_tracker_data["partyMembers"][0]
        from updates.update_character_info import normalize_character_name
        player_name_normalized = normalize_character_name(player_name_actual)
        player_data_file = path_manager.get_character_path(player_name_normalized)
        player_data_current = load_json_file(player_data_file)
        
        # Display the prompt with the (now correct) stats.
        if player_data_current:
            current_hp = player_data_current.get("hitPoints", "N/A")
            max_hp = player_data_current.get("maxHitPoints", "N/A")
            current_xp = player_data_current.get("experience_points", "N/A")
            next_level_xp = player_data_current.get("exp_required_for_next_level", "N/A")
            current_time_str = party_tracker_data["worldConditions"]["time"]
            stats_display = f"{LIGHT_OFF_GREEN}[{current_time_str}][HP:{current_hp}/{max_hp}][XP:{current_xp}/{next_level_xp}]{RESET_COLOR}"
            player_name_display = f"{SOLID_GREEN}{player_name_actual}{RESET_COLOR}"
            user_input_text = input(f"{stats_display} {player_name_display}: ")
        else:
            user_input_text = input("User: ")

        # Skip processing if input is empty or only whitespace
        if not user_input_text or not user_input_text.strip():
            continue
        else:
            # Reset counter on valid input
            empty_input_count = 0
        
        party_tracker_data = load_json_file("party_tracker.json") 
        
        # Remove duplicate NPCs if any exist
        party_tracker_data, npcs_were_cleaned = remove_duplicate_npcs(party_tracker_data)
        if npcs_were_cleaned:
            # Save the cleaned party tracker back to file
            safe_write_json("party_tracker.json", party_tracker_data)
            debug("FILE_OP: Updated party_tracker.json with duplicate NPCs removed", category="npc_management")

        party_members_stats = []
        for member_name_iter in party_tracker_data["partyMembers"]:
            member_file_path = path_manager.get_character_path(member_name_iter)
            member_data_iter = load_json_file(member_file_path)
            if member_data_iter:
                stats = {
                    "name": member_name_iter,  # Keep original case to match file names
                    "display_name": member_name_iter.capitalize(),  # For display purposes
                    "level": member_data_iter.get("level", "N/A"),
                    "xp": member_data_iter.get("experience_points", "N/A"),
                    "hp": member_data_iter.get("hitPoints", "N/A"),
                    "max_hp": member_data_iter.get("maxHitPoints", "N/A")
                }
                party_members_stats.append(stats)

        try:
            for npc_info_iter in party_tracker_data["partyNPCs"]:
                debug(f"STATE_CHANGE: Processing NPC: {npc_info_iter['name']}", category="npc_management")
                npc_name_iter = npc_info_iter["name"]
                npc_data_file = path_manager.get_character_path(npc_name_iter)
                debug(f"FILE_OP: NPC file path: {npc_data_file}", category="npc_management")
                npc_data_iter = load_json_file(npc_data_file)
                debug(f"FILE_OP: NPC data loaded: {npc_data_iter is not None}", category="npc_management")
                if npc_data_iter:
                    stats = {
                        "name": npc_info_iter["name"],
                        "display_name": npc_info_iter["name"].capitalize(),  # For display purposes
                        "level": npc_data_iter.get("level", npc_info_iter.get("level", "N/A")),
                        "xp": npc_data_iter.get("experience_points", "N/A"),
                        "hp": npc_data_iter.get("hitPoints", "N/A"),
                        "max_hp": npc_data_iter.get("maxHitPoints", "N/A")
                    }
                    party_members_stats.append(stats)
                    debug(f"STATE_CHANGE: Added NPC stats: {stats}", category="npc_management")
        except Exception as e:
            error(f"FAILURE: Error processing NPCs", exception=e, category="npc_management")
            import traceback
            traceback.print_exc()
        
        # Reload current location_data for the DM note based on party_tracker
        # This ensures location_data is fresh for each DM note construction
        current_area_id = party_tracker_data["worldConditions"]["currentAreaId"] 
        location_data = location_manager.get_location_info( 
            party_tracker_data["worldConditions"]["currentLocation"],
            party_tracker_data["worldConditions"]["currentArea"],
            current_area_id
        )

        if party_members_stats:
            world_conditions = party_tracker_data["worldConditions"]
            date_time_str = f"{world_conditions['year']} {world_conditions['month']} {world_conditions['day']} {world_conditions['time']}"
            party_stats_formatted = []
            for stats_item in party_members_stats:
                # Check if this is a player or an NPC
                if stats_item['name'] in party_tracker_data["partyMembers"]:
                    member_data_for_note = load_json_file(path_manager.get_character_path(stats_item['name']))
                else:
                    member_data_for_note = load_json_file(path_manager.get_character_path(stats_item['name']))
                if member_data_for_note:
                    abilities = member_data_for_note.get("abilities", {})
                    ability_str = f"STR:{abilities.get('strength', 'N/A')} DEX:{abilities.get('dexterity', 'N/A')} CON:{abilities.get('constitution', 'N/A')} INT:{abilities.get('intelligence', 'N/A')} WIS:{abilities.get('wisdom', 'N/A')} CHA:{abilities.get('charisma', 'N/A')}"
                    next_level_xp_note = member_data_for_note.get("exp_required_for_next_level", "N/A")
                    display_name = stats_item.get('display_name', stats_item['name'].capitalize())
                    
                    # Extract spell slot information if character has spellcasting
                    spell_slots_str = ""
                    spellcasting = member_data_for_note.get("spellcasting", {})
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
                            spell_slots_str = f", Spell Slots: {' '.join(slot_parts)}"
                    
                    party_stats_formatted.append(f"{display_name}: Level {stats_item['level']}, XP {stats_item['xp']}/{next_level_xp_note}, HP {stats_item['hp']}/{stats_item['max_hp']}, {ability_str}{spell_slots_str}")

            party_stats_str = "; ".join(party_stats_formatted)
            current_location_name_note = world_conditions["currentLocation"]
            current_location_id_note = world_conditions["currentLocationId"]
            
            # --- CONNECTIVITY SECTION ---
            connected_locations_display_str = "None listed"
            connected_areas_display_str = "" # Initialize as empty

            if location_data: # Ensure location_data is not None
                # Get connections within the current area
                if "connectivity" in location_data and location_data["connectivity"]:
                    connected_ids_current_area = location_data["connectivity"]
                    connected_names_current_area = []
                    # Load the current area's full data to get names from IDs
                    current_area_full_data = load_json_file(path_manager.get_area_path(current_area_id))
                    if current_area_full_data and "locations" in current_area_full_data:
                        for loc_id in connected_ids_current_area:
                            found_loc = next((l["name"] for l in current_area_full_data["locations"] if l["locationId"] == loc_id), loc_id)
                            connected_names_current_area.append(found_loc)
                    if connected_names_current_area:
                         connected_locations_display_str = ", ".join(connected_names_current_area)
                
                # Get connections to other areas
                if "areaConnectivityId" in location_data and location_data["areaConnectivityId"]:
                    # Use the global location_graph to get info about connected locations
                    connected_area_details = []
                    for connected_loc_id in location_data["areaConnectivityId"]:
                        # Get the full info for the connected location
                        conn_loc_info = location_graph.get_location_info(connected_loc_id)
                        if conn_loc_info:
                            conn_loc_name = conn_loc_info['location_name']
                            conn_area_name = location_graph.get_area_name_from_location_id(connected_loc_id)
                            connected_area_details.append(f"{conn_loc_name} (in {conn_area_name})")
                    
                    if connected_area_details:
                        connected_areas_display_str = ". Connects to other areas via: " + ", ".join(connected_area_details)
            
            # --- INTER-MODULE CONNECTIVITY SECTION ---
            available_modules_str = ""
            try:
                # Load world registry to get all available modules
                world_registry_path = "modules/world_registry.json"
                world_registry = safe_read_json(world_registry_path)
                
                if world_registry and 'modules' in world_registry:
                    current_module = party_tracker_data.get('module', '').replace(' ', '_')
                    all_modules = list(world_registry['modules'].keys())
                    other_modules = [m for m in all_modules if m != current_module]
                    
                    if other_modules:
                        # Get areas from other modules
                        other_module_areas = []
                        for module_name in other_modules:
                            module_info = world_registry['modules'][module_name]
                            # Get the areas for this module from the areas section
                            module_areas = []
                            for area_id, area_info in world_registry.get('areas', {}).items():
                                if area_info.get('module') == module_name:
                                    area_name = area_info.get('areaName', area_id)
                                    module_areas.append(f"{area_name} ({area_id})")
                            
                            if module_areas:
                                level_range = module_info.get('levelRange', {})
                                level_str = f"Level {level_range.get('min', '?')}-{level_range.get('max', '?')}"
                                
                                # Get starting location for this module
                                try:
                                    start_location_id, start_location_name, start_area_id, start_area_name = action_handler.get_module_starting_location(module_name)
                                    starting_info = f" (Starting location: {start_location_name} [{start_location_id}] in {start_area_name} [{start_area_id}])"
                                except Exception as e:
                                    print(f"Warning: Could not get starting location for {module_name}: {e}")
                                    starting_info = ""
                                
                                module_description = f"{module_name} [{level_str}]: {', '.join(module_areas[:3])}{starting_info}"
                                other_module_areas.append(module_description)
                        
                        if other_module_areas:
                            available_modules_str = ". Available modules for travel: " + "; ".join(other_module_areas)
            except Exception as e:
                error(f"FAILURE: Failed to load inter-module connectivity", exception=e, category="module_management")
            # --- END OF INTER-MODULE CONNECTIVITY SECTION ---
            # --- END OF CONNECTIVITY SECTION ---
            
            # Use current module from party tracker for plot data
            current_module_for_plot = party_tracker_data.get("module", "").replace(" ", "_")
            current_plot_manager = ModulePathManager(current_module_for_plot)
            plot_data_for_note = load_json_file(current_plot_manager.get_plot_path())
            debug(f"FILE_OP: Plot file path: {current_plot_manager.get_plot_path()}", category="module_management")
            debug(f"FILE_OP: Plot data loaded: {plot_data_for_note is not None}", category="module_management")
            if plot_data_for_note:
                debug(f"FILE_OP: Plot data keys: {list(plot_data_for_note.keys())}", category="module_management")
            else:
                debug("FILE_OP: No plot data loaded - plot_data_for_note is None", category="module_management") 
            current_plot_points = []
            all_active_plot_points = []
            if plot_data_for_note and "plotPoints" in plot_data_for_note:
                # Get plot points for current location
                current_plot_points = [
                    point for point in plot_data_for_note["plotPoints"]
                    if point.get("location") == current_area_id and point["status"] != "completed"
                ]
                # Get ALL active plot points in the module
                all_active_plot_points = [
                    point for point in plot_data_for_note["plotPoints"]
                    if point["status"] != "completed"
                ]
            
            # Format plot points - show current location plots first, then other active plots
            plot_points_parts = []
            if current_plot_points:
                plot_points_parts.append("At this location:")
                plot_points_parts.extend([f"- {point['id']}: {point['title']} [{point.get('status', 'active')}]" for point in current_plot_points])
            
            # Add other active plots from different locations
            other_plots = [p for p in all_active_plot_points if p not in current_plot_points]
            if other_plots:
                if plot_points_parts:  # Add separator if we have location plots
                    plot_points_parts.append("\nActive elsewhere in module:")
                plot_points_parts.extend([f"- {point['id']}: {point['title']} [{point.get('status', 'active')}] @{point.get('location', 'Unknown')}" for point in other_plots])
            
            plot_points_str = "\n".join(plot_points_parts) if plot_points_parts else "None active"
            
            side_quests = []
            # Get ALL side quests from ALL plot points (not just current location)
            for point in plot_data_for_note.get("plotPoints", []):
                for quest in point.get("sideQuests", []):
                    if quest["status"] != "completed":
                        location_info = f" [Location: {point.get('location', 'Unknown')}]" if point.get('location') != current_area_id else ""
                        side_quests.append(f"- {quest['id']}: {quest['title']} [{quest['status']}]{location_info}")
            side_quests_str = "\n".join(side_quests) if side_quests else "None active"

            traps_str = "None listed"
            if location_data and "traps" in location_data: 
                traps = location_data.get("traps", [])
                if traps:
                    traps_str = "\n".join([
                        f"- {trap.get('name', 'Unknown Trap')}: {trap.get('description', 'No description')} (Detect DC: {trap.get('detectDC', 'N/A')}, Disable DC: {trap.get('disableDC', 'N/A')}, Trigger DC: {trap.get('triggerDC', 'N/A')}, Damage: {trap.get('damage', 'N/A')})"
                        for trap in traps
                    ])

            monsters_str = "None listed"
            if location_data and "monsters" in location_data:
                monsters = location_data.get("monsters", [])
                
                # Bulletproof check: ensure monsters is actually a list/array
                if not isinstance(monsters, (list, tuple)):
                    monsters_str = f"Invalid monster data format: {type(monsters)}"
                elif monsters:
                    monster_list = []
                    for monster in monsters:
                        # Graceful handling for different monster formats
                        if isinstance(monster, str):
                            # Handle legacy string format (just use the string)
                            monster_list.append(f"- {monster}")
                        elif isinstance(monster, dict):
                            # Handle dictionary format (multiple schema versions)
                            name = monster.get('name', 'Unknown')
                            
                            # Try different quantity field names
                            qty = None
                            qty_str = "1"
                            
                            if 'quantity' in monster:
                                # Standard schema: {"quantity": {"min": 1, "max": 1}}
                                qty = monster.get('quantity', {})
                                if isinstance(qty, dict):
                                    qty_str = f"{qty.get('min', 1)}-{qty.get('max', 1)}"
                                else:
                                    qty_str = str(qty)
                            elif 'number' in monster:
                                # Keep of Doom schema: {"number": "2d4"}
                                qty_str = str(monster.get('number', 1))
                            elif 'count' in monster:
                                # Silver Vein schema: {"count": 2}
                                qty_str = str(monster.get('count', 1))
                            
                            monster_list.append(f"- {name} ({qty_str})")
                        else:
                            # Handle unexpected types
                            monster_list.append(f"- Unknown monster type: {type(monster)}")
                    monsters_str = "\n".join(monster_list)

            # Check ALL modules for plot completion before suggesting module creation
            module_creation_prompt = ""
            should_inject_creation_prompt = False  # Initialize for later use
            try:
                # Debug current module detection
                current_module = party_tracker_data.get('module', '').replace(' ', '_')
                debug(f"STATE_CHANGE: Current module from party tracker: '{current_module}'", category="module_management")
                
                # Use new comprehensive module completion checker
                all_modules_completion = check_all_modules_plot_completion()
                
                # Extract results
                all_modules_complete = all_modules_completion["all_complete"]
                modules_checked = all_modules_completion["modules_checked"]
                completion_summary = all_modules_completion["completion_summary"]
                
                # Print summary of all modules
                debug("STATE_CHANGE: === ALL MODULES COMPLETION SUMMARY ===", category="module_management")
                print("DEBUG: [Module Manager] === MODULE COMPLETION SUMMARY ===")
                for module_name, summary in completion_summary.items():
                    status = "COMPLETE" if summary["is_complete"] else "INCOMPLETE"
                    debug(f"STATE_CHANGE: {module_name}: {summary['completed_plots']}/{summary['total_plots']} plots - {status}", category="module_management")
                    print(f"DEBUG: [Module Manager] {module_name}: {summary['completed_plots']}/{summary['total_plots']} plots - {status}")
                debug("STATE_CHANGE: === END SUMMARY ===", category="module_management")
                
                # Determine if we should inject module creation prompt
                # Only suggest module creation if ALL modules are complete
                should_inject_creation_prompt = all_modules_complete and len(modules_checked) > 0
                
                debug(f"STATE_CHANGE: All modules complete: {all_modules_complete}", category="module_management")
                debug(f"STATE_CHANGE: Should inject module creation prompt: {should_inject_creation_prompt}", category="module_management")
                print(f"DEBUG: [Module Manager] All modules complete: {all_modules_complete}")
                print(f"DEBUG: [Module Manager] Module transfer available: {should_inject_creation_prompt}")
                
                # If ALL modules are complete, inject creation prompt
                if should_inject_creation_prompt:
                    debug("STATE_CHANGE: *** MODULE CREATION PROMPT INJECTION TRIGGERED ***", category="module_management")
                    debug("STATE_CHANGE: All available modules have completed plots - suggesting new module creation", category="module_management")
                    # Load the module creation prompt
                    import os
                    if os.path.exists("prompts/generators/module_creation_prompt.txt"):
                        with open("prompts/generators/module_creation_prompt.txt", "r", encoding="utf-8") as f:
                            module_creation_prompt = "\n\n" + f.read()
                        debug(f"FILE_OP: Module creation prompt loaded ({len(module_creation_prompt)} characters)", category="module_management")
                    else:
                        warning("FILE_OP: module_creation_prompt.txt not found!", category="module_management")
                else:
                    incomplete_modules = [name for name, summary in completion_summary.items() if not summary["is_complete"]]
                    if incomplete_modules:
                        debug(f"STATE_CHANGE: Module creation prompt NOT injected - incomplete modules: {incomplete_modules}", category="module_management")
                    else:
                        debug("STATE_CHANGE: Module creation prompt NOT injected - no modules found to check", category="module_management")
                    
            except Exception as e:
                error(f"FAILURE: Module completion check failed", exception=e, category="module_management")
                import traceback
                traceback.print_exc()
            
            # Sanitize location name before using in DM note
            current_location_name_note = sanitize_text(current_location_name_note)
            
            # Build DM note - exclude plot/quest info when module creation is active
            if should_inject_creation_prompt:
                # Simplified DM note for module creation - no confusing plot/quest info
                dm_note = (f"Dungeon Master Note: Current date and time: {date_time_str}. "
                    f"Party stats: {party_stats_str}. "
                    f"Current location: {current_location_name_note} ({current_location_id_note}). "
                    f"Adjacent locations in this area: {connected_locations_display_str}{connected_areas_display_str}{available_modules_str}.\n")
            else:
                # Normal DM note with all plot/quest/monster info
                dm_note = (f"Dungeon Master Note: Current date and time: {date_time_str}. "
                    f"Party stats: {party_stats_str}. "
                    f"Current location: {current_location_name_note} ({current_location_id_note}). "
                    # --- MODIFIED LINE TO INCLUDE CONNECTIVITY ---
                    f"Adjacent locations in this area: {connected_locations_display_str}{connected_areas_display_str}{available_modules_str}.\n"
                    # --- END OF MODIFIED LINE ---
                    f"Active plot points for this location:\n{plot_points_str}\n"
                    f"Active side quests for this location:\n{side_quests_str}\n"
                    f"Monsters in this location:\n{monsters_str}\n"
                    f"Traps in this location:\n{traps_str}\n"
                    "Monsters should be active threats per engagement rules. ")
            
            # Add common instructions
            dm_note += (
                "updateCharacterInfo for player and NPC character changes (inventory, stats, abilities), "
                "updateTime for time passage, "
                "updatePlot for story progression, discovers, and new information, "
                "updatePartyNPCs for party composition changes to the party tracker, "
                "levelUp for advancement, "
                "establishHub when the party gains ownership or control of a location that could serve as a base of operations (stronghold, tavern, keep, etc.) - example: establishHub('The Silver Swan Inn', {hubType: 'tavern', description: 'Our permanent base of operations', services: ['rest', 'information'], ownership: 'party'}), "
                "exitGame for ending sessions, and "
                "transitionLocation should always be used when the player expresses a desire to move to an adjacent location to their current location, "
                "Always roleplay the NPC and NPC party rolls without asking the player. "
                "Always ask the player character to roll for skill checks and other actions. "
                "Proactively narrate location NPCs, start conversations, and weave plot elements into the adventure. "
                "Use party NPCs to narrate if possible instead of always narrating from the DM's perspective, but don't overdo it. "
                "Maintain immersive and engaging storytelling similar to an adventure novel while accurately managing game mechanics. "
                "Update all relevant information immediately and confirm with the player before major actions. "
                "Consider whether the party's action trigger traps in this location. "
                "Consider updating the plot elements on every action the player and NPCs take."
                f"{module_creation_prompt}")
        else:
            dm_note = "Dungeon Master Note: Remember to take actions if necessary such as updating the plot, time, character sheets, and location if changes occur."

        user_input_with_note = f"{dm_note} Player: {user_input_text}"
        conversation_history.append({"role": "user", "content": user_input_with_note})
        save_conversation_history(conversation_history)

        retry_count = 0
        valid_response_received = False 
        ai_response_content = None
        
        while retry_count < 5 and not valid_response_received:
            # Pass validation retry count for intelligent model escalation
            ai_response_content = get_ai_response(conversation_history, validation_retry_count=retry_count)
            validation_result = validate_ai_response(ai_response_content, user_input_text, validation_prompt_text, conversation_history, party_tracker_data)
            
            if validation_result is True:
                valid_response_received = True
                debug(f"SUCCESS: Valid response generated on attempt {retry_count + 1}", category="ai_validation")
                
                # SIMPLIFIED ARCHITECTURE: process_ai_response now handles ALL complexity internally.
                # This includes:
                # - Standard turn processing
                # - Combat encounters (via needs_post_combat_narration signal)
                # - Location transitions (with seamless narration generation)
                # - Level-up sessions (returned as enter_levelup_mode signal)
                # - All conversation history updates
                # The main loop is now just a thin orchestration layer.
                final_result = process_ai_response(ai_response_content, party_tracker_data, location_data, conversation_history)

                # After processing, we only need to check for control flow signals.
                # Everything else (including history updates) has been handled by process_ai_response.
                if final_result == "exit":
                    return
                elif final_result == "restart":
                    print("\n[SYSTEM] Restarting game with restored save...\n")
                    main_game_loop()
                    return
                elif isinstance(final_result, dict) and final_result.get("status") == "enter_levelup_mode":
                    # Enter the level up sub-loop
                    level_up_session = final_result["session"]
                    final_narration = ""

                    # Get the first message from the session
                    dm_response = level_up_session.start()
                    
                    # Display the first message and add to history
                    print(colored("Dungeon Master:", "blue"), colored(dm_response, "blue"))
                    conversation_history.append({"role": "assistant", "content": dm_response})
                    save_conversation_history(conversation_history)

                    # Loop until the session is complete
                    while not level_up_session.is_complete:
                        # Get player input
                        player_name_display = f"{SOLID_GREEN}{player_name_actual}{RESET_COLOR}"
                        level_up_input = input(f"{player_name_display} (Leveling Up): ")

                        if not level_up_input or not level_up_input.strip():
                            continue
                        
                        # Handle the input and get the next AI response from the session
                        dm_response = level_up_session.handle_input(level_up_input)

                        # Check if the response is the final JSON or a conversational step
                        try:
                            # It's the final JSON response
                            parsed_data = json.loads(dm_response)
                            final_narration = parsed_data.get("narration", "Level up complete!")
                            print(colored("Dungeon Master:", "blue"), colored(final_narration, "blue"))
                            # The session is now complete, loop will exit
                        except (json.JSONDecodeError, TypeError):
                            # It's a normal conversational response
                            print(colored("Dungeon Master:", "blue"), colored(dm_response, "blue"))

                    # After the loop, the session is complete.
                    if level_up_session.success:
                        debug("SUCCESS: Level up successful. Using final narration for context.", category="level_up")
                        # Add the final, high-quality narration to the history as the definitive AI response.
                        # This provides perfect context for the next turn without an extra AI call.
                        conversation_history.append({"role": "assistant", "content": json.dumps({"narration": final_narration, "actions": []})})
                        save_conversation_history(conversation_history)
                    else:
                        # If the level up failed, inform the player and log it.
                        print(colored("Dungeon Master:", "red"), colored(level_up_session.summary, "red"))
                        conversation_history.append({"role": "system", "content": level_up_session.summary})
                        save_conversation_history(conversation_history)

                    # Break the outer validation loop and proceed to the next turn.
                    break 

                # CRITICAL: Reload conversation history from disk.
                # Since process_ai_response handles all history updates internally (including sub-systems
                # like combat that may add multiple messages), we must reload to ensure our local
                # conversation_history variable matches the persisted state.
                # This is the ONLY place the main loop needs to manage conversation_history.
                conversation_history = load_json_file(json_file) or []
                # No need to save here, as process_ai_response already handled all persistence.

            elif isinstance(validation_result, str):
                # (The rest of your validation retry logic remains the same)
                debug(f"VALIDATION: Validation failed. Reason: {validation_result}", category="ai_validation")
                status_retrying(retry_count + 1, 5)
                conversation_history.append({"role": "user", "content": f"Error Note: Your previous response failed validation. Reason: {validation_result}. Please adjust your response accordingly."})
                retry_count += 1
            else: 
                warning(f"VALIDATION: Unexpected validation result: {validation_result}. Assuming invalid and retrying.", category="ai_validation")
                retry_count += 1
        
        if not valid_response_received:
            error("FAILURE: Failed to generate a valid response after 5 attempts. Proceeding with the last generated response.", category="ai_validation")
            if ai_response_content: 
                result = process_ai_response(ai_response_content, party_tracker_data, location_data, conversation_history) 
                if result == "exit": return
                if result == "restart":
                    print("\n[SYSTEM] Restarting game with restored save...\n")
                    main_game_loop()
                    return
            else:
                error("FAILURE: No AI response was generated after retries.", category="ai_validation")
                conversation_history.append({"role": "assistant", "content": "I seem to be having trouble formulating a response. Could you try rephrasing your action or query?"})
                save_conversation_history(conversation_history)
        
        status_ready()

        # This block now only runs if a response was NOT held
        current_area_id = party_tracker_data["worldConditions"]["currentAreaId"] 
        # Use current module from party tracker for plot data  
        module_name_updated = party_tracker_data.get("module", "").replace(" ", "_")
        updated_path_manager = ModulePathManager(module_name_updated)
        plot_data = load_json_file(updated_path_manager.get_plot_path())
        module_data = load_json_file(updated_path_manager.get_module_file_path())
        debug(f"FILE_OP: Updated plot file path: {updated_path_manager.get_plot_path()}", category="module_management")

        debug(f"STATE_CHANGE: Before AI response update_conversation_history - history has {len(conversation_history)} messages", category="conversation_management")
        conversation_history = update_conversation_history(conversation_history, party_tracker_data, plot_data, module_data)
        debug(f"STATE_CHANGE: After AI response update_conversation_history - history has {len(conversation_history)} messages", category="conversation_management")
        conversation_history = update_character_data(conversation_history, party_tracker_data)
        conversation_history = ensure_main_system_prompt(conversation_history, main_system_prompt_text)
        
        # Use the new order_conversation_messages function
        conversation_history = order_conversation_messages(conversation_history, main_system_prompt_text)
        
        save_conversation_history(conversation_history)

def main():
    """Main entry point with startup wizard integration"""
    setup_utf8_console()
    
    # Check if config.py exists, create from template if not
    import os
    import shutil
    if not os.path.exists('config.py'):
        print("[D20] Welcome to NeverEndingQuest! [D20]")
        print("\nFirst-time setup detected...")
        
        try:
            # Copy config_template.py to config.py
            shutil.copy('config_template.py', 'config.py')
            print("\n✓ Created config.py from template")
            print("\n" + "="*60)
            print("IMPORTANT: OpenAI API Key Required")
            print("="*60)
            print("\n1. Open config.py in a text editor")
            print("2. Find the line: OPENAI_API_KEY = \"your_openai_api_key_here\"")
            print("3. Replace \"your_openai_api_key_here\" with your actual OpenAI API key")
            print("4. Save the file and run the game again")
            print("\nGet your API key at: https://platform.openai.com/api-keys")
            print("\n" + "="*60)
            input("\nPress Enter to exit...")
            return
        except Exception as e:
            print(f"[ERROR] Failed to create config.py: {e}")
            print("Please manually copy config_template.py to config.py")
            input("\nPress Enter to exit...")
            return
    
    # Initialize all required directories
    required_dirs = [
        "modules/conversation_history",
        "modules/campaign_archives", 
        "modules/campaign_summaries",
        "modules/backups",
        "modules/logs",
        "save_games",
        "characters",
        "combat_logs"
    ]
    
    for dir_path in required_dirs:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
    
    # Always initialize game files from BU templates if needed
    from utils.startup_wizard import initialize_game_files_from_bu
    initialize_game_files_from_bu()
    
    # Check if first-time setup is needed
    try:
        from utils.startup_wizard import startup_required, run_startup_sequence
        
        if startup_required():
            print("[D20] Welcome to your 5th Edition Adventure! [D20]")
            print("It looks like this is your first time, or you need to set up a character.")
            print("Let's get you ready for adventure!\n")
            
            success = run_startup_sequence()
            if not success:
                print("[ERROR] Setup was cancelled or failed. Exiting...")
                return
            
            print("Setup complete! Your adventure begins now...\n")
    
    except Exception as e:
        warning(f"INITIALIZATION: Startup wizard had an issue", category="startup")
        print("Continuing with main game (assuming setup is complete)...\n")
    
    # Continue with normal game loop
    main_game_loop()

if __name__ == "__main__":
    main()