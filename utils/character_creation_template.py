#!/usr/bin/env python3
"""
Character Creation Template
Provides schema-compliant character templates for new character creation
"""

def get_default_character_template(name="Adventurer", character_class="Fighter", race="Human", 
                                  background="Folk Hero", abilities=None):
    """
    Get a schema-compliant character template
    
    Args:
        name: Character name
        character_class: Character class (Fighter, Wizard, Rogue, etc.)
        race: Character race
        background: Character background
        abilities: Dictionary of ability scores (defaults to standard array)
    
    Returns:
        Dictionary with schema-compliant character data
    """
    if abilities is None:
        abilities = {
            "strength": 15,
            "dexterity": 13,
            "constitution": 14,
            "intelligence": 12,
            "wisdom": 10,
            "charisma": 8
        }
    
    # Calculate HP based on class
    class_hp = {
        'Fighter': 10, 'Paladin': 10, 'Ranger': 10, 'Barbarian': 12,
        'Bard': 8, 'Cleric': 8, 'Druid': 8, 'Monk': 8, 'Rogue': 8, 'Warlock': 8,
        'Sorcerer': 6, 'Wizard': 6
    }
    
    base_hp = class_hp.get(character_class, 8)
    con_mod = (abilities['constitution'] - 10) // 2
    max_hp = base_hp + con_mod
    
    # Base template with all required fields
    character_template = {
        "character_role": "player",
        "character_type": "player",
        "name": name,
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
        "skills": {},
        "proficiencyBonus": 2,
        "senses": {
            "darkvision": 0,
            "passivePerception": 10 + (abilities['wisdom'] - 10) // 2
        },
        "languages": ["Common"],
        "proficiencies": {
            "armor": [],
            "weapons": [],
            "tools": []
        },
        "damageVulnerabilities": [],
        "damageResistances": [],
        "damageImmunities": [],
        "conditionImmunities": [],
        "experience_points": 0,
        "classFeatures": [],
        "racialTraits": [],
        "backgroundFeature": {},
        "temporaryEffects": [],  # REQUIRED FIELD
        "injuries": [],
        "equipment_effects": [],
        "feats": [],
        "equipment": [],  # NEW SCHEMA FORMAT
        "attacksAndSpellcasting": [],
        "currency": {  # NEW SCHEMA FORMAT
            "copper": 0,
            "silver": 0,
            "electrum": 0,
            "gold": 10,
            "platinum": 0
        },
        "personality_traits": "I stand up for what I believe in.",  # INDIVIDUAL FIELDS
        "ideals": "I fight for those who cannot fight for themselves.",
        "bonds": "I protect those who cannot protect themselves.",
        "flaws": "I have a weakness for the vices of the city.",
        "exp_required_for_next_level": 300
    }
    
    return character_template

def get_class_saving_throws(character_class):
    """Get saving throw proficiencies for a class"""
    saving_throws = {
        'Fighter': ['strength', 'constitution'],
        'Wizard': ['intelligence', 'wisdom'],
        'Rogue': ['dexterity', 'intelligence'],
        'Cleric': ['wisdom', 'charisma'],
        'Ranger': ['strength', 'dexterity'],
        'Paladin': ['wisdom', 'charisma'],
        'Barbarian': ['strength', 'constitution'],
        'Bard': ['dexterity', 'charisma'],
        'Druid': ['intelligence', 'wisdom'],
        'Monk': ['strength', 'dexterity'],
        'Sorcerer': ['constitution', 'charisma'],
        'Warlock': ['wisdom', 'charisma']
    }
    return saving_throws.get(character_class, ['strength', 'constitution'])

def get_starting_equipment(character_class, background):
    """Get starting equipment in NEW SCHEMA FORMAT"""
    # Basic equipment all characters get
    equipment = [
        {
            "item_name": "Backpack",
            "item_type": "equipment",
            "description": "A backpack can hold 1 cubic foot or 30 pounds of gear",
            "quantity": 1,
            "equipped": False,
            "magical": False
        },
        {
            "item_name": "Bedroll",
            "item_type": "miscellaneous",
            "description": "For sleeping outdoors",
            "quantity": 1,
            "equipped": False,
            "magical": False
        },
        {
            "item_name": "Rations (5 days)",
            "item_type": "consumable",
            "description": "Trail rations consist of jerky, dried fruit, hardtack, and nuts",
            "quantity": 5,
            "equipped": False,
            "magical": False
        }
    ]
    
    # Class-specific equipment
    if character_class == 'Fighter':
        equipment.extend([
            {
                "item_name": "Chain Mail",
                "item_type": "armor",
                "description": "Heavy armor, AC 16",
                "quantity": 1,
                "equipped": True,
                "magical": False,
                "ac_base": 16
            },
            {
                "item_name": "Longsword",
                "item_type": "weapon",
                "description": "Versatile weapon, 1d8 slashing damage",
                "quantity": 1,
                "equipped": True,
                "magical": False,
                "damage": "1d8",
                "damage_type": "slashing"
            },
            {
                "item_name": "Shield",
                "item_type": "armor",
                "description": "A shield grants +2 AC",
                "quantity": 1,
                "equipped": True,
                "magical": False,
                "ac_bonus": 2
            }
        ])
    elif character_class == 'Wizard':
        equipment.extend([
            {
                "item_name": "Spellbook",
                "item_type": "equipment",
                "description": "Essential for a wizard, contains your spells",
                "quantity": 1,
                "equipped": False,
                "magical": False
            },
            {
                "item_name": "Component Pouch",
                "item_type": "equipment",
                "description": "A small pouch containing spell components",
                "quantity": 1,
                "equipped": True,
                "magical": False
            },
            {
                "item_name": "Quarterstaff",
                "item_type": "weapon",
                "description": "Simple weapon, 1d6 bludgeoning damage",
                "quantity": 1,
                "equipped": False,
                "magical": False,
                "damage": "1d6",
                "damage_type": "bludgeoning"
            }
        ])
    elif character_class == 'Rogue':
        equipment.extend([
            {
                "item_name": "Leather Armor",
                "item_type": "armor",
                "description": "Light armor, AC 11 + Dex modifier",
                "quantity": 1,
                "equipped": True,
                "magical": False,
                "ac_base": 11
            },
            {
                "item_name": "Shortsword",
                "item_type": "weapon",
                "description": "Finesse weapon, 1d6 piercing damage",
                "quantity": 1,
                "equipped": True,
                "magical": False,
                "damage": "1d6",
                "damage_type": "piercing"
            },
            {
                "item_name": "Thieves' Tools",
                "item_type": "equipment",
                "description": "This set of tools includes a small file, lockpicks, and other tools",
                "quantity": 1,
                "equipped": False,
                "magical": False
            }
        ])
    
    return equipment

def convert_old_inventory_to_equipment(old_inventory):
    """
    Convert old inventory format to new equipment format
    For backward compatibility when loading old characters
    """
    equipment = []
    
    if isinstance(old_inventory, dict):
        # Convert items
        for item in old_inventory.get("items", []):
            equipment.append({
                "item_name": item.get("name", "Unknown Item"),
                "item_type": "miscellaneous",
                "description": item.get("description", ""),
                "quantity": item.get("quantity", 1),
                "equipped": False,
                "magical": False
            })
        
        # Convert weapons
        for weapon in old_inventory.get("weapons", []):
            equipment.append({
                "item_name": weapon.get("name", "Unknown Weapon"),
                "item_type": "weapon",
                "description": weapon.get("description", ""),
                "quantity": weapon.get("quantity", 1),
                "equipped": weapon.get("equipped", False),
                "magical": weapon.get("magical", False),
                "damage": weapon.get("damage", ""),
                "damage_type": weapon.get("damage_type", "")
            })
        
        # Convert armor
        for armor in old_inventory.get("armor", []):
            equipment.append({
                "item_name": armor.get("name", "Unknown Armor"),
                "item_type": "armor",
                "description": armor.get("description", ""),
                "quantity": armor.get("quantity", 1),
                "equipped": armor.get("equipped", False),
                "magical": armor.get("magical", False),
                "ac_base": armor.get("ac_base", 0),
                "ac_bonus": armor.get("ac_bonus", 0)
            })
    
    return equipment