#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0

"""
Systematic Comparison Between Single-Player (main.py) and Multiplayer (server.py)
This will identify exactly what functionality is missing from multiplayer
"""

import json
import os
from datetime import datetime

def load_analysis_data():
    """Load the analysis data for both files"""
    main_data = None
    server_data = None
    
    if os.path.exists('main_py_analysis_complete.json'):
        with open('main_py_analysis_complete.json', 'r', encoding='utf-8') as f:
            main_data = json.load(f)
    
    if os.path.exists('server_py_analysis_complete.json'):
        with open('server_py_analysis_complete.json', 'r', encoding='utf-8') as f:
            server_data = json.load(f)
    
    return main_data, server_data

def compare_functions(main_data, server_data):
    """Compare functions between main.py and server.py"""
    main_functions = {f['name']: f for f in main_data['functions']}
    server_functions = {f['name']: f for f in server_data['functions']}
    
    main_only = set(main_functions.keys()) - set(server_functions.keys())
    server_only = set(server_functions.keys()) - set(main_functions.keys())
    common = set(main_functions.keys()) & set(server_functions.keys())
    
    return {
        'main_only': main_only,
        'server_only': server_only,
        'common': common,
        'main_functions': main_functions,
        'server_functions': server_functions
    }

def compare_imports(main_data, server_data):
    """Compare imports between main.py and server.py"""
    main_imports = set()
    server_imports = set()
    
    for category in main_data['imports']:
        main_imports.update(main_data['imports'][category])
    
    for category in server_data['imports']:
        server_imports.update(server_data['imports'][category])
    
    main_only = main_imports - server_imports
    server_only = server_imports - main_imports
    common = main_imports & server_imports
    
    return {
        'main_only': main_only,
        'server_only': server_only,
        'common': common
    }

def compare_systems(main_data, server_data):
    """Compare system patterns between main.py and server.py"""
    comparison = {}
    
    # Get all system categories
    all_categories = set(main_data['systems'].keys()) | set(server_data['systems'].keys())
    
    for category in all_categories:
        main_items = set(main_data['systems'].get(category, []))
        server_items = set(server_data['systems'].get(category, []))
        
        comparison[category] = {
            'main_only': main_items - server_items,
            'server_only': server_items - main_items,
            'common': main_items & server_items
        }
    
    return comparison

def identify_critical_missing_functionality(comparison_data):
    """Identify the most critical missing functionality"""
    critical_missing = {
        'high_priority': [],
        'medium_priority': [],
        'low_priority': [],
        'multiplayer_specific': []
    }
    
    # Critical single-player functions not in multiplayer
    critical_functions = [
        'check_and_process_location_transitions',
        'check_and_inject_return_message',
        'generate_module_summary',
        'compress_conversation_history_on_module_transition',
        'main_game_loop',
        'get_npc_stat',
        'create_module_validation_context',
        'generate_arrival_narration',
        'generate_seamless_transition_narration'
    ]
    
    for func in critical_functions:
        if func in comparison_data['functions']['main_only']:
            if func in ['check_and_process_location_transitions', 'main_game_loop']:
                critical_missing['high_priority'].append({
                    'type': 'function',
                    'name': func,
                    'reason': 'Core game loop or location processing functionality'
                })
            elif func in ['check_and_inject_return_message', 'generate_module_summary']:
                critical_missing['medium_priority'].append({
                    'type': 'function',
                    'name': func,
                    'reason': 'Session management or narrative enhancement'
                })
            else:
                critical_missing['low_priority'].append({
                    'type': 'function',
                    'name': func,
                    'reason': 'Utility or enhancement function'
                })
    
    # Check for multiplayer-specific functionality
    multiplayer_functions = [
        'handle_join_game',
        'broadcast_full_game_state',
        'handle_player_action_logic',
        'handle_combat_action_logic'
    ]
    
    for func in multiplayer_functions:
        if func in comparison_data['functions']['server_only']:
            critical_missing['multiplayer_specific'].append({
                'type': 'function',
                'name': func,
                'reason': 'Multiplayer-specific functionality'
            })
    
    return critical_missing

def generate_detailed_report():
    """Generate the comprehensive comparison report"""
    print("Loading analysis data...")
    main_data, server_data = load_analysis_data()
    
    if not main_data or not server_data:
        print("Error: Missing analysis data files. Run the analysis scripts first.")
        return
    
    print("=" * 100)
    print("COMPREHENSIVE SINGLE-PLAYER vs MULTIPLAYER COMPARISON")
    print("=" * 100)
    print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Single-Player Functions: {len(main_data['functions'])}")
    print(f"Multiplayer Functions: {len(server_data['functions'])}")
    print(f"Single-Player Imports: {main_data['summary']['total_imports']}")
    print(f"Multiplayer Imports: {server_data['summary']['total_imports']}")
    
    # Compare functions
    print("\\n" + "=" * 60)
    print("FUNCTION COMPARISON")
    print("=" * 60)
    
    func_comparison = compare_functions(main_data, server_data)
    
    print(f"\\nFunctions ONLY in Single-Player (main.py): {len(func_comparison['main_only'])}")
    for func in sorted(func_comparison['main_only']):
        func_info = func_comparison['main_functions'][func]
        args_str = ', '.join(func_info['args']) if func_info['args'] else 'no args'
        print(f"  - {func}({args_str}) [line {func_info['line_number']}]")
    
    print(f"\\nFunctions ONLY in Multiplayer (server.py): {len(func_comparison['server_only'])}")
    for func in sorted(func_comparison['server_only']):
        func_info = func_comparison['server_functions'][func]
        args_str = ', '.join(func_info['args']) if func_info['args'] else 'no args'
        print(f"  - {func}({args_str}) [line {func_info['line_number']}]")
    
    print(f"\\nCommon Functions: {len(func_comparison['common'])}")
    
    # Compare imports
    print("\\n" + "=" * 60)
    print("IMPORT COMPARISON")
    print("=" * 60)
    
    import_comparison = compare_imports(main_data, server_data)
    
    print(f"\\nImports ONLY in Single-Player: {len(import_comparison['main_only'])}")
    for imp in sorted(import_comparison['main_only']):
        print(f"  - {imp}")
    
    print(f"\\nImports ONLY in Multiplayer: {len(import_comparison['server_only'])}")
    for imp in sorted(import_comparison['server_only']):
        print(f"  - {imp}")
    
    # Compare systems
    print("\\n" + "=" * 60)
    print("SYSTEM COMPARISON")
    print("=" * 60)
    
    system_comparison = compare_systems(main_data, server_data)
    
    for category, data in system_comparison.items():
        if data['main_only'] or data['server_only']:
            print(f"\\n{category.upper()}:")
            if data['main_only']:
                print(f"  Single-Player Only ({len(data['main_only'])}):")
                for item in sorted(data['main_only'])[:5]:  # Show first 5
                    print(f"    - {item}")
                if len(data['main_only']) > 5:
                    print(f"    ... and {len(data['main_only']) - 5} more")
            
            if data['server_only']:
                print(f"  Multiplayer Only ({len(data['server_only'])}):")  
                for item in sorted(data['server_only'])[:5]:  # Show first 5
                    print(f"    - {item}")
                if len(data['server_only']) > 5:
                    print(f"    ... and {len(data['server_only']) - 5} more")
    
    # Identify critical missing functionality
    print("\\n" + "=" * 60)
    print("CRITICAL MISSING FUNCTIONALITY ANALYSIS")
    print("=" * 60)
    
    critical_missing = identify_critical_missing_functionality({
        'functions': func_comparison,
        'imports': import_comparison,
        'systems': system_comparison
    })
    
    print("\\n[HIGH PRIORITY - MISSING FROM MULTIPLAYER]")
    for item in critical_missing['high_priority']:
        print(f"  - {item['name']} ({item['type']}): {item['reason']}")
    
    print("\\n[MEDIUM PRIORITY - MISSING FROM MULTIPLAYER]")
    for item in critical_missing['medium_priority']:
        print(f"  - {item['name']} ({item['type']}): {item['reason']}")
    
    print("\\n[LOW PRIORITY - MISSING FROM MULTIPLAYER]")
    for item in critical_missing['low_priority']:
        print(f"  - {item['name']} ({item['type']}): {item['reason']}")
    
    print("\\n[MULTIPLAYER-SPECIFIC FUNCTIONALITY]")
    for item in critical_missing['multiplayer_specific']:
        print(f"  - {item['name']} ({item['type']}): {item['reason']}")
    
    # Generate comprehensive report
    comprehensive_report = {
        'analysis_date': datetime.now().isoformat(),
        'summary': {
            'single_player_functions': len(main_data['functions']),
            'multiplayer_functions': len(server_data['functions']),
            'functions_only_in_single': len(func_comparison['main_only']),
            'functions_only_in_multiplayer': len(func_comparison['server_only']),
            'common_functions': len(func_comparison['common']),
            'imports_only_in_single': len(import_comparison['main_only']),
            'imports_only_in_multiplayer': len(import_comparison['server_only']),
            'common_imports': len(import_comparison['common'])
        },
        'function_comparison': {
            'single_player_only': list(func_comparison['main_only']),
            'multiplayer_only': list(func_comparison['server_only']),
            'common': list(func_comparison['common'])
        },
        'import_comparison': {
            'single_player_only': list(import_comparison['main_only']),
            'multiplayer_only': list(import_comparison['server_only']),
            'common': list(import_comparison['common'])
        },
        'system_comparison': {k: {
            'single_player_only': list(v['main_only']),
            'multiplayer_only': list(v['server_only']),
            'common': list(v['common'])
        } for k, v in system_comparison.items()},
        'critical_missing': critical_missing,
        'recommendations': [
            "HIGH PRIORITY: Implement missing core game loop functionality",
            "MEDIUM PRIORITY: Add session management and narrative enhancements", 
            "LOW PRIORITY: Add utility functions for completeness",
            "MULTIPLAYER: Continue developing multiplayer-specific features"
        ]
    }
    
    # Save comprehensive report
    with open('single_vs_multiplayer_comparison.json', 'w', encoding='utf-8') as f:
        json.dump(comprehensive_report, f, indent=2, ensure_ascii=False)
    
    print("\\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"Functions missing from multiplayer: {len(func_comparison['main_only'])}")
    print(f"High priority missing: {len(critical_missing['high_priority'])}")
    print(f"Medium priority missing: {len(critical_missing['medium_priority'])}")
    print(f"Low priority missing: {len(critical_missing['low_priority'])}")
    print(f"Multiplayer-specific additions: {len(critical_missing['multiplayer_specific'])}")
    
    print("\\nDetailed report saved to: single_vs_multiplayer_comparison.json")
    print("=" * 100)
    
    return comprehensive_report

if __name__ == "__main__":
    generate_detailed_report()