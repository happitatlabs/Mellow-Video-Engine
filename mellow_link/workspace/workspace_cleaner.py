# Description: List all files in 'workspace_cleaner.py' and 'temp_tools/' directory using pathlib and print their paths.

from pathlib import Path

# Define the workspace root
WORKSPACE_ROOT = Path.cwd()

# Define the paths to 'workspace_cleaner.py' and 'temp_tools/' directory
workspace_cleaner_path = WORKSPACE_ROOT / 'workspace_cleaner.py'
temp_tools_dir = WORKSPACE_ROOT / 'temp_tools'

# List and print the path of 'workspace_cleaner.py' if it exists
if workspace_cleaner_path.exists():
    print(workspace_cleaner_path)

# List and print all files in 'temp_tools/' directory
if temp_tools_dir.exists() and temp_tools_dir.is_dir():
    for file in temp_tools_dir.iterdir():
        if file.is_file():
            print(file)