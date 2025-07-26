"""
Comprehensive Inventory Management System
Multi-layered recognition and verification pipeline for ensuring items get properly added to character inventories.
"""

import json
import re
import time
from typing import Dict, List, Any, Optional, Tuple, Set
from pathlib import Path
from utils.enhanced_logger import debug, info, warning, error, set_script_name
from utils.file_operations import safe_read_json, safe_write_json
from utils.encoding_utils import safe_json_load, safe_json_dump, sanitize_text
# AI functionality imported from server when needed

set_script_name("inventory_manager")

class InventoryManager:
    """
    Comprehensive inventory management system with multi-layered verification.
    
    Architecture:
    Layer 1: Enhanced Primary Detection - Expanded pattern recognition
    Layer 2: Smart JSON Generation - Robust item processing
    Layer 3: Verification Engine - Post-action checking
    Layer 4: AI-Powered Fallback - GPT analysis when verification fails
    Layer 5: Re-checking with Loop Prevention - Retry logic with limits
    """
    
    def __init__(self):
        self.max_retries = 3
        self.retry_delays = [1, 2, 4]  # Exponential backoff
        self.processed_responses = set()  # Loop prevention
        
        # Enhanced detection patterns
        self.inventory_patterns = [
            # Direct inventory operations
            r"add(?:ed|ing|s)?.*?(?:to|into).*?inventory",
            r"put(?:ting|s)?.*?(?:in|into).*?inventory",
            r"place(?:d|s)?.*?(?:in|into).*?inventory",
            r"store(?:d|s)?.*?(?:in|into).*?inventory",
            r"stow(?:ed|ing|s)?.*?(?:in|into|away)",
            
            # Inventory status updates
            r"inventory.*?(?:now|contains|has|includes)",
            r"(?:your|the).*?inventory.*?(?:update|change)",
            r"\*\*inventory.*?update\*\*",
            r"inventory.*?:",
            
            # Item acquisition
            r"pick(?:ed|ing|s)?.*?up",
            r"gather(?:ed|ing|s)?.*?(?:up|from)",
            r"collect(?:ed|ing|s)?.*?(?:from|up)",
            r"find(?:s|ing)?.*?(?:and|then).*?(?:take|get|pick)",
            r"obtain(?:ed|ing|s)?",
            r"acquire(?:d|s)?",
            r"receive(?:d|s)?",
            r"gain(?:ed|ing|s)?",
            r"get(?:ting|s)?.*?(?:from|off|out)",
            
            # Item results/rewards
            r"(?:you|they).*?(?:now have|have gained|have found)",
            r"(?:the|your).*?(?:contains|holds|has)",
            r"(?:inside|within).*?(?:you find|there (?:is|are))",
            
            # Natural mentions
            r"(?:some|several|many|few|a bunch of|pieces of)",
            r"(?:wood|branches|twigs|logs|timber|kindling)",
            r"(?:stone|rocks|ore|gems|crystals)",
            r"(?:food|rations|supplies|provisions)",
            r"(?:rope|cloth|leather|fabric|materials)",
        ]
        
        # Item extraction patterns (ordered by priority)
        self.item_patterns = [
            # Structured formats
            r"\*\*([^*]+?)\s*\(x?(\d+)\)\*\*",  # **Item (x5)**
            r"[-•*]\s*\*?([^*\n(]+?)\*?\s*\(x?(\d+)\)",  # - Item (x5)
            r"[-•*]\s*\*?([^*\n]+?)\*?\s*x\s*(\d+)",  # - Item x5
            r"(\d+)\s*x?\s*([^,\n]+?)(?:\.|,|$)",  # 5x Item or 5 Item
            
            # Natural language
            r"(?:some|several|many|few|a bunch of|pieces of)\s+(\w+(?:\s+\w+)*)",
            r"(?:you find|you see|there (?:is|are))\s+(?:some\s+)?(\w+(?:\s+\w+)*)",
            r"(?:pick up|gather|collect|take|get)\s+(?:some\s+)?(\w+(?:\s+\w+)*)",
            
            # Simple mentions with implied quantity
            r"[-•*]\s*([^-•*\n]+?)(?:\.|,|$)",  # Simple bullet points
        ]
        
        # Item type categorization
        self.item_categories = {
            "weapon": ["sword", "dagger", "axe", "mace", "spear", "bow", "crossbow", "staff", "wand", "blade"],
            "armor": ["armor", "shield", "helm", "helmet", "gauntlet", "boots", "plate", "mail", "leather"],
            "consumable": ["potion", "elixir", "vial", "flask", "food", "ration", "bread", "water", "ale", "wine"],
            "currency": ["gold", "silver", "copper", "coin", "gp", "sp", "cp", "platinum", "electrum"],
            "material": ["wood", "twig", "branch", "log", "timber", "kindling", "stone", "rock", "ore", "gem", 
                        "crystal", "rope", "cloth", "leather", "fabric", "iron", "steel", "bronze"],
            "tool": ["hammer", "chisel", "saw", "knife", "rope", "torch", "lantern", "shovel", "pickaxe"],
            "misc": ["book", "scroll", "map", "key", "ring", "amulet", "pendant", "bag", "pouch", "container"]
        }

    def detect_inventory_scenario(self, text: str, user_message: str = "") -> bool:
        """
        Layer 1: Enhanced detection of inventory scenarios
        """
        combined_text = f"{user_message} {text}".lower()
        
        # Check for explicit inventory patterns
        for pattern in self.inventory_patterns:
            if re.search(pattern, combined_text, re.IGNORECASE | re.MULTILINE):
                debug(f"Inventory pattern matched: {pattern}", category="inventory_detection")
                return True
        
        # Check for item-related context
        item_keywords = ["wood", "branch", "stone", "rock", "herb", "potion", "coin", "gold", "silver"]
        action_keywords = ["find", "search", "look", "gather", "collect", "take", "pick"]
        
        has_item = any(keyword in combined_text for keyword in item_keywords)
        has_action = any(keyword in combined_text for keyword in action_keywords)
        
        if has_item and has_action:
            debug("Item + action context detected", category="inventory_detection")
            return True
            
        return False

    def extract_items_comprehensive(self, text: str, user_message: str = "") -> List[Dict[str, Any]]:
        """
        Layer 2: Comprehensive item extraction with smart categorization
        """
        items = []
        processed_items = set()  # Avoid duplicates
        
        # Focus on the AI response text for item extraction
        lines = text.split('\n')
        
        # First pass: Look for structured bullet points and explicit formats
        for line in lines:
            line = line.strip()
            if not line or len(line) < 5:
                continue
                
            # Skip non-item lines
            if any(skip in line.lower() for skip in ["you decide", "you might", "you could", "what would", "continue to", "inventory update", "successfully gather"]):
                continue
            
            # Look for bullet points with items
            if line.startswith(('-', '•', '*')):
                item_data = self._extract_from_bullet_line(line)
                if item_data and item_data["name"].lower() not in processed_items:
                    items.append(item_data)
                    processed_items.add(item_data["name"].lower())
            
            # Look for **Item (x5)** format
            asterisk_matches = re.finditer(r'\*\*([^*]+?)\s*\(x?(\d+)\)\*\*', line)
            for match in asterisk_matches:
                item_name = match.group(1).strip()
                quantity = match.group(2)
                if len(item_name) > 2 and item_name.lower() not in processed_items:
                    items.append({
                        "name": item_name,
                        "quantity": quantity,
                        "type": self._categorize_item(item_name),
                        "description": "Found during adventure"
                    })
                    processed_items.add(item_name.lower())
        
        # Clean and validate items
        validated_items = []
        for item in items:
            if self._validate_item_strict(item):
                validated_items.append(item)
        
        debug(f"Extracted {len(validated_items)} items: {[item['name'] for item in validated_items]}", 
              category="item_extraction")
        
        return validated_items

    def _extract_from_bullet_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Extract item from a bullet point line"""
        # Remove bullet point
        clean_line = re.sub(r'^[-•*]\s*', '', line).strip()
        
        # Skip empty lines
        if not clean_line:
            return None
            
        # Look for quantity patterns
        quantity = "1"
        
        # Pattern: Item x5 or Item (x5)
        qty_match = re.search(r'(.+?)\s*(?:\(x?(\d+)\)|x\s*(\d+))', clean_line)
        if qty_match:
            item_name = qty_match.group(1).strip()
            quantity = qty_match.group(2) or qty_match.group(3) or "1"
        else:
            # No explicit quantity, extract numbers if any
            numbers = re.findall(r'\b\d+\b', clean_line)
            if numbers:
                quantity = numbers[-1]  # Use last number found
                # Remove the number from item name
                item_name = re.sub(r'\s*\b' + quantity + r'\b\s*', ' ', clean_line).strip()
            else:
                item_name = clean_line
        
        # Clean up item name
        item_name = re.sub(r'[*_]+', '', item_name).strip()
        item_name = re.sub(r'\s+', ' ', item_name)
        
        if len(item_name) < 2:
            return None
            
        return {
            "name": item_name,
            "quantity": quantity,
            "type": self._categorize_item(item_name),
            "description": "Found during adventure"
        }

    def _validate_item_strict(self, item: Dict[str, Any]) -> bool:
        """Strict validation for items"""
        name = item.get("name", "").strip()
        
        # Must have a reasonable name
        if len(name) < 2:
            return False
        
        # Filter out unwanted terms
        invalid_terms = [
            "you", "the", "and", "with", "from", "into", "that", "this", "what", "where", "when", "how",
            "these", "materials", "should", "be", "useful", "for", "making", "fire", "later",
            "time", "gathering", "forest", "floor", "searching", "carefully", "manage", "collect",
            "successfully", "gather", "following", "now", "added", "inventory"
        ]
        
        name_words = name.lower().split()
        if any(word in invalid_terms for word in name_words):
            return False
        
        # Must contain at least one material/item keyword
        valid_keywords = [
            "wood", "branch", "twig", "log", "timber", "kindling",
            "stone", "rock", "ore", "gem", "crystal",
            "herb", "plant", "flower", "leaf", "root",
            "coin", "gold", "silver", "copper", "platinum",
            "rope", "cloth", "leather", "fabric", "iron", "steel",
            "potion", "vial", "flask", "scroll", "book", "map"
        ]
        
        has_valid_keyword = any(keyword in name.lower() for keyword in valid_keywords)
        if not has_valid_keyword:
            return False
        
        return True

    def _process_pattern_match(self, match: re.Match, pattern: str) -> Optional[Dict[str, Any]]:
        """Process a regex match to extract item data"""
        groups = match.groups()
        
        if len(groups) >= 2:
            # Pattern with quantity
            if groups[1].isdigit():
                item_name, quantity = groups[0].strip(), groups[1]
            else:
                item_name, quantity = groups[1].strip(), groups[0]
        else:
            # Pattern without quantity
            item_name = groups[0].strip()
            quantity = "1"
        
        # Clean item name
        item_name = re.sub(r'[*_\-•]', '', item_name).strip()
        item_name = re.sub(r'\s+', ' ', item_name)
        
        if len(item_name) < 2:
            return None
            
        return {
            "name": item_name,
            "quantity": quantity,
            "type": self._categorize_item(item_name),
            "description": f"Found during adventure"
        }

    def _extract_contextual_items(self, text: str) -> List[Dict[str, Any]]:
        """Extract items based on context clues"""
        items = []
        text_lower = text.lower()
        
        # Look for material mentions
        for category, keywords in self.item_categories.items():
            for keyword in keywords:
                if keyword in text_lower:
                    # Extract quantity if present
                    quantity_match = re.search(rf"(\d+).*?{keyword}|{keyword}.*?(\d+)", text_lower)
                    quantity = "1"
                    if quantity_match:
                        quantity = quantity_match.group(1) or quantity_match.group(2) or "1"
                    
                    # Use contextual description
                    if "small" in text_lower or "tiny" in text_lower:
                        item_name = f"Small {keyword.title()}"
                    elif "large" in text_lower or "big" in text_lower:
                        item_name = f"Large {keyword.title()}"
                    elif "dry" in text_lower and keyword in ["wood", "branch", "twig"]:
                        item_name = f"Dry {keyword.title()}"
                    else:
                        item_name = keyword.title()
                    
                    items.append({
                        "name": item_name,
                        "quantity": quantity,
                        "type": category,
                        "description": f"Found during adventure"
                    })
                    break  # Only one item per category per line
        
        return items

    def _categorize_item(self, item_name: str) -> str:
        """Categorize an item based on its name"""
        item_lower = item_name.lower()
        
        for category, keywords in self.item_categories.items():
            if any(keyword in item_lower for keyword in keywords):
                return category
        
        return "item"  # Default category

    def _validate_item(self, item: Dict[str, Any]) -> bool:
        """Validate that an item makes sense"""
        name = item.get("name", "").strip()
        quantity = item.get("quantity", "1")
        
        # Basic validation
        if len(name) < 2:
            return False
        
        # Quantity validation
        try:
            int(quantity)
        except (ValueError, TypeError):
            item["quantity"] = "1"
        
        # Filter out non-items
        invalid_terms = ["you", "the", "and", "with", "from", "into", "that", "this", "what", "where", "when", "how"]
        if name.lower() in invalid_terms:
            return False
        
        return True

    def generate_inventory_action(self, items: List[Dict[str, Any]], character_name: str, 
                                 original_narration: str) -> Dict[str, Any]:
        """
        Layer 2: Generate proper JSON action for inventory updates
        """
        if not items:
            return None
        
        # Build changes description using the correct format for the AI system
        # The system expects "Added X to equipment" not "Added X to inventory"
        changes_parts = []
        for item in items:
            quantity_str = f" ({item['quantity']})" if item['quantity'] != "1" else ""
            # Use "equipment" instead of "inventory" to match the character schema
            changes_parts.append(f"Added {item['name']}{quantity_str} to equipment")
        
        changes = "; ".join(changes_parts)
        
        action_data = {
            "narration": original_narration,
            "actions": [
                {
                    "action": "updateCharacterInfo",
                    "parameters": {
                        "characterName": character_name,
                        "changes": changes
                    }
                }
            ]
        }
        
        debug(f"Generated inventory action for {character_name}: {changes}", category="json_generation")
        return action_data

    def get_character_inventory_snapshot(self, character_name: str) -> Dict[str, Any]:
        """
        Layer 3: Get current character inventory for comparison
        """
        try:
            char_file = f"characters/{character_name.lower()}.json"
            char_data = safe_read_json(char_file)
            
            # The character schema uses 'equipment' field, not 'inventory'
            # Also check for legacy 'inventory' field for backwards compatibility
            equipment = []
            if char_data:
                if "equipment" in char_data:
                    equipment = char_data["equipment"]
                elif "inventory" in char_data:
                    # Legacy support: convert inventory to equipment format
                    inventory = char_data["inventory"]
                    if isinstance(inventory, dict) and "items" in inventory:
                        equipment = inventory["items"]
            
            return {"equipment": equipment}
        except Exception as e:
            warning(f"Could not read character equipment for {character_name}: {e}", category="inventory_verification")
            return {"equipment": []}

    def verify_inventory_update(self, character_name: str, expected_items: List[Dict[str, Any]], 
                               pre_inventory: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Layer 3: Verify that items were actually added to character equipment
        """
        try:
            post_inventory = self.get_character_inventory_snapshot(character_name)
            
            missing_items = []
            for expected_item in expected_items:
                found = False
                
                # Check in equipment list (schema format)
                for item in post_inventory.get("equipment", []):
                    # Check both 'item_name' (schema format) and 'name' (legacy format)
                    item_name = item.get("item_name", "") or item.get("name", "")
                    if expected_item["name"].lower() in item_name.lower():
                        found = True
                        break
                
                if not found:
                    missing_items.append(expected_item["name"])
            
            success = len(missing_items) == 0
            debug(f"Equipment verification for {character_name}: Success={success}, Missing={missing_items}", 
                  category="inventory_verification")
            
            return success, missing_items
            
        except Exception as e:
            error(f"Error verifying equipment update: {e}", category="inventory_verification")
            return False, [item["name"] for item in expected_items]

    def ai_fallback_analysis(self, original_response: str, user_message: str, 
                           character_name: str) -> Optional[Dict[str, Any]]:
        """
        Layer 4: AI-powered fallback analysis when normal extraction fails
        """
        try:
            from openai import OpenAI
            from config import OPENAI_API_KEY, DM_MAIN_MODEL
            
            client = OpenAI(api_key=OPENAI_API_KEY)
            
            fallback_prompt = f"""Analyze this conversation and identify items that should be added to the character's equipment.

User said: "{user_message}"
Game response: "{original_response}"

If any items should be added to {character_name}'s equipment based on what happened, respond with ONLY a JSON object:
{{
  "items": [
    {{"name": "Item Name", "quantity": "number", "description": "brief description"}}
  ]
}}

If no items should be added, respond with: {{"items": []}}

Focus on actual items mentioned that the character obtained, gathered, picked up, or put in inventory.
"""
            
            response = client.chat.completions.create(
                model=DM_MAIN_MODEL,
                messages=[{"role": "user", "content": fallback_prompt}],
                temperature=0.1,
                max_tokens=300
            )
            
            ai_response = response.choices[0].message.content.strip()
            info(f"AI fallback analysis response: {ai_response}", category="ai_fallback")
            
            # Parse the AI response
            analysis = json.loads(ai_response)
            items_found = analysis.get("items", [])
            
            if items_found:
                info(f"AI fallback found {len(items_found)} items", category="ai_fallback")
                return self.generate_inventory_action(items_found, character_name, original_response)
            else:
                debug("AI fallback found no items", category="ai_fallback")
                return None
                
        except Exception as e:
            error(f"AI fallback analysis failed: {e}", category="ai_fallback")
            return None

    def process_response_with_verification(self, ai_response: str, character_name: str, 
                                         user_message: str) -> Tuple[str, bool]:
        """
        Main entry point: Process AI response with full multi-layer verification and FORCING
        """
        # Generate unique ID for this response to prevent loops
        response_id = hash(f"{ai_response[:100]}{character_name}{user_message[:50]}")
        
        if response_id in self.processed_responses:
            debug("Response already processed, skipping to prevent loops", category="loop_prevention")
            return ai_response, False
        
        self.processed_responses.add(response_id)
        
        try:
            # Check if already valid JSON
            try:
                parsed = json.loads(ai_response)
                if isinstance(parsed, dict) and "narration" in parsed:
                    debug("Response already in valid JSON format", category="main_processor")
                    return ai_response, False
            except json.JSONDecodeError:
                pass
            
            # Layer 1: Detect inventory scenario
            if not self.detect_inventory_scenario(ai_response, user_message):
                debug("No inventory scenario detected", category="main_processor")
                return ai_response, False
            
            info(f"Processing inventory scenario for {character_name}", category="main_processor")
            
            # Get pre-action inventory snapshot
            pre_inventory = self.get_character_inventory_snapshot(character_name)
            
            # Layer 2: Extract items and generate action
            items = self.extract_items_comprehensive(ai_response, user_message)
            
            # Layer 3: If no items found, FORCE AI analysis
            if not items:
                warning(f"No items extracted normally, using AI fallback for {character_name}", category="main_processor")
                fallback_action = self.ai_fallback_analysis(ai_response, user_message, character_name)
                if fallback_action:
                    fixed_response = json.dumps(fallback_action, indent=2)
                    info(f"AI fallback generated action for {character_name}", category="main_processor")
                    return fixed_response, True
                else:
                    debug("AI fallback also found no items", category="main_processor")
                    return ai_response, False
            
            # Generate JSON action
            action_data = self.generate_inventory_action(items, character_name, ai_response)
            if not action_data:
                return ai_response, False
            
            # Convert to JSON string
            fixed_response = json.dumps(action_data, indent=2)
            
            info(f"Generated inventory update for {character_name} with {len(items)} items", 
                 category="main_processor")
            
            return fixed_response, True
            
        except Exception as e:
            error(f"Error in inventory processing: {e}", category="main_processor")
            return ai_response, False

    def cleanup_processed_responses(self):
        """Clean up processed responses to prevent memory buildup"""
        if len(self.processed_responses) > 1000:
            # Keep only the most recent 500
            self.processed_responses = set(list(self.processed_responses)[-500:])
            debug("Cleaned up processed responses cache", category="maintenance")


# Convenience function for easy integration
def process_inventory_response(ai_response: str, character_name: str, user_message: str) -> Tuple[str, bool]:
    """
    Main entry point for inventory processing
    
    Args:
        ai_response: The AI's response text
        character_name: The character's name
        user_message: The user's original message
        
    Returns:
        Tuple of (processed_response, was_modified)
    """
    manager = InventoryManager()
    return manager.process_response_with_verification(ai_response, character_name, user_message)