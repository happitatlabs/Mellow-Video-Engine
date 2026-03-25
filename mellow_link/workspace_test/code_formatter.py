# Description: List all Python files in the mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

workspace_dir = Path('mellow_link/workspace')

for py_file in workspace_dir.rglob('*.py'):
    print(py_file)