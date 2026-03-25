# Description: Analyze and report redundancy and similarity of scripts in temp_tools directory using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()
TEMP_TOOLS_DIR = WORKSPACE_ROOT / 'temp_tools'

def get_script_metadata(script_path):
    """Extracts metadata from the script file."""
    content = script_path.read_text(encoding='utf-8')
    lines = content.splitlines()
    description = next((line for line in lines if line.startswith('# Description:')), 'No description found')
    return {
        'path': script_path,
        'description': description
    }

def list_scripts_in_directory(directory):
    """Lists all Python scripts in the given directory."""
    return [f for f in directory.iterdir() if f.is_file() and f.suffix == '.py']

def analyze_similarity_and_redundancy(scripts):
    """Analyzes scripts for similarity and redundancy."""
    similar_scripts = []
    descriptions = {}

    for script in scripts:
        metadata = get_script_metadata(script)
        desc = metadata['description']
        if desc in descriptions:
            similar_scripts.append((descriptions[desc], metadata['path']))
        else:
            descriptions[desc] = metadata['path']

    return similar_scripts

scripts = list_scripts_in_directory(TEMP_TOOLS_DIR)
similar_scripts = analyze_similarity_and_redundancy(scripts)

print("Similar or Redundant Scripts:")
for script1, script2 in similar_scripts:
    print(f" - {script1} and {script2} have similar descriptions.")