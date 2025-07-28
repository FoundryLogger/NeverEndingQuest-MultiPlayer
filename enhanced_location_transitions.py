#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0

"""
Enhanced Location Transitions for Multiplayer
Adaptation of main.py check_and_process_location_transitions() for multiplayer environment
"""

import re
from utils.enhanced_logger import debug, info, warning, error
from core.ai.cumulative_summary import (
    generate_enhanced_adventure_summary,
    update_journal_with_summary,
    compress_conversation_history_on_transition
)

def check_and_process_location_transitions_multiplayer(conversation_history, party_tracker_data, path_manager):
    """
    MULTIPLAYER ADAPTATION of main.py check_and_process_location_transitions()
    
    Check if there are any unprocessed location transitions in the conversation history
    and process them to create summaries and compress the history.
    
    Args:
        conversation_history: List of conversation messages
        party_tracker_data: Current party tracker data
        path_manager: ModulePathManager instance
        
    Returns:
        Updated conversation history (compressed if processing occurred)
    """
    try:
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
            debug("LOCATION_TRANSITIONS: No location transitions found in conversation history", category="location_transitions")
            return conversation_history
        
        # Check if this transition has already been processed (has a summary right before it)
        if last_transition_index > 0:
            prev_msg = conversation_history[last_transition_index - 1]
            if "=== LOCATION SUMMARY ===" in prev_msg.get("content", ""):
                # This transition has already been processed
                debug("LOCATION_TRANSITIONS: Transition already processed, skipping", category="location_transitions")
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
            debug("LOCATION_TRANSITIONS: No conversation after transition yet, waiting", category="location_transitions")
            return conversation_history
        
        # Extract the leaving location from the transition message
        # New format: "Location transition: [from_location] (ID) to [to_location] (ID)"
        # Old format: "Location transition: [from_location] to [to_location]"
        leaving_location_name = None
        leaving_location_id = None
        
        try:
            # Try to extract with IDs first (new format)
            id_pattern = r'Location transition: (.+?) \(([A-Z]\d+)\) to (.+?) \(([A-Z]\d+)\)'
            id_match = re.match(id_pattern, last_transition_content)
            
            if id_match:
                # New format with IDs
                leaving_location_name = id_match.group(1)
                leaving_location_id = id_match.group(2)
                debug(f"LOCATION_TRANSITIONS: Extracted from new format - Location: {leaving_location_name}, ID: {leaving_location_id}", category="location_transitions")
            else:
                # Fall back to old format
                parts = last_transition_content.split(" to ")
                if len(parts) == 2:
                    from_part = parts[0].replace("Location transition: ", "").strip()
                    leaving_location_name = from_part
                    leaving_location_id = None
                    debug(f"LOCATION_TRANSITIONS: Extracted from old format - Location: {leaving_location_name}", category="location_transitions")
                else:
                    warning("LOCATION_TRANSITIONS: Could not parse transition message format", category="location_transitions")
                    return conversation_history
        except Exception as e:
            error(f"LOCATION_TRANSITIONS: Error parsing transition message", exception=e, category="location_transitions")
            return conversation_history
        
        if not leaving_location_name:
            warning("LOCATION_TRANSITIONS: Could not extract leaving location name", category="location_transitions")
            return conversation_history
        
        debug(f"LOCATION_TRANSITIONS: Processing transition from {leaving_location_name}", category="location_transitions")
        
        try:
            # Generate enhanced adventure summary using the same single-player function
            adventure_summary = generate_enhanced_adventure_summary(
                conversation_history,
                party_tracker_data,
                leaving_location_name
            )
            
            if adventure_summary:
                # Update journal with the summary using the same single-player function
                update_journal_with_summary(
                    adventure_summary,
                    party_tracker_data,
                    leaving_location_name
                )
                
                # Compress conversation history using the same single-player function
                compressed_history = compress_conversation_history_on_transition(
                    conversation_history,
                    leaving_location_name
                )
                
                # MULTIPLAYER SPECIFIC: Check if chunked compression is needed
                # Note: This is optional since multiplayer handles conversation differently
                try:
                    from core.ai.chunked_compression_integration import check_and_perform_chunked_compression
                    if check_and_perform_chunked_compression():
                        debug("LOCATION_TRANSITIONS: Chunked compression performed after location transition", category="conversation_management")
                        # In multiplayer, we work with the compressed_history directly rather than reloading from file
                except Exception as e:
                    debug(f"LOCATION_TRANSITIONS: Chunked compression check failed (non-critical): {str(e)}", category="conversation_management")
                
                info(f"LOCATION_TRANSITIONS: Successfully processed location transition from {leaving_location_name}", category="location_transitions")
                return compressed_history
            else:
                debug("LOCATION_TRANSITIONS: No adventure summary generated, returning original history", category="location_transitions")
                return conversation_history
                
        except Exception as e:
            error(f"LOCATION_TRANSITIONS: Failed to process location transition", exception=e, category="location_transitions")
            # Return original history to be safe
            return conversation_history
            
    except Exception as e:
        error(f"LOCATION_TRANSITIONS: Unexpected error in location transition processing", exception=e, category="location_transitions")
        # Always return the original history if something goes wrong
        return conversation_history

def test_location_transitions_multiplayer():
    """Test function to verify the adaptation works"""
    print("Testing Enhanced Location Transitions for Multiplayer...")
    
    # Test basic functionality
    try:
        # Test with empty conversation
        empty_conversation = []
        result = check_and_process_location_transitions_multiplayer(empty_conversation, {}, None)
        print(f"Empty conversation test: {len(result)} messages (expected: 0)")
        
        # Test with conversation without transitions
        no_transition_conversation = [
            {"role": "system", "content": "You are a DM"},
            {"role": "user", "content": "I look around"},
            {"role": "assistant", "content": "You see a room"}
        ]
        result = check_and_process_location_transitions_multiplayer(no_transition_conversation, {}, None)
        print(f"No transition test: {len(result)} messages (expected: 3)")
        
        print("Basic tests completed successfully")
        return True
        
    except Exception as e:
        print(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    test_location_transitions_multiplayer()