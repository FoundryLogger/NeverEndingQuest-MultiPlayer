#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 MoonlightByte
# SPDX-License-Identifier: Fair-Source-1.0

"""
Complete Analysis of main.py to extract ALL functions, imports, and systems
This will serve as the definitive source of truth for single-player functionality
"""

import ast
import os
import re
import json

def extract_function_definitions(file_path):
    """Extract all function definitions from a Python file"""
    functions = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    try:
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Get function signature
                args = []
                for arg in node.args.args:
                    args.append(arg.arg)
                
                # Get docstring if exists
                docstring = ast.get_docstring(node)
                
                # Get line number
                line_no = node.lineno
                
                functions.append({
                    'name': node.name,
                    'args': args,
                    'docstring': docstring or "No docstring",
                    'line_number': line_no
                })
    except Exception as e:
        print(f"Error parsing AST: {e}")
    
    return functions

def extract_imports(file_path):
    """Extract all import statements"""
    imports = {
        'standard': [],
        'third_party': [],
        'local': []
    }
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    try:
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    if module_name.startswith(('core.', 'utils.', 'updates.')):
                        imports['local'].append(module_name)
                    elif module_name in ['json', 'os', 'sys', 're', 'datetime', 'time', 'threading']:
                        imports['standard'].append(module_name)
                    else:
                        imports['third_party'].append(module_name)
            
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for alias in node.names:
                    import_name = f"{module_name}.{alias.name}" if module_name else alias.name
                    if module_name.startswith(('core.', 'utils.', 'updates.')):
                        imports['local'].append(import_name)
                    elif module_name in ['json', 'os', 'sys', 're', 'datetime', 'time', 'threading']:
                        imports['standard'].append(import_name)
                    else:
                        imports['third_party'].append(import_name)
    
    except Exception as e:
        print(f"Error parsing imports: {e}")
    
    return imports

def extract_global_variables_and_constants(file_path):
    """Extract global variables and constants"""
    globals_vars = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Look for global variable assignments (simple heuristic)
    lines = content.split('\n')
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if line and not line.startswith('#') and not line.startswith('def ') and not line.startswith('class '):
            if '=' in line and not line.startswith(' '):  # Top-level assignments
                var_name = line.split('=')[0].strip()
                if var_name.isidentifier():
                    globals_vars.append({
                        'name': var_name,
                        'line': i,
                        'definition': line
                    })
    
    return globals_vars

def extract_system_patterns(file_path):
    """Extract system patterns and key functionality"""
    systems = {
        'ai_models': [],
        'validation_systems': [],
        'conversation_management': [],
        'game_loop_functions': [],
        'save_load_functions': [],
        'combat_functions': [],
        'character_functions': [],
        'location_functions': [],
        'module_functions': [],
        'utility_functions': []
    }
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Extract function calls and patterns
    function_calls = re.findall(r'(\w+)\s*\(', content)
    function_calls = list(set(function_calls))  # Remove duplicates
    
    # Categorize based on naming patterns and context
    for func in function_calls:
        func_lower = func.lower()
        
        if any(keyword in func_lower for keyword in ['ai', 'openai', 'gpt', 'model', 'client']):
            systems['ai_models'].append(func)
        elif any(keyword in func_lower for keyword in ['validate', 'validation', 'verify']):
            systems['validation_systems'].append(func)
        elif any(keyword in func_lower for keyword in ['conversation', 'history', 'message']):
            systems['conversation_management'].append(func)
        elif any(keyword in func_lower for keyword in ['loop', 'main', 'game']):
            systems['game_loop_functions'].append(func)
        elif any(keyword in func_lower for keyword in ['save', 'load', 'json', 'file']):
            systems['save_load_functions'].append(func)
        elif any(keyword in func_lower for keyword in ['combat', 'battle', 'fight']):
            systems['combat_functions'].append(func)
        elif any(keyword in func_lower for keyword in ['character', 'player', 'party']):
            systems['character_functions'].append(func)
        elif any(keyword in func_lower for keyword in ['location', 'area', 'transition']):
            systems['location_functions'].append(func)
        elif any(keyword in func_lower for keyword in ['module', 'campaign']):
            systems['module_functions'].append(func)
        else:
            systems['utility_functions'].append(func)
    
    return systems

def analyze_main_py():
    """Complete analysis of main.py"""
    file_path = "main.py"
    
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found")
        return
    
    print("=" * 80)
    print("COMPLETE ANALYSIS OF MAIN.PY - SINGLE PLAYER FUNCTIONALITY")
    print("=" * 80)
    
    # Extract functions
    print("\n[1] EXTRACTING FUNCTION DEFINITIONS...")
    functions = extract_function_definitions(file_path)
    print(f"Found {len(functions)} function definitions")
    
    # Extract imports
    print("\n[2] EXTRACTING IMPORT STATEMENTS...")
    imports = extract_imports(file_path)
    total_imports = sum(len(imports[key]) for key in imports)
    print(f"Found {total_imports} import statements")
    
    # Extract globals
    print("\n[3] EXTRACTING GLOBAL VARIABLES...")
    globals_vars = extract_global_variables_and_constants(file_path)
    print(f"Found {len(globals_vars)} global variables")
    
    # Extract systems
    print("\n[4] EXTRACTING SYSTEM PATTERNS...")
    systems = extract_system_patterns(file_path)
    total_systems = sum(len(systems[key]) for key in systems)
    print(f"Found {total_systems} system components")
    
    # Create comprehensive report
    report = {
        'analysis_date': '2025-07-28',
        'file_analyzed': file_path,
        'functions': functions,
        'imports': imports,
        'global_variables': globals_vars,
        'systems': systems,
        'summary': {
            'total_functions': len(functions),
            'total_imports': total_imports,
            'total_globals': len(globals_vars),
            'total_systems': total_systems
        }
    }
    
    # Save detailed report
    with open('main_py_analysis_complete.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n[5] DETAILED REPORT SAVED TO: main_py_analysis_complete.json")
    
    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY OF MAIN.PY FUNCTIONALITY")
    print("=" * 80)
    
    print(f"\n[FUNCTIONS] {len(functions)} total functions:")
    for func in sorted(functions, key=lambda x: x['name']):
        args_str = ', '.join(func['args']) if func['args'] else 'no args'
        print(f"  - {func['name']}({args_str}) [line {func['line_number']}]")
    
    print(f"\n[IMPORTS] {total_imports} total imports:")
    for category, items in imports.items():
        if items:
            print(f"  {category.upper()} ({len(items)}):")
            for item in sorted(set(items)):
                print(f"    - {item}")
    
    print(f"\n[GLOBAL VARIABLES] {len(globals_vars)} total globals:")
    for var in sorted(globals_vars, key=lambda x: x['name']):
        print(f"  - {var['name']} [line {var['line']}]")
    
    print(f"\n[SYSTEMS] {total_systems} total system components:")
    for category, items in systems.items():
        if items:
            unique_items = sorted(set(items))
            print(f"  {category.upper()} ({len(unique_items)}):")
            for item in unique_items[:10]:  # Show first 10 to avoid spam
                print(f"    - {item}")
            if len(unique_items) > 10:
                print(f"    ... and {len(unique_items) - 10} more")
    
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE - USE JSON FILE FOR DETAILED COMPARISON")
    print("=" * 80)
    
    return report

if __name__ == "__main__":
    analyze_main_py()