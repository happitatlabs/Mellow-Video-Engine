# Description: Update README with script paths and their descriptions based on comments and docstrings.

from pathlib import Path

# Define the workspace root path
WORKSPACE_ROOT = Path.cwd()

# Function to extract the first line of a file that contains '# Description:'
def extract_description(file_path):
    description = "No description available."
    for line in file_path.read_text(encoding='utf-8').splitlines():
        if line.startswith("# Description:"):
            description = line.replace("# Description:", "").strip()
            break
    return description

# Function to list all Python scripts and their descriptions
def list_scripts_and_descriptions():
    scripts_info = []
    for script_path in WORKSPACE_ROOT.rglob('*.py'):
        if "temp_tools" in script_path.parts:
            continue
        description = extract_description(script_path)
        relative_path = script_path.relative_to(WORKSPACE_ROOT)
        scripts_info.append((str(relative_path), description))
    return scripts_info

# Gather script information and print it
scripts_info = list_scripts_and_descriptions()
for script_path, description in scripts_info:
    print(f"{script_path}  # {description}")