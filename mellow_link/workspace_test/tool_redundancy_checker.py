# Description: List all Python files in the mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()

# List all Python files in the mellow_link/workspace directory
python_files = WORKSPACE_ROOT.rglob('*.py')

# Print the paths of the Python files
for file in python_files:
    print(file)