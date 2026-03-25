# Description: List metadata of files in the temp_tools directory using pathlib

from pathlib import Path
import datetime

WORKSPACE_ROOT = Path.cwd()
temp_tools_path = WORKSPACE_ROOT / "temp_tools"

for file in temp_tools_path.iterdir():
    if file.is_file():
        file_name = file.name
        file_size = file.stat().st_size
        creation_time = datetime.datetime.fromtimestamp(file.stat().st_ctime)
        
        print(f"File Name: {file_name}")
        print(f"Creation Time: {creation_time}")
        print(f"File Size: {file_size} bytes")
        print("-" * 40)