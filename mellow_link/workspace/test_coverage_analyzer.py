# Description: List all Python files in the mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()
workspace_dir = WORKSPACE_ROOT / "mellow_link" / "workspace"

# List all Python files in the workspace directory
python_files = workspace_dir.rglob("*.py")

# Print the paths of all Python files
for file in python_files:
    print(file)