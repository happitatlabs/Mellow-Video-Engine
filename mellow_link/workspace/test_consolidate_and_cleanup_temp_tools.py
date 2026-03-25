# Description: List all Python files in the mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()
workspace_dir = WORKSPACE_ROOT / "mellow_link" / "workspace"

# List all Python files in the workspace directory and print their paths
for py_file in workspace_dir.rglob("*.py"):
    print(py_file)