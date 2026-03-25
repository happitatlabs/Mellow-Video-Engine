# Description: List all .py files in the workspace with their paths and main functionality based on comments and docstrings.

from pathlib import Path

workspace_path = Path('mellow_link/workspace')

def extract_functionality(file_path):
    content = file_path.read_text(encoding='utf-8')
    lines = content.splitlines()
    functionality = []
    for line in lines:
        line = line.strip()
        if line.startswith('#') or line.startswith('"""') or line.startswith("'''"):
            functionality.append(line.strip('#').strip('"""').strip("'''").strip())
        if line.endswith('"""') or line.endswith("'''"):
            break
    return ' '.join(functionality)

for py_file in workspace_path.rglob('*.py'):
    file_name = py_file.name
    file_path = py_file.resolve()
    functionality = extract_functionality(py_file)
    print(f"File Name: {file_name}")
    print(f"File Path: {file_path}")
    print(f"Functionality: {functionality}")
    print('-' * 40)