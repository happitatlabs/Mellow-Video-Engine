# Description: List all Python files in the mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()

for py_file in (WORKSPACE_ROOT / 'mellow_link' / 'workspace').rglob('*.py'):
    print(py_file)