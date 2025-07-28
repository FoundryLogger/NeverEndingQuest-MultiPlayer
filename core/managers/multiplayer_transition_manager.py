# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0
# License: See LICENSE file in the repository root
# This software is subject to the terms of the Fair Source License.

# ============================================================================
# MULTIPLAYER_TRANSITION_MANAGER.PY - MODULE TIMELINE PRESERVATION FOR MULTIPLAYER
# ============================================================================
# 
# ARCHITECTURE ROLE: Manager Pattern - Module Transition & Timeline Orchestration
# 
# This manager adapts the single-player module transition system for multiplayer,
# maintaining chronological adventure history across modules with conversation
# compression and adventure summary generation.
# 
# KEY RESPONSIBILITIES:
# - Detect module transitions in multiplayer context
# - Compress conversation history on module transitions
# - Generate AI-powered adventure summaries
# - Preserve timeline continuity across modules
# - Broadcast transition events to all players
# 
# INTEGRATION POINTS:
# - Works with action_handler.py for transition detection
# - Uses cumulative_summary.py for AI summary generation
# - Coordinates with campaign_manager.py for archiving
# - Broadcasts events via SocketIO to all players
# 
# TIMELINE PRESERVATION:
# - Two-condition boundary detection for optimal compression
# - Sequential module summaries maintain adventure chronology
# - Automatic archiving of completed module conversations
# - Hub-and-spoke campaign model support
# ============================================================================

import json
import re
import threading
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime

from utils.enhanced_logger import debug, info, warning, error, set_script_name
from utils.file_operations import safe_read_json, safe_write_json
from utils.encoding_utils import sanitize_text
from utils.module_path_manager import ModulePathManager
# Note: AI summary generation is implemented directly in this module
from core.managers.campaign_manager import CampaignManager
from core.managers.status_manager import status_generating_summary, status_compressing_history

# Set script name for logging
set_script_name("multiplayer_transition_manager")

class MultiplayerTransitionManager:
    """Manages module transitions and timeline preservation for multiplayer games"""
    
    def __init__(self):
        """Initialize the transition manager"""
        self.transition_lock = threading.Lock()
        self.path_manager = ModulePathManager()
        self.campaign_manager = CampaignManager()
        self.socketio = None  # Will be set by server.py
        
        info("MultiplayerTransitionManager initialized", category="module_transitions")
    
    def set_socketio(self, socketio):
        """Set the SocketIO instance for broadcasting events"""
        self.socketio = socketio
        debug("SocketIO instance set for transition manager", category="module_transitions")
    
    def check_and_process_module_transitions(self, conversation_history: List[Dict], 
                                           party_tracker_data: Dict) -> List[Dict]:
        """
        Check if there are any unprocessed module transitions in the conversation history
        and process them to create summaries and compress the history.
        
        This is the multiplayer adaptation of the single-player function in main.py
        """
        with self.transition_lock:
            try:
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
                    if msg.get("role") == "assistant" or (msg.get("role") == "user" and 
                                                         "Dungeon Master Note:" not in msg.get("content", "")):
                        has_conversation_after = True
                        break
                
                if not has_conversation_after:
                    # No conversation after the transition yet, wait for next round
                    return conversation_history
                
                # Extract the leaving module from the transition message
                # Format: "Module transition: [from_module] to [to_module]"
                pattern = r'Module transition: (.+?) to (.+?)$'
                match = re.match(pattern, last_transition_content)
                
                if match:
                    leaving_module_name = match.group(1)
                    arriving_module_name = match.group(2)
                    
                    info(f"Processing module transition: {leaving_module_name} -> {arriving_module_name}", 
                         category="module_transitions")
                    
                    # Broadcast transition start event
                    if self.socketio:
                        self.socketio.emit('module_transition_start', {
                            'from_module': leaving_module_name,
                            'to_module': arriving_module_name,
                            'timestamp': datetime.now().isoformat()
                        })
                    
                    # Generate module summary
                    module_summary = self.generate_module_summary(
                        conversation_history,
                        party_tracker_data,
                        leaving_module_name,
                        last_transition_index
                    )
                    
                    if module_summary:
                        # Compress conversation history for module transition
                        compressed_history = self.compress_conversation_history_on_module_transition(
                            conversation_history,
                            leaving_module_name,
                            module_summary,
                            last_transition_index
                        )
                        
                        # Save the compressed history
                        self._save_conversation_history(compressed_history)
                        
                        # Archive the module conversation (if archiving is available)
                        try:
                            self._archive_module_conversation(
                                leaving_module_name,
                                module_summary,
                                conversation_history[:last_transition_index]
                            )
                        except Exception as e:
                            warning(f"Module archiving failed but transition continues", exception=e, category="module_transitions")
                        
                        # Broadcast transition complete event
                        if self.socketio:
                            self.socketio.emit('module_transition_complete', {
                                'from_module': leaving_module_name,
                                'to_module': arriving_module_name,
                                'summary_generated': True,
                                'history_compressed': True,
                                'timestamp': datetime.now().isoformat()
                            })
                        
                        info(f"Module transition processed successfully: {leaving_module_name} -> {arriving_module_name}", 
                             category="module_transitions")
                        return compressed_history
                    else:
                        warning(f"No module summary generated for {leaving_module_name}", 
                               category="module_transitions")
                        return conversation_history
                        
                else:
                    error(f"Could not parse module transition: {last_transition_content}", 
                         category="module_transitions")
                    return conversation_history
                    
            except Exception as e:
                error(f"Failed to process module transition", exception=e, category="module_transitions")
                import traceback
                traceback.print_exc()
                return conversation_history
    
    def generate_module_summary(self, conversation_history: List[Dict], 
                               party_tracker_data: Dict, 
                               module_name: str, 
                               transition_index: int) -> Optional[str]:
        """
        Generate a summary for a module transition.
        Adapts the single-player function to work with multiplayer context.
        """
        try:
            status_generating_summary()
            
            # Use the same two-condition boundary detection logic
            boundary_index = None
            
            # Condition 1: Look for previous module transition OR module summary first
            for i in range(transition_index - 1, -1, -1):
                msg = conversation_history[i]
                content = msg.get("content", "")
                
                # Look for either previous module transition or existing module summary
                if (msg.get("role") == "user" and 
                    ("Module transition:" in content or "Module summary:" in content)):
                    boundary_index = i + 1  # Start after previous transition/summary
                    debug(f"CONDITION 1 - Found previous module marker at index {i}, boundary at {boundary_index}", 
                         category="conversation_management")
                    break
            
            # Condition 2: If no previous module transition/summary, find last system message
            if boundary_index is None:
                for i in range(transition_index - 1, -1, -1):
                    msg = conversation_history[i]
                    if msg.get("role") == "system":
                        boundary_index = i + 1  # Start after last system message
                        debug(f"CONDITION 2 - Found last system message at index {i}, boundary at {boundary_index}", 
                             category="conversation_management")
                        break
                
                # Fallback if no system message found (shouldn't happen)
                if boundary_index is None:
                    boundary_index = 0
                    debug(f"FALLBACK - No system message found, using boundary at {boundary_index}", 
                         category="conversation_management")
            
            # Extract ONLY the conversation from boundary to transition (actual gameplay)
            module_conversation = conversation_history[boundary_index:transition_index]
            debug(f"Extracting {len(module_conversation)} messages from index {boundary_index} to {transition_index} for summary", 
                 category="conversation_management")
            
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
            
            debug(f"Found {len(meaningful_messages)} meaningful conversation messages to summarize", 
                 category="summary_building")
            
            # Use the AI summary generation from cumulative_summary module
            if meaningful_messages:
                # Format messages for AI summarization
                dialogue = f"Module: {module_name}\n\nEvents and conversations:\n\n"
                
                for message in meaningful_messages:
                    role = message.get('role')
                    content = message.get('content', '')
                    
                    if role == 'assistant':
                        # Extract narration from JSON if present
                        if content.strip().startswith("{"):
                            try:
                                parsed = json.loads(content)
                                narration = parsed.get("narration", content)
                                dialogue += f"Dungeon Master: {narration}\n\n"
                            except:
                                dialogue += f"Dungeon Master: {content}\n\n"
                        else:
                            dialogue += f"Dungeon Master: {content}\n\n"
                    elif role == 'user':
                        dialogue += f"Player: {content}\n\n"
                
                # Generate summary using AI
                summary = self._generate_ai_summary(module_name, dialogue)
                
                if summary:
                    # Format as complete module summary
                    formatted_summary = f"=== MODULE SUMMARY ===\n\n{module_name}:\n"
                    formatted_summary += "-" * len(module_name + ":") + "\n"
                    formatted_summary += summary
                    
                    return formatted_summary
                else:
                    warning(f"AI summary generation failed for module {module_name}", 
                           category="module_transitions")
                    return None
            else:
                info(f"No meaningful messages to summarize for module {module_name}", 
                    category="module_transitions")
                return None
                
        except Exception as e:
            error(f"Failed to generate module summary", exception=e, category="module_transitions")
            return None
    
    def compress_conversation_history_on_module_transition(self, 
                                                         conversation_history: List[Dict],
                                                         module_name: str,
                                                         summary_text: str,
                                                         transition_index: int) -> List[Dict]:
        """
        Compress conversation history by replacing conversation segment with summary,
        preserving previous summaries. Adapts single-player logic for multiplayer.
        """
        try:
            status_compressing_history()
            
            # Find the boundary for compression - same logic as generate_module_summary
            boundary_index = None
            
            for i in range(transition_index - 1, -1, -1):
                msg = conversation_history[i]
                content = msg.get("content", "")
                
                # Look for either previous module transition or existing module summary
                if (msg.get("role") == "user" and 
                    ("Module transition:" in content or "Module summary:" in content)):
                    boundary_index = i + 1  # Start after previous transition/summary
                    debug(f"COMPRESSION - Found previous module marker at index {i}, boundary at {boundary_index}", 
                         category="conversation_management")
                    break
            
            # If no previous module marker, find last system message
            if boundary_index is None:
                for i, msg in enumerate(conversation_history):
                    if msg.get("role") == "system":
                        boundary_index = i + 1  # Start after system message
                        debug(f"COMPRESSION - Found system message at index {i}, boundary at {boundary_index}", 
                             category="conversation_management")
                        break
                
                if boundary_index is None:
                    boundary_index = 0
                    debug(f"COMPRESSION - No system message found, using boundary at {boundary_index}", 
                         category="conversation_management")
            
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
            
            info(f"Compressed module conversation from {len(conversation_history)} to {len(compressed_history)} messages", 
                category="conversation_management")
            debug(f"Preserved {boundary_index} messages before boundary, added 1 summary, kept {len(conversation_history) - transition_index} messages after transition", 
                 category="conversation_management")
            
            return compressed_history
            
        except Exception as e:
            error(f"Failed to compress conversation history", exception=e, category="conversation_management")
            return conversation_history
    
    def _generate_ai_summary(self, module_name: str, dialogue: str) -> Optional[str]:
        """Generate AI summary for module content"""
        try:
            from openai import OpenAI
            from config import OPENAI_API_KEY, ADVENTURE_SUMMARY_MODEL
            
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            messages = [
                {"role": "system", "content": f"""You are a chronicler documenting a 5th edition campaign. Your task is to write a comprehensive summary of the adventure in module '{module_name}'.
                
Your summary should capture:
1. Major story developments and plot progression
2. Key character decisions and their consequences  
3. Important NPCs met and relationships formed
4. Combat encounters and their outcomes
5. Items acquired and resources used
6. Character development and party dynamics
7. Mysteries uncovered or questions raised
8. Module completion status

Use past tense and third person. Be specific about events, names, and outcomes. Create a narrative summary that preserves the adventure's story for future reference."""},
                {"role": "user", "content": dialogue}
            ]
            
            response = client.chat.completions.create(
                model=ADVENTURE_SUMMARY_MODEL,
                temperature=0.8,
                messages=messages
            )
            
            summary = response.choices[0].message.content.strip()
            summary = sanitize_text(summary)
            
            info(f"AI summary generated for module {module_name}", category="module_transitions")
            return summary
            
        except Exception as e:
            error(f"Failed to generate AI summary", exception=e, category="module_transitions")
            return None
    
    def _save_conversation_history(self, conversation_history: List[Dict]):
        """Save the conversation history to file"""
        try:
            conversation_file = "modules/conversation_history/conversation_history.json"
            if safe_write_json(conversation_file, conversation_history):
                debug("Conversation history saved successfully", category="file_operations")
            else:
                error("Failed to save conversation history", category="file_operations")
        except Exception as e:
            error(f"Error saving conversation history", exception=e, category="file_operations")
    
    def _archive_module_conversation(self, module_name: str, summary: str, conversation: List[Dict]):
        """Archive the module conversation and summary"""
        try:
            # Use campaign manager to handle archiving
            self.campaign_manager.archive_module_completion(
                module_name=module_name,
                conversation_history=conversation,
                adventure_summary=summary
            )
            
            info(f"Module {module_name} archived successfully", category="module_transitions")
            
        except Exception as e:
            error(f"Failed to archive module {module_name}", exception=e, category="module_transitions")

# Singleton instance
_transition_manager = None

def get_multiplayer_transition_manager():
    """Get or create the singleton transition manager instance"""
    global _transition_manager
    if _transition_manager is None:
        _transition_manager = MultiplayerTransitionManager()
    return _transition_manager