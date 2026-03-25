# Description: List all Python scripts in the mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()

# List all Python files in the mellow_link/workspace directory
workspace_path = WORKSPACE_ROOT / "mellow_link" / "workspace"
python_files = workspace_path.rglob("*.py")

# Print the paths of all Python files
for python_file in python_files:
    print(python_file)