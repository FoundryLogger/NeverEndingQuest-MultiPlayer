import json
import os
from campaign_path_manager import CampaignPathManager

# CR to XP mapping (updated to include fractional CRs)
cr_to_xp = {
    0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700, 4: 1100, 5: 1800,
    6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900, 11: 7200, 12: 8400, 13: 10000,
    14: 11500, 15: 13000, 16: 15000, 17: 18000, 18: 20000, 19: 22000, 20: 25000,
    21: 33000, 22: 41000, 23: 50000, 24: 62000, 25: 75000, 26: 90000, 27: 105000,
    28: 120000, 29: 135000, 30: 155000
}

def load_json_file(filename):
    with open(filename, 'r') as file:
        return json.load(file)

def get_xp_for_cr(cr):
    if isinstance(cr, str):
        if '/' in cr:
            numerator, denominator = map(int, cr.split('/'))
            cr = numerator / denominator
        else:
            cr = float(cr)
    return cr_to_xp.get(cr, 0)  # Return 0 if CR not found in the dictionary

def is_defeated(status):
    return status.lower() in ['dead', 'defeated', 'unconscious']

def calculate_xp(encounter, party_tracker):
    total_xp = 0
    defeated_count = 0
    monster_cache = {}
    xp_breakdown = []
    path_manager = CampaignPathManager()

    for creature in encounter['creatures']:
        if creature['type'] == 'enemy' and is_defeated(creature['status']):
            defeated_count += 1
            monster_type = creature['monsterType'].lower()
            if monster_type not in monster_cache:
                monster_file = path_manager.get_monster_path(monster_type)
                if os.path.exists(monster_file):
                    monster = load_json_file(monster_file)
                    monster_cache[monster_type] = monster
                else:
                    print(f"Warning: Monster file {monster_file} not found.")
                    continue
            
            monster = monster_cache[monster_type]
            cr = monster['challengeRating']
            xp = get_xp_for_cr(cr)
            total_xp += xp
            xp_breakdown.append((monster['name'], cr, xp))

    # Count player characters and party NPCs
    num_players = len(party_tracker['partyMembers'])
    num_npcs = len(party_tracker['partyNPCs'])
    total_participants = num_players + num_npcs

    return total_xp, defeated_count, xp_breakdown, total_participants

def main():
    # Load party tracker
    party_tracker = load_json_file('party_tracker.json')

    # Get the active encounter ID
    active_encounter_id = party_tracker['worldConditions']['activeCombatEncounter']
    
    if not active_encounter_id:
        return "No active combat encounter found.", 0

    # Load the active encounter
    encounter_filename = f"encounter_{active_encounter_id}.json"
    if not os.path.exists(encounter_filename):
        return f"Encounter file {encounter_filename} not found.", 0

    encounter = load_json_file(encounter_filename)

    # Calculate XP
    total_xp, defeated_count, xp_breakdown, total_participants = calculate_xp(encounter, party_tracker)

    if total_participants == 0:
        return "No players or party NPCs found.", 0

    # Create a single narrative sentence
    player_names = ", ".join(party_tracker['partyMembers'])
    npc_names = ", ".join([npc['name'] for npc in party_tracker['partyNPCs']])
    all_participants = f"{player_names} and their companions {npc_names}" if npc_names else player_names

    if defeated_count == 0:
        narrative = f"{all_participants} faced enemies in an encounter at {encounter['encounterSummary']}, but no experience was awarded as no monsters were defeated."
        xp_awarded = 0
    else:
        xp_per_participant = total_xp // total_participants
        enemy_description = f"{defeated_count} enemy" if defeated_count == 1 else f"{defeated_count} enemies"
        xp_details = ", ".join([f"{monster} (CR {cr}, {xp} XP)" for monster, cr, xp in xp_breakdown])
        
        narrative = f"{all_participants} defeated {enemy_description} ({xp_details}) in an encounter at {encounter['encounterSummary']}, "
        narrative += f"resulting in a total of {total_xp} XP, which was divided among {total_participants} participant(s) "
        narrative += f"({len(party_tracker['partyMembers'])} player(s) and {len(party_tracker['partyNPCs'])} party NPC(s)), "
        narrative += f"awarding each participant {xp_per_participant} experience points."
        xp_awarded = xp_per_participant

    return narrative, xp_awarded

if __name__ == "__main__":
    narrative, xp = main()
    print(narrative)
    print(f"XP awarded per participant: {xp}")