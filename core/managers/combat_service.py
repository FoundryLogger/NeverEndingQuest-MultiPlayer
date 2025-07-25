# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0
# License: See LICENSE file in the repository root
# This software is subject to the terms of the Fair Source License.

"""
NeverEndingQuest Core Engine - Combat Service
Copyright (c) 2024 MoonlightByte
Licensed under Fair Source License 1.0

This software is free for non-commercial and educational use.
Commercial competing use is prohibited for 2 years from release.
See LICENSE file for full terms.
"""

"""
Combat Service Module for NeverEndingQuest Multiplayer

This module provides a refactored version of the combat_manager.py functionality
adapted for multiplayer event-driven architecture. It removes the blocking while True
loop and input() calls, replacing them with event-based processing.

Key Features:
- Event-driven combat processing
- State management for multiplayer sessions
- AI turn processing for NPCs and monsters
- Combat state synchronization
- Real-time combat updates

Architecture:
- CombatService: Main service class managing combat state
- Event handlers for player actions and AI turns
- State synchronization with party_tracker.json
- Integration with server.py for multiplayer support
"""

import json
import os
import time
import re
import random
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from openai import OpenAI

# Import existing combat manager utilities
from core.managers.combat_manager import (
    get_combat_temperature, load_npc_with_fuzzy_match, get_current_area_id,
    get_location_data, read_prompt_from_file, load_monster_stats,
    load_json_file, save_json_file, clean_old_dm_notes, is_valid_json,
    parse_json_safely, validate_combat_response, normalize_encounter_status,
    get_initiative_order, sync_active_encounter, filter_dynamic_fields,
    filter_encounter_for_system_prompt, generate_prerolls
)

# Import other dependencies
from utils.xp import main as calculate_xp
from config import (
    OPENAI_API_KEY, COMBAT_MAIN_MODEL, DM_VALIDATION_MODEL,
    COMBAT_DIALOGUE_SUMMARY_MODEL, DM_MINI_MODEL
)
from updates.update_character_info import update_character_info, normalize_character_name
import updates.update_encounter as update_encounter
import updates.update_party_tracker as update_party_tracker
from utils.encoding_utils import safe_json_load
from utils.file_operations import safe_write_json
from utils.enhanced_logger import debug, info, warning, error, game_event, set_script_name

# Set script name for logging
set_script_name(__name__)

# OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Constants
TEMPERATURE = 0.8
HISTORY_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

class CombatService:
    """
    Event-driven combat service for multiplayer sessions.
    
    This class manages combat state and processes player/AI actions
    without blocking on input() calls. Designed for integration
    with server.py for real-time multiplayer combat.
    """
    
    def __init__(self, encounter_id: str, party_tracker_data: Dict, location_info: Dict):
        """
        Initialize the combat service for a specific encounter.
        
        Args:
            encounter_id: The encounter ID to manage
            party_tracker_data: Current party tracker data
            location_info: Location information for the encounter
        """
        self.encounter_id = encounter_id
        self.party_tracker_data = party_tracker_data
        self.location_info = location_info
        self.is_active = True
        self.current_turn_character = None
        self.conversation_history = []
        self.encounter_data = None
        self.player_info = None
        self.monster_templates = {}
        self.npc_templates = {}
        self.path_manager = None
        
        # Initialize path manager
        from utils.module_path_manager import ModulePathManager
        try:
            current_module = party_tracker_data.get("module", "").replace(" ", "_") if party_tracker_data else None
            self.path_manager = ModulePathManager(current_module)
        except:
            self.path_manager = ModulePathManager()
        
        # Initialize combat state
        self._initialize_combat_state()
    
    def _initialize_combat_state(self):
        """Initialize the combat state and load all necessary data."""
        debug(f"INITIALIZATION: Initializing combat service for encounter {self.encounter_id}", category="combat_events")
        
        # Load encounter data
        json_file_path = f"modules/encounters/encounter_{self.encounter_id}.json"
        try:
            self.encounter_data = safe_json_load(json_file_path)
            if not self.encounter_data:
                error(f"FAILURE: Failed to load encounter file {json_file_path}", category="file_operations")
                self.is_active = False
                return
            debug(f"SUCCESS: Loaded encounter with {len(self.encounter_data.get('creatures', []))} creatures", category="combat_events")
        except Exception as e:
            error(f"FAILURE: Failed to load encounter file {json_file_path}", exception=e, category="file_operations")
            self.is_active = False
            return
        
        # Load character and monster data
        self._load_creature_data()
        
        # Initialize conversation history
        self._initialize_conversation_history()
        
        # Determine first turn
        self.current_turn_character = self._determine_first_turn()
        
        debug(f"INITIALIZATION: Combat service initialized. First turn: {self.current_turn_character}", category="combat_events")
    
    def _load_creature_data(self):
        """Load all creature data (players, NPCs, monsters) for the encounter."""
        for creature in self.encounter_data["creatures"]:
            if creature["type"] == "player":
                self._load_player_data(creature)
            elif creature["type"] == "enemy":
                self._load_monster_data(creature)
            elif creature["type"] == "npc":
                self._load_npc_data(creature)
    
    def _load_player_data(self, creature: Dict):
        """Load player character data."""
        player_name = normalize_character_name(creature["name"])
        player_file = self.path_manager.get_character_path(player_name)
        try:
            self.player_info = safe_json_load(player_file)
            if not self.player_info:
                error(f"FAILURE: Failed to load player file: {player_file}", category="file_operations")
                self.is_active = False
            else:
                debug(f"SUCCESS: Loaded player {creature['name']}", category="combat_events")
        except Exception as e:
            error(f"FAILURE: Failed to load player file {player_file}", exception=e, category="file_operations")
            self.is_active = False
    
    def _load_monster_data(self, creature: Dict):
        """Load monster template data."""
        monster_type = creature["monsterType"]
        if monster_type not in self.monster_templates:
            monster_file = self.path_manager.get_monster_path(monster_type)
            try:
                monster_data = safe_json_load(monster_file)
                if monster_data:
                    self.monster_templates[monster_type] = monster_data
                    debug(f"SUCCESS: Loaded monster template {monster_type}", category="combat_events")
                else:
                    error(f"FAILURE: Failed to load monster file: {monster_file}", category="file_operations")
                    self.is_active = False
            except Exception as e:
                error(f"FAILURE: Failed to load monster file {monster_file}", exception=e, category="file_operations")
                self.is_active = False
    
    def _load_npc_data(self, creature: Dict):
        """Load NPC data using fuzzy matching."""
        npc_data, matched_filename = load_npc_with_fuzzy_match(creature["name"], self.path_manager)
        if npc_data and matched_filename:
            if matched_filename not in self.npc_templates:
                self.npc_templates[matched_filename] = npc_data
                debug(f"SUCCESS: Loaded NPC {creature['name']} as {matched_filename}", category="combat_events")
        else:
            error(f"FAILURE: Failed to load NPC file for: {creature['name']}", category="file_operations")
    
    def _initialize_conversation_history(self):
        """Initialize the conversation history for the combat session."""
        conversation_history_file = "modules/conversation_history/combat_conversation_history.json"
        
        # Check if we're resuming an existing session
        if os.path.exists(conversation_history_file) and os.path.getsize(conversation_history_file) > 100:
            self.conversation_history = load_json_file(conversation_history_file)
            debug("INITIALIZATION: Resuming existing combat session", category="combat_events")
        else:
            # Start new session
            self.conversation_history = [
                {"role": "system", "content": read_prompt_from_file('combat/combat_sim_prompt.txt')},
                {"role": "system", "content": f"Current Combat Encounter: {self.encounter_id}"},
                {"role": "system", "content": ""},  # Player data placeholder
                {"role": "system", "content": ""},  # Monster templates placeholder
                {"role": "system", "content": ""},  # Location info placeholder
            ]
            debug("INITIALIZATION: Starting new combat session", category="combat_events")
            
            # Populate system messages for new session
            self._populate_system_messages()
    
    def _populate_system_messages(self):
        """Populate system messages with character and encounter data."""
        if not self.player_info:
            return
        
        # Player character data
        self.conversation_history[2]["content"] = f"Player Character:\n{json.dumps(filter_dynamic_fields(self.player_info), indent=2)}"
        
        # Monster templates
        self.conversation_history[3]["content"] = f"Monster Templates:\n{json.dumps({k: filter_dynamic_fields(v) for k, v in self.monster_templates.items()}, indent=2)}"
        
        # Location info
        self.conversation_history[4]["content"] = f"Location:\n{json.dumps(self.location_info, indent=2)}"
        
        # NPC templates
        self.conversation_history.append({"role": "system", "content": f"NPC Templates:\n{json.dumps({k: filter_dynamic_fields(v) for k, v in self.npc_templates.items()}, indent=2)}"})
        
        # Encounter details
        self.conversation_history.append({"role": "system", "content": f"Encounter Details:\n{json.dumps(filter_encounter_for_system_prompt(self.encounter_data), indent=2)}"})
        
        # Save initial conversation history
        save_json_file("modules/conversation_history/combat_conversation_history.json", self.conversation_history)
    
    def _determine_first_turn(self) -> Optional[str]:
        """Determine which character has the first turn based on initiative."""
        if not self.encounter_data:
            return None
        
        creatures = self.encounter_data.get("creatures", [])
        if not creatures:
            return None
        
        # Sort by initiative (descending), then alphabetically for ties
        sorted_creatures = sorted(creatures, key=lambda x: (-x.get("initiative", 0), x.get("name", "")))
        
        if sorted_creatures:
            return sorted_creatures[0].get("name")
        
        return None
    
    def get_current_combat_state(self) -> Dict[str, Any]:
        """
        Get the current combat state for client display.
        
        Returns:
            Dict containing current combat state information
        """
        if not self.is_active:
            return {"is_active": False}
        
        # Get current round
        current_round = self.encounter_data.get('combat_round', self.encounter_data.get('current_round', 1))
        
        # Build initiative order
        initiative_order = []
        for creature in self.encounter_data.get("creatures", []):
            if creature.get("status", "alive").lower() != "dead":
                initiative_order.append({
                    "name": creature.get("name"),
                    "initiative": creature.get("initiative", 0),
                    "type": creature.get("type"),
                    "current_hp": creature.get("currentHitPoints"),
                    "max_hp": creature.get("maxHitPoints"),
                    "status": creature.get("status", "alive")
                })
        
        # Sort by initiative
        initiative_order.sort(key=lambda x: (-x["initiative"], x["name"]))
        
        # Get recent combat log (last 10 messages)
        combat_log = []
        for msg in self.conversation_history[-10:]:
            if msg.get("role") in ["user", "assistant"]:
                content = msg.get("content", "")
                if msg.get("role") == "assistant":
                    try:
                        parsed = json.loads(content)
                        if "narration" in parsed:
                            combat_log.append(f"DM: {parsed['narration']}")
                        else:
                            combat_log.append(f"DM: {content}")
                    except:
                        combat_log.append(f"DM: {content}")
                else:
                    # Extract player input from DM notes
                    if "Player:" in content:
                        player_part = content.split("Player:", 1)[1].strip()
                        if player_part:
                            combat_log.append(f"Player: {player_part}")
        
        return {
            "is_active": self.is_active,
            "encounter_id": self.encounter_id,
            "current_round": current_round,
            "current_turn": self.current_turn_character,
            "initiative_order": initiative_order,
            "combat_log": combat_log,
            "player_info": self._get_player_display_info(),
            "creatures": self._get_creatures_display_info()
        }
    
    def _get_player_display_info(self) -> Dict:
        """Get player information for display."""
        if not self.player_info:
            return {}
        
        return {
            "name": self.player_info.get("name"),
            "current_hp": self.player_info.get("hitPoints", 0),
            "max_hp": self.player_info.get("maxHitPoints", 0),
            "status": self.player_info.get("status", "alive"),
            "condition": self.player_info.get("condition", "none"),
            "conditions": self.player_info.get("condition_affected", []),
            "spell_slots": self._get_spell_slots_display(self.player_info)
        }
    
    def _get_creatures_display_info(self) -> List[Dict]:
        """Get creatures information for display."""
        creatures_info = []
        
        for creature in self.encounter_data.get("creatures", []):
            if creature["type"] == "player":
                continue  # Player info handled separately
            
            creature_info = {
                "name": creature.get("name"),
                "type": creature.get("type"),
                "current_hp": creature.get("currentHitPoints"),
                "max_hp": creature.get("maxHitPoints"),
                "status": creature.get("status", "alive"),
                "condition": creature.get("condition", "none")
            }
            
            # Add spell slots for NPCs
            if creature["type"] == "npc":
                npc_data, _ = load_npc_with_fuzzy_match(creature["name"], self.path_manager)
                if npc_data:
                    creature_info["spell_slots"] = self._get_spell_slots_display(npc_data)
            
            creatures_info.append(creature_info)
        
        return creatures_info
    
    def _get_spell_slots_display(self, character_data: Dict) -> Dict:
        """Get spell slots display information for a character."""
        spellcasting = character_data.get("spellcasting", {})
        if not spellcasting or "spellSlots" not in spellcasting:
            return {}
        
        spell_slots = spellcasting["spellSlots"]
        slot_display = {}
        
        for level in range(1, 10):  # Spell levels 1-9
            level_key = f"level{level}"
            if level_key in spell_slots:
                slot_data = spell_slots[level_key]
                current_slots = slot_data.get("current", 0)
                max_slots = slot_data.get("max", 0)
                if max_slots > 0:
                    slot_display[f"level{level}"] = {
                        "current": current_slots,
                        "max": max_slots
                    }
        
        return slot_display
    
    def process_player_turn(self, player_name: str, action_text: str) -> Dict[str, Any]:
        """
        Process a player's turn action.
        
        Args:
            player_name: Name of the player taking the action
            action_text: The action text from the player
            
        Returns:
            Dict containing the result of the action processing
        """
        if not self.is_active:
            return {"error": "Combat is not active"}
        
        # Check if it's the player's turn
        if player_name != self.current_turn_character:
            return {"error": f"It's not {player_name}'s turn. Current turn: {self.current_turn_character}"}
        
        debug(f"PROCESSING: Player {player_name} action: {action_text[:50]}...", category="combat_events")
        
        # Sync character data
        sync_active_encounter()
        
        # Refresh conversation history with latest data
        self._refresh_conversation_history()
        
        # Prepare dynamic state info
        dynamic_state = self._prepare_dynamic_state()
        
        # Get prerolls for current round
        current_round = self.encounter_data.get('combat_round', self.encounter_data.get('current_round', 1))
        preroll_text = self._get_prerolls_for_round(current_round)
        
        # Create the user input with combat state
        user_input_with_note = self._create_combat_prompt(action_text, dynamic_state, preroll_text)
        
        # Clean old DM notes and add user input
        self.conversation_history = clean_old_dm_notes(self.conversation_history)
        self.conversation_history.append({"role": "user", "content": user_input_with_note})
        
        # Get AI response with validation
        ai_response = self._get_ai_response_with_validation(action_text)
        
        if not ai_response:
            return {"error": "Failed to get AI response"}
        
        # Process the AI response
        self._process_ai_response(ai_response)
        
        # Determine next turn
        self._advance_turn()
        
        # Check if combat has ended
        if self._check_combat_ended():
            self._end_combat()
            return self.get_current_combat_state()
        
        # Save conversation history
        save_json_file("modules/conversation_history/combat_conversation_history.json", self.conversation_history)
        
        return self.get_current_combat_state()
    
    def _refresh_conversation_history(self):
        """Refresh conversation history with latest character data."""
        # Reload player info
        if self.player_info:
            player_name = normalize_character_name(self.player_info["name"])
            player_file = self.path_manager.get_character_path(player_name)
            try:
                fresh_player_data = safe_json_load(player_file)
                if fresh_player_data:
                    self.conversation_history[2]["content"] = f"Player Character:\n{json.dumps(filter_dynamic_fields(fresh_player_data), indent=2)}"
            except Exception as e:
                error(f"FAILURE: Failed to reload player file {player_file}", exception=e, category="file_operations")
        
        # Reload encounter data
        json_file_path = f"modules/encounters/encounter_{self.encounter_id}.json"
        try:
            self.encounter_data = safe_json_load(json_file_path)
            if self.encounter_data:
                for i, msg in enumerate(self.conversation_history):
                    if msg["role"] == "system" and "Encounter Details:" in msg["content"]:
                        self.conversation_history[i]["content"] = f"Encounter Details:\n{json.dumps(filter_encounter_for_system_prompt(self.encounter_data), indent=2)}"
                        break
        except Exception as e:
            error(f"FAILURE: Failed to reload encounter file {json_file_path}", exception=e, category="file_operations")
        
        # Reload NPC data
        for creature in self.encounter_data.get("creatures", []):
            if creature["type"] == "npc":
                npc_data, matched_filename = load_npc_with_fuzzy_match(creature["name"], self.path_manager)
                if npc_data and matched_filename:
                    self.npc_templates[matched_filename] = npc_data
        
        # Update NPC templates in conversation history
        for i, msg in enumerate(self.conversation_history):
            if msg["role"] == "system" and "NPC Templates:" in msg["content"]:
                self.conversation_history[i]["content"] = f"NPC Templates:\n{json.dumps({k: filter_dynamic_fields(v) for k, v in self.npc_templates.items()}, indent=2)}"
                break
    
    def _prepare_dynamic_state(self) -> str:
        """Prepare dynamic state information for all creatures."""
        dynamic_state_parts = []
        
        # Player info
        if self.player_info:
            player_name_display = self.player_info["name"]
            current_hp = self.player_info.get("hitPoints", 0)
            max_hp = self.player_info.get("maxHitPoints", 0)
            player_status = self.player_info.get("status", "alive")
            player_condition = self.player_info.get("condition", "none")
            player_conditions = self.player_info.get("condition_affected", [])
            
            dynamic_state_parts.append(f"{player_name_display}:")
            dynamic_state_parts.append(f"  - HP: {current_hp}/{max_hp}")
            dynamic_state_parts.append(f"  - Status: {player_status}")
            dynamic_state_parts.append(f"  - Condition: {player_condition}")
            if player_conditions:
                dynamic_state_parts.append(f"  - Active Conditions: {', '.join(player_conditions)}")
            
            # Add spell slots for player
            spellcasting = self.player_info.get("spellcasting", {})
            if spellcasting and "spellSlots" in spellcasting:
                spell_slots = spellcasting["spellSlots"]
                slot_parts = []
                for level in range(1, 10):
                    level_key = f"level{level}"
                    if level_key in spell_slots:
                        slot_data = spell_slots[level_key]
                        current_slots = slot_data.get("current", 0)
                        max_slots = slot_data.get("max", 0)
                        if max_slots > 0:
                            slot_parts.append(f"L{level}:{current_slots}/{max_slots}")
                if slot_parts:
                    dynamic_state_parts.append(f"  - Spell Slots: {' '.join(slot_parts)}")
        
        # Creature info
        for creature in self.encounter_data.get("creatures", []):
            if creature["type"] != "player":
                creature_name = creature.get("name", "Unknown Creature")
                creature_hp = creature.get("currentHitPoints", "Unknown")
                creature_status = creature.get("status", "alive")
                creature_condition = creature.get("condition", "none")
                
                # Get max HP from correct source
                if creature["type"] == "npc":
                    npc_data, _ = load_npc_with_fuzzy_match(creature_name, self.path_manager)
                    creature_max_hp = npc_data["maxHitPoints"] if npc_data else creature.get("maxHitPoints", "Unknown")
                else:
                    creature_max_hp = creature.get("maxHitPoints", "Unknown")
                
                dynamic_state_parts.append(f"\n{creature_name}:")
                dynamic_state_parts.append(f"  - HP: {creature_hp}/{creature_max_hp}")
                dynamic_state_parts.append(f"  - Status: {creature_status}")
                dynamic_state_parts.append(f"  - Condition: {creature_condition}")
                
                # Add spell slots for NPCs
                if creature["type"] == "npc":
                    npc_data, _ = load_npc_with_fuzzy_match(creature_name, self.path_manager)
                    if npc_data:
                        npc_spellcasting = npc_data.get("spellcasting", {})
                        if npc_spellcasting and "spellSlots" in npc_spellcasting:
                            npc_spell_slots = npc_spellcasting["spellSlots"]
                            npc_slot_parts = []
                            for level in range(1, 10):
                                level_key = f"level{level}"
                                if level_key in npc_spell_slots:
                                    slot_data = npc_spell_slots[level_key]
                                    current_slots = slot_data.get("current", 0)
                                    max_slots = slot_data.get("max", 0)
                                    if max_slots > 0:
                                        npc_slot_parts.append(f"L{level}:{current_slots}/{max_slots}")
                            if npc_slot_parts:
                                dynamic_state_parts.append(f"  - Spell Slots: {' '.join(npc_slot_parts)}")
        
        return "\n".join(dynamic_state_parts)
    
    def _get_prerolls_for_round(self, round_num: int) -> str:
        """Get prerolls for the specified round."""
        cached_round = self.encounter_data.get('preroll_cache', {}).get('round', 0)
        
        if round_num > cached_round:
            # Generate fresh prerolls for new round
            preroll_text = generate_prerolls(self.encounter_data, round_num=round_num)
            self.encounter_data['preroll_cache'] = {
                'round': round_num,
                'rolls': preroll_text,
                'preroll_id': f"{round_num}-{random.randint(1000,9999)}"
            }
            # Save the encounter data with preroll cache
            json_file_path = f"modules/encounters/encounter_{self.encounter_id}.json"
            save_json_file(json_file_path, self.encounter_data)
            debug(f"STATE_CHANGE: Generated new prerolls for round {round_num}", category="combat_events")
        else:
            # Use cached prerolls for current round
            preroll_text = self.encounter_data.get('preroll_cache', {}).get('rolls', '')
            if not preroll_text:
                # Fallback if cache missing
                preroll_text = generate_prerolls(self.encounter_data, round_num=round_num)
                self.encounter_data['preroll_cache'] = {
                    'round': round_num,
                    'rolls': preroll_text,
                    'preroll_id': f"{round_num}-{random.randint(1000,9999)}"
                }
                json_file_path = f"modules/encounters/encounter_{self.encounter_id}.json"
                save_json_file(json_file_path, self.encounter_data)
                debug(f"STATE_CHANGE: Generated fallback prerolls for round {round_num}", category="combat_events")
        
        return preroll_text
    
    def _create_combat_prompt(self, action_text: str, dynamic_state: str, preroll_text: str) -> str:
        """Create the combat prompt for AI processing."""
        current_round = self.encounter_data.get('combat_round', self.encounter_data.get('current_round', 1))
        initiative_order = get_initiative_order(self.encounter_data)
        
        return f"""--- CURRENT COMBAT STATE ---
Round: {current_round}
Initiative Order: {initiative_order}
All Creatures State:
{dynamic_state}

--- PRE-ROLLED DICE FOR NPCS/MONSTERS ---

CRITICAL DICE USAGE - YOU MUST FOLLOW THESE RULES:
1. For an NPC/Monster ATTACK ROLL: You MUST use a die from the '== CREATURE ATTACKS ==' list for that specific creature.
2. For an NPC/Monster SAVING THROW: You MUST use a die from the '== SAVING THROWS ==' list for that specific creature.
3. The '== GENERIC DICE ==' pool is ONLY for damage rolls, spell effects, or other non-attack/non-save rolls.
FAILURE TO USE THE CORRECT POOL IS A CRITICAL ERROR.

{preroll_text}
--- END OF STATE & DICE ---

Player: {action_text}

Now, continue the combat flow by narrating and resolving all remaining monster and NPC turns for the current round in initiative order until you get to my turn or, if I'm done this turn, then narrate the rest of this round.

Your response narratively MUST stop at one of two points:
1.  When you have resolved the turn for the LAST creature in the current round's initiative order.
2.  When the initiative order returns to my turn again.

Do not narrate or process any actions from the next round in this response. The goal is to complete the current round of actions and then pause. If you do need to stop, please engage me creatively so I don't get bored."""
    
    def _get_ai_response_with_validation(self, user_input: str) -> Optional[str]:
        """Get AI response with validation and retries."""
        max_retries = 5
        
        for attempt in range(max_retries):
            try:
                debug(f"AI_CALL: Making AI call for player action (attempt {attempt + 1}/{max_retries})", category="combat_events")
                
                # Calculate temperature with attempt number for dynamic adjustment
                temperature_used = get_combat_temperature(self.encounter_data, validation_attempt=attempt)
                
                response = client.chat.completions.create(
                    model=COMBAT_MAIN_MODEL,
                    temperature=temperature_used,
                    messages=self.conversation_history
                )
                ai_response = response.choices[0].message.content.strip()
                
                if not is_valid_json(ai_response):
                    if attempt < max_retries - 1:
                        self.conversation_history.append({"role": "user", "content": "Invalid JSON format. Please try again."})
                        continue
                    else:
                        error("FAILURE: Invalid JSON after max retries", category="combat_events")
                        return None
                
                # Validate the response
                validation_result = validate_combat_response(ai_response, self.encounter_data, user_input, self.conversation_history)
                
                if validation_result is True:
                    # Response is valid
                    self.conversation_history.append({"role": "assistant", "content": ai_response})
                    return ai_response
                else:
                    # Response is invalid, add feedback and retry
                    if attempt < max_retries - 1:
                        self.conversation_history.append({"role": "user", "content": validation_result})
                        continue
                    else:
                        error("FAILURE: Validation failed after max retries", category="combat_events")
                        return None
                        
            except Exception as e:
                error(f"FAILURE: AI call failed on attempt {attempt + 1}", exception=e, category="combat_events")
                if attempt >= max_retries - 1:
                    return None
        
        return None
    
    def _process_ai_response(self, ai_response: str):
        """Process the AI response and update game state."""
        try:
            parsed_response = json.loads(ai_response)
            
            # Process actions
            actions = parsed_response.get("actions", [])
            for action in actions:
                self._process_action(action)
            
            # Update encounter data
            if "combat_round" in parsed_response:
                self.encounter_data["combat_round"] = parsed_response["combat_round"]
            
            # Save encounter data
            json_file_path = f"modules/encounters/encounter_{self.encounter_id}.json"
            save_json_file(json_file_path, self.encounter_data)
            
        except Exception as e:
            error(f"FAILURE: Failed to process AI response", exception=e, category="combat_events")
    
    def _process_action(self, action: Dict):
        """Process a single action from the AI response."""
        action_type = action.get("action", "").lower()
        
        if action_type == "updatecharacterinfo":
            character_name = action.get("parameters", {}).get("characterName")
            changes = action.get("parameters", {}).get("changes", "")
            if character_name and changes:
                try:
                    update_character_info(character_name, changes)
                    debug(f"SUCCESS: Updated character {character_name}", category="character_updates")
                except Exception as e:
                    error(f"FAILURE: Failed to update character {character_name}", exception=e, category="character_updates")
        
        elif action_type == "updateencounter":
            encounter_id = action.get("parameters", {}).get("encounterId")
            changes = action.get("parameters", {}).get("changes", "")
            if encounter_id and changes:
                try:
                    update_encounter.update_encounter(encounter_id, changes)
                    debug(f"SUCCESS: Updated encounter {encounter_id}", category="encounter_updates")
                except Exception as e:
                    error(f"FAILURE: Failed to update encounter {encounter_id}", exception=e, category="encounter_updates")
    
    def _advance_turn(self):
        """Advance to the next turn in initiative order."""
        creatures = self.encounter_data.get("creatures", [])
        if not creatures:
            return
        
        # Get alive creatures sorted by initiative
        alive_creatures = [c for c in creatures if c.get("status", "alive").lower() != "dead"]
        if not alive_creatures:
            return
        
        alive_creatures.sort(key=lambda x: (-x.get("initiative", 0), x.get("name", "")))
        
        # Find current turn index
        current_index = -1
        for i, creature in enumerate(alive_creatures):
            if creature.get("name") == self.current_turn_character:
                current_index = i
                break
        
        # Move to next creature
        if current_index >= 0 and current_index < len(alive_creatures) - 1:
            self.current_turn_character = alive_creatures[current_index + 1].get("name")
        elif current_index >= 0:
            # End of round, start new round
            self.current_turn_character = alive_creatures[0].get("name")
            current_round = self.encounter_data.get('combat_round', 1)
            self.encounter_data['combat_round'] = current_round + 1
        else:
            # Fallback to first creature
            self.current_turn_character = alive_creatures[0].get("name")
    
    def _check_combat_ended(self) -> bool:
        """Check if combat has ended."""
        creatures = self.encounter_data.get("creatures", [])
        if not creatures:
            return True
        
        # Check if all players are dead
        players = [c for c in creatures if c.get("type") == "player"]
        if players and all(c.get("status", "alive").lower() == "dead" for c in players):
            return True
        
        # Check if all enemies are dead
        enemies = [c for c in creatures if c.get("type") in ["enemy", "npc"]]
        if enemies and all(c.get("status", "alive").lower() == "dead" for c in enemies):
            return True
        
        return False
    
    def _end_combat(self):
        """End the combat and perform cleanup."""
        debug("COMBAT_END: Ending combat", category="combat_events")
        
        # Clear active combat encounter
        if 'worldConditions' in self.party_tracker_data and 'activeCombatEncounter' in self.party_tracker_data['worldConditions']:
            last_encounter_id = self.party_tracker_data["worldConditions"]["activeCombatEncounter"]
            if last_encounter_id:
                self.party_tracker_data["worldConditions"]["lastCompletedEncounter"] = last_encounter_id
            self.party_tracker_data['worldConditions']['activeCombatEncounter'] = ""
            safe_write_json("party_tracker.json", self.party_tracker_data)
        
        # Generate combat summary
        self._generate_combat_summary()
        
        # Generate chat history
        self._generate_chat_history()
        
        self.is_active = False
    
    def _generate_combat_summary(self):
        """Generate and save combat summary."""
        try:
            # Import the summarize_dialogue function from combat_manager
            from core.managers.combat_manager import summarize_dialogue
            dialogue_summary = summarize_dialogue(self.conversation_history, self.location_info, self.party_tracker_data)
            debug(f"SUCCESS: Generated combat summary", category="combat_events")
        except Exception as e:
            error(f"FAILURE: Failed to generate combat summary", exception=e, category="combat_events")
    
    def _generate_chat_history(self):
        """Generate and save combat chat history."""
        try:
            # Import the generate_chat_history function from combat_manager
            from core.managers.combat_manager import generate_chat_history
            generate_chat_history(self.conversation_history, self.encounter_id)
            debug(f"SUCCESS: Generated combat chat history", category="combat_events")
        except Exception as e:
            error(f"FAILURE: Failed to generate combat chat history", exception=e, category="combat_events")
    
    def process_ai_turns(self) -> Dict[str, Any]:
        """
        Process AI turns for NPCs and monsters until it's a player's turn.
        
        Returns:
            Dict containing the updated combat state
        """
        if not self.is_active:
            return {"error": "Combat is not active"}
        
        debug("AI_TURNS: Processing AI turns", category="combat_events")
        
        # Process AI turns until it's a player's turn
        while self.current_turn_character and self.is_active:
            # Check if current turn is a player
            current_creature = None
            for creature in self.encounter_data.get("creatures", []):
                if creature.get("name") == self.current_turn_character:
                    current_creature = creature
                    break
            
            if not current_creature:
                break
            
            # If it's a player's turn, stop processing
            if current_creature.get("type") == "player":
                break
            
            # Process AI turn
            self._process_ai_turn(current_creature)
            
            # Advance turn
            self._advance_turn()
            
            # Check if combat has ended
            if self._check_combat_ended():
                self._end_combat()
                break
        
        return self.get_current_combat_state()
    
    def _process_ai_turn(self, creature: Dict):
        """Process a single AI turn for a creature."""
        creature_name = creature.get("name", "Unknown")
        debug(f"AI_TURN: Processing turn for {creature_name}", category="combat_events")
        
        # Create AI turn prompt
        ai_prompt = self._create_ai_turn_prompt(creature)
        
        # Add to conversation history
        self.conversation_history.append({"role": "user", "content": ai_prompt})
        
        # Get AI response
        ai_response = self._get_ai_response_with_validation(f"AI turn for {creature_name}")
        
        if ai_response:
            # Process the response
            self._process_ai_response(ai_response)
        else:
            # Fallback: just advance turn
            debug(f"AI_TURN: Failed to get AI response for {creature_name}, advancing turn", category="combat_events")
    
    def _create_ai_turn_prompt(self, creature: Dict) -> str:
        """Create a prompt for AI turn processing."""
        creature_name = creature.get("name", "Unknown")
        creature_type = creature.get("type", "unknown")
        
        # Get dynamic state
        dynamic_state = self._prepare_dynamic_state()
        
        # Get prerolls
        current_round = self.encounter_data.get('combat_round', self.encounter_data.get('current_round', 1))
        preroll_text = self._get_prerolls_for_round(current_round)
        
        return f"""--- CURRENT COMBAT STATE ---
Round: {current_round}
Current Turn: {creature_name} ({creature_type})
All Creatures State:
{dynamic_state}

--- PRE-ROLLED DICE FOR NPCS/MONSTERS ---

CRITICAL DICE USAGE - YOU MUST FOLLOW THESE RULES:
1. For an NPC/Monster ATTACK ROLL: You MUST use a die from the '== CREATURE ATTACKS ==' list for that specific creature.
2. For an NPC/Monster SAVING THROW: You MUST use a die from the '== SAVING THROWS ==' list for that specific creature.
3. The '== GENERIC DICE ==' pool is ONLY for damage rolls, spell effects, or other non-attack/non-save rolls.
FAILURE TO USE THE CORRECT POOL IS A CRITICAL ERROR.

{preroll_text}
--- END OF STATE & DICE ---

Dungeon Master Note: It is now {creature_name}'s turn. Please narrate their action and resolve it according to their abilities and the current combat situation. Use the prerolled dice appropriately."""
    
    def get_combat_status(self) -> str:
        """Get a human-readable combat status."""
        if not self.is_active:
            return "Combat has ended"
        
        current_round = self.encounter_data.get('combat_round', self.encounter_data.get('current_round', 1))
        return f"Round {current_round} - {self.current_turn_character}'s turn"
    
    def is_player_turn(self, player_name: str) -> bool:
        """Check if it's the specified player's turn."""
        return self.current_turn_character == player_name
    
    def get_available_actions(self, player_name: str) -> List[str]:
        """Get available actions for a player (placeholder for future expansion)."""
        if not self.is_player_turn(player_name):
            return []
        
        # This could be expanded to include specific actions based on character abilities
        return ["attack", "cast_spell", "use_item", "move", "dodge", "help"] 