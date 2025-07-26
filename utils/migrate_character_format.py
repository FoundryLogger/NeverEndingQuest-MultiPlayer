#!/usr/bin/env python3
"""
Character Format Migration Utility
Converts old character files to match the current schema requirements
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, List
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.file_operations import safe_read_json, safe_write_json
from utils.enhanced_logger import debug, info, warning, error, set_script_name

set_script_name("character_migration")

def migrate_inventory_to_equipment(inventory: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Convert old inventory format to new equipment format
    
    Old format:
    "inventory": {
        "items": [{"name": "Item", "type": "item", "description": "..."}],
        "weapons": [...],
        "armor": [...],
        "money": {...}
    }
    
    New format:
    "equipment": [{"item_name": "Item", "item_type": "miscellaneous", "description": "...", "quantity": 1}]
    """
    equipment = []
    
    if not isinstance(inventory, dict):
        return equipment
    
    # Convert items
    for item in inventory.get("items", []):
        equipment_item = {
            "item_name": item.get("name", "Unknown Item"),
            "item_type": map_item_type(item.get("type", "item")),
            "description": item.get("description", ""),
            "quantity": item.get("quantity", 1),
            "equipped": False,
            "magical": False
        }
        equipment.append(equipment_item)
    
    # Convert weapons
    for weapon in inventory.get("weapons", []):
        equipment_item = {
            "item_name": weapon.get("name", "Unknown Weapon"),
            "item_type": "weapon",
            "description": weapon.get("description", ""),
            "quantity": weapon.get("quantity", 1),
            "equipped": weapon.get("equipped", False),
            "magical": weapon.get("magical", False),
            "damage": weapon.get("damage", ""),
            "damage_type": weapon.get("damage_type", "")
        }
        equipment.append(equipment_item)
    
    # Convert armor
    for armor in inventory.get("armor", []):
        equipment_item = {
            "item_name": armor.get("name", "Unknown Armor"),
            "item_type": "armor",
            "description": armor.get("description", ""),
            "quantity": armor.get("quantity", 1),
            "equipped": armor.get("equipped", False),
            "magical": armor.get("magical", False),
            "ac_bonus": armor.get("ac_bonus", 0)
        }
        equipment.append(equipment_item)
    
    return equipment

def map_item_type(old_type: str) -> str:
    """Map old item types to schema-compliant types"""
    type_mapping = {
        "item": "miscellaneous",
        "consumable": "consumable",
        "weapon": "weapon",
        "armor": "armor",
        "material": "miscellaneous",
        "container": "equipment",
        "tool": "equipment"
    }
    return type_mapping.get(old_type, "miscellaneous")

def migrate_personality_fields(personality: Dict[str, Any], char_data: Dict[str, Any]) -> None:
    """
    Convert old personality object to individual fields
    
    Old format:
    "personality": {
        "traits": "...",
        "ideals": "...",
        "bonds": "...",
        "flaws": "..."
    }
    
    New format:
    "personality_traits": "...",
    "ideals": "...",
    "bonds": "...",
    "flaws": "..."
    """
    if not isinstance(personality, dict):
        return
    
    if "traits" in personality:
        char_data["personality_traits"] = personality["traits"]
    if "ideals" in personality:
        char_data["ideals"] = personality["ideals"]
    if "bonds" in personality:
        char_data["bonds"] = personality["bonds"]
    if "flaws" in personality:
        char_data["flaws"] = personality["flaws"]

def add_missing_required_fields(char_data: Dict[str, Any]) -> None:
    """Add any missing required fields with sensible defaults"""
    
    # Add temporaryEffects if missing
    if "temporaryEffects" not in char_data:
        char_data["temporaryEffects"] = []
        info("Added missing temporaryEffects field", category="migration")
    
    # Add equipment if missing (and no inventory to convert)
    if "equipment" not in char_data and "inventory" not in char_data:
        char_data["equipment"] = []
        info("Added missing equipment field", category="migration")
    
    # Add currency if missing
    if "currency" not in char_data:
        # Check if money was in inventory
        if "inventory" in char_data and "money" in char_data["inventory"]:
            money = char_data["inventory"]["money"]
            char_data["currency"] = {
                "copper": money.get("copper", 0),
                "silver": money.get("silver", 0),
                "electrum": money.get("electrum", 0),
                "gold": money.get("gold", 0),
                "platinum": money.get("platinum", 0)
            }
        else:
            char_data["currency"] = {
                "copper": 0,
                "silver": 0,
                "electrum": 0,
                "gold": 0,
                "platinum": 0
            }
        info("Added currency field", category="migration")
    
    # Add other commonly missing fields
    if "equipment_effects" not in char_data:
        char_data["equipment_effects"] = []
    
    if "injuries" not in char_data:
        char_data["injuries"] = []
    
    if "feats" not in char_data:
        char_data["feats"] = []
    
    if "attacksAndSpellcasting" not in char_data:
        char_data["attacksAndSpellcasting"] = []
    
    if "racialTraits" not in char_data:
        char_data["racialTraits"] = []
        info("Added missing racialTraits field", category="migration")
    
    if "backgroundFeature" not in char_data:
        char_data["backgroundFeature"] = {
            "name": "Background Feature",
            "description": "Feature from character background"
        }
        info("Added missing backgroundFeature field", category="migration")
    
    if "exp_required_for_next_level" not in char_data:
        # Calculate based on level
        level = char_data.get("level", 1)
        xp_thresholds = {1: 300, 2: 900, 3: 2700, 4: 6500, 5: 14000}
        char_data["exp_required_for_next_level"] = xp_thresholds.get(level, 300)
        info("Added missing exp_required_for_next_level field", category="migration")

def migrate_character_file(filepath: str) -> bool:
    """
    Migrate a single character file to the current schema format
    
    Returns True if migration was successful, False otherwise
    """
    try:
        # Read the character file
        char_data = safe_read_json(filepath)
        if not char_data:
            error(f"Could not read character file: {filepath}", category="migration")
            return False
        
        info(f"Migrating character: {char_data.get('name', 'Unknown')}", category="migration")
        
        # Track if any changes were made
        changes_made = False
        
        # Convert inventory to equipment
        if "inventory" in char_data:
            info("Converting inventory to equipment format", category="migration")
            equipment = migrate_inventory_to_equipment(char_data["inventory"])
            
            # If equipment already exists, merge the items
            if "equipment" in char_data:
                char_data["equipment"].extend(equipment)
            else:
                char_data["equipment"] = equipment
            
            # Remove the old inventory field
            del char_data["inventory"]
            changes_made = True
        
        # Convert personality to individual fields
        if "personality" in char_data:
            info("Converting personality to individual fields", category="migration")
            migrate_personality_fields(char_data["personality"], char_data)
            del char_data["personality"]
            changes_made = True
        
        # Add missing required fields
        old_keys = set(char_data.keys())
        add_missing_required_fields(char_data)
        if set(char_data.keys()) != old_keys:
            changes_made = True
        
        # Save the migrated character file
        if changes_made:
            # Create backup
            backup_path = f"{filepath}.pre-migration.bak"
            safe_write_json(backup_path, safe_read_json(filepath))
            info(f"Created backup at: {backup_path}", category="migration")
            
            # Save migrated data
            safe_write_json(filepath, char_data)
            info(f"Successfully migrated character file: {filepath}", category="migration")
            return True
        else:
            info(f"No migration needed for: {filepath}", category="migration")
            return True
            
    except Exception as e:
        error(f"Error migrating character file {filepath}: {e}", category="migration")
        return False

def migrate_all_characters(characters_dir: str = "characters") -> None:
    """Migrate all character files in the characters directory"""
    
    info(f"Starting character migration in: {characters_dir}", category="migration")
    
    success_count = 0
    failure_count = 0
    
    # Process all JSON files in the characters directory
    for filename in os.listdir(characters_dir):
        if filename.endswith(".json") and not filename.endswith(".bak"):
            filepath = os.path.join(characters_dir, filename)
            if migrate_character_file(filepath):
                success_count += 1
            else:
                failure_count += 1
    
    info(f"Migration complete: {success_count} successful, {failure_count} failed", category="migration")

if __name__ == "__main__":
    # Run migration on all character files
    migrate_all_characters()
    
    # Also specifically ensure Exurgodor is migrated
    exurgodor_path = "characters/exurgodor.json"
    if os.path.exists(exurgodor_path):
        info("Ensuring Exurgodor is properly migrated...", category="migration")
        migrate_character_file(exurgodor_path)