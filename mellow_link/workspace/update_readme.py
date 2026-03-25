# Description: List Python scripts in the workspace with their paths and functionality based on comments and docstrings.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()

def extract_description(file_path):
    """Extracts the first line of the file that starts with '# Description:'"""
    lines = file_path.read_text(encoding='utf-8').splitlines()
    for line in lines:
        if line.startswith('# Description:'):
            return line[len('# Description:'):].strip()
    return "No description available"

def list_python_scripts_with_descriptions():
    python_files = WORKSPACE_ROOT.rglob('*.py')
    for file in python_files:
        relative_path = file.relative_to(WORKSPACE_ROOT)
        description = extract_description(file)
        print(f"{relative_path}: {description}")

list_python_scripts_with_descriptions()