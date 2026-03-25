# Description: Analyze 'tool_redundancy_checker.py' to design test cases by listing Python files in the workspace using pathlib.

from pathlib import Path

# Define the workspace root path
WORKSPACE_ROOT = Path.cwd()

# List all Python files in the workspace directory
python_files = list(WORKSPACE_ROOT.rglob('*.py'))

# Print the paths of all Python files
for file in python_files:
    print(file)