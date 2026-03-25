# Description: Analyze code_formatter.py to design appropriate test cases by listing Python files in the workspace.

from pathlib import Path

# Define the workspace root path
WORKSPACE_ROOT = Path.cwd()

# Define the path to the code_formatter.py script
code_formatter_path = WORKSPACE_ROOT / "code_formatter.py"

# Read the content of code_formatter.py
code_formatter_content = code_formatter_path.read_text(encoding='utf-8')

# Print the content of code_formatter.py to analyze its functionality
print(code_formatter_content)

# List all Python files in the mellow_link/workspace directory
workspace_path = WORKSPACE_ROOT / "mellow_link" / "workspace"
python_files = list(workspace_path.rglob('*.py'))

# Print the paths of all Python files
for python_file in python_files:
    print(python_file)