# Description: List all Python files in mellow_link/workspace directory and print their paths using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()

# Define the target scripts to analyze
scripts_to_analyze = [
    "code_formatter.py",
    "project_status_reporter.py",
    "temp_tool_analyzer.py",
    "test_coverage_analyzer.py",
    "test_update_readme.py"
]

# Iterate over each script and print its path
for script_name in scripts_to_analyze:
    script_path = WORKSPACE_ROOT / script_name
    if script_path.exists():
        print(f"Path: {script_path}")
    else:
        print(f"Script {script_name} not found in the workspace.")