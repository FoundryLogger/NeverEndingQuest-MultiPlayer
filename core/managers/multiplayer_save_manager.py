#!/usr/bin/env python3
"""
Multiplayer Save Game Manager Module for NeverEndingQuest

Extends the base SaveGameManager with multiplayer-specific functionality.
"""

# ============================================================================
# MULTIPLAYER_SAVE_MANAGER.PY - MULTIPLAYER GAME STATE PERSISTENCE
# ============================================================================
# 
# ARCHITECTURE ROLE: Data Management Layer - Multiplayer Save/Load System
# 
# This module extends the single-player SaveGameManager to support multiplayer
# functionality including player permissions, concurrent access handling, and
# real-time notifications.
# 
# KEY RESPONSIBILITIES:
# - Player permission management for save/load operations
# - Concurrent save handling with locking mechanisms
# - Real-time save notifications to all connected players
# - Multiplayer-specific metadata (all players, host info)
# - Auto-save functionality without disrupting gameplay
# - Save conflict resolution
# 
# DESIGN PRINCIPLES:
# - Non-breaking: Extends rather than modifies base functionality
# - Thread-safe: Handles concurrent access from multiple players
# - Graceful degradation: Falls back safely on errors
# - Progressive enhancement: Works with existing save format
# ============================================================================

import os
import json
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set

# Import base SaveGameManager
from updates.save_game_manager import SaveGameManager
from utils.file_operations import safe_write_json, safe_read_json
from utils.encoding_utils import safe_json_load
from utils.enhanced_logger import debug, info, warning, error, set_script_name

# Set script name for logging
set_script_name(__name__)

class MultiplayerSaveManager(SaveGameManager):
    """Extends SaveGameManager with multiplayer-specific functionality"""
    
    def __init__(self):
        super().__init__()
        # Multiplayer-specific attributes
        self.save_lock = threading.Lock()
        self.active_players: Set[str] = set()
        self.host_player: Optional[str] = None
        self.last_save_time: Optional[datetime] = None
        self.auto_save_enabled = True
        self.auto_save_interval = 300  # 5 minutes in seconds
        
    def set_active_players(self, players: List[str]):
        """Update the list of active players"""
        self.active_players = set(players)
        debug(f"MULTIPLAYER: Updated active players: {players}", category="save_game")
        
    def set_host_player(self, host: str):
        """Set the host player who has save/load permissions"""
        self.host_player = host
        info(f"MULTIPLAYER: Set host player: {host}", category="save_game")
        
    def can_player_save(self, player_name: str) -> bool:
        """Check if a player has permission to save"""
        # In multiplayer, only the host can save
        return player_name == self.host_player
        
    def can_player_load(self, player_name: str) -> bool:
        """Check if a player has permission to load"""
        # In multiplayer, only the host can load
        return player_name == self.host_player
        
    def get_multiplayer_metadata(self) -> Dict[str, Any]:
        """Get multiplayer-specific metadata"""
        return {
            "multiplayer": True,
            "host_player": self.host_player,
            "active_players": list(self.active_players),
            "player_count": len(self.active_players),
            "save_type": "multiplayer"
        }
        
    def get_save_directory(self) -> str:
        """Override to use multiplayer-specific save directory"""
        base_dir = super().get_save_directory()
        return f"{base_dir}/multiplayer"
        
    def generate_save_metadata(self, description: str = "", save_mode: str = "essential") -> Dict[str, Any]:
        """Extend metadata with multiplayer information"""
        metadata = super().generate_save_metadata(description, save_mode)
        
        # Add multiplayer-specific metadata
        metadata.update(self.get_multiplayer_metadata())
        
        # Add character info for all players
        character_info = {}
        for player in self.active_players:
            char_file = f"characters/{player.lower()}.json"
            if os.path.exists(char_file):
                char_data = safe_json_load(char_file)
                if char_data:
                    character_info[player] = {
                        "name": char_data.get("name", player),
                        "level": char_data.get("level", 1),
                        "class": char_data.get("class", "Unknown"),
                        "race": char_data.get("race", "Unknown"),
                        "hp": char_data.get("hitPoints", 0),
                        "max_hp": char_data.get("maxHitPoints", 0)
                    }
        
        metadata["character_info"] = character_info
        return metadata
        
    def create_save_game_thread_safe(self, player_name: str, description: str = "", 
                                   save_mode: str = "essential") -> Tuple[bool, str]:
        """Thread-safe save game creation"""
        # Check permissions
        if not self.can_player_save(player_name):
            return False, f"Only the host ({self.host_player}) can save the game"
            
        # Acquire lock for thread safety
        with self.save_lock:
            try:
                # Call parent save method
                success, message = self.create_save_game(description, save_mode)
                
                if success:
                    self.last_save_time = datetime.now()
                    # Add player who saved to the message
                    message = f"{message}\nSaved by: {player_name}"
                    
                return success, message
                
            except Exception as e:
                error_msg = f"Failed to create multiplayer save: {str(e)}"
                error(f"FAILURE: {error_msg}", category="save_game")
                return False, error_msg
                
    def restore_save_game_thread_safe(self, player_name: str, save_folder: str) -> Tuple[bool, str]:
        """Thread-safe save game restoration"""
        # Check permissions
        if not self.can_player_load(player_name):
            return False, f"Only the host ({self.host_player}) can load saves"
            
        # Acquire lock for thread safety
        with self.save_lock:
            try:
                # Call parent restore method
                success, message = self.restore_save_game(save_folder)
                
                if success:
                    # Add player who loaded to the message
                    message = f"{message}\nLoaded by: {player_name}"
                    
                return success, message
                
            except Exception as e:
                error_msg = f"Failed to restore multiplayer save: {str(e)}"
                error(f"FAILURE: {error_msg}", category="save_game")
                return False, error_msg
                
    def list_save_games_with_permissions(self, player_name: str) -> Tuple[List[Dict[str, Any]], bool]:
        """List save games and indicate if player can load them"""
        saves = self.list_save_games()
        can_load = self.can_player_load(player_name)
        
        # Add permission info to each save
        for save in saves:
            save["can_load"] = can_load
            save["is_multiplayer"] = save.get("multiplayer", False)
            
        return saves, can_load
        
    def should_auto_save(self) -> bool:
        """Check if auto-save should trigger"""
        if not self.auto_save_enabled:
            return False
            
        if self.last_save_time is None:
            return True
            
        time_since_save = (datetime.now() - self.last_save_time).total_seconds()
        return time_since_save >= self.auto_save_interval
        
    def create_auto_save(self) -> Tuple[bool, str]:
        """Create an automatic save"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        description = f"Auto-save at {timestamp}"
        
        # Use host player for auto-saves
        if self.host_player:
            return self.create_save_game_thread_safe(self.host_player, description, "essential")
        else:
            return False, "No host player set for auto-save"
            
    def get_save_info(self, save_folder: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific save"""
        saves = self.list_save_games()
        for save in saves:
            if save.get("save_folder") == save_folder:
                return save
        return None
        
    def validate_save_compatibility(self, save_metadata: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if a save is compatible with current game state"""
        # Check module compatibility
        save_module = save_metadata.get("module", "Unknown")
        if self.current_module and save_module != self.current_module:
            return False, f"Save is from module '{save_module}' but current module is '{self.current_module}'"
            
        # Check save format version
        save_version = save_metadata.get("system_info", {}).get("save_format_version", "0.0")
        if save_version != "1.0":
            return False, f"Incompatible save format version: {save_version}"
            
        return True, "Save is compatible"
        
    def cleanup_old_saves(self, keep_count: int = 10):
        """Remove old saves keeping only the most recent ones"""
        saves = self.list_save_games()
        
        if len(saves) <= keep_count:
            return
            
        # Sort by timestamp (already sorted in list_save_games)
        saves_to_delete = saves[keep_count:]
        
        for save in saves_to_delete:
            save_folder = save.get("save_folder")
            if save_folder:
                try:
                    self.delete_save_game(save_folder)
                    info(f"CLEANUP: Deleted old save: {save_folder}", category="save_game")
                except Exception as e:
                    warning(f"CLEANUP: Failed to delete old save {save_folder}: {e}", category="save_game")

# Singleton instance for the multiplayer server
multiplayer_save_manager = None

def get_multiplayer_save_manager() -> MultiplayerSaveManager:
    """Get or create the singleton multiplayer save manager"""
    global multiplayer_save_manager
    if multiplayer_save_manager is None:
        multiplayer_save_manager = MultiplayerSaveManager()
    return multiplayer_save_manager