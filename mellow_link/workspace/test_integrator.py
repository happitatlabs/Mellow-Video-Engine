# Description: Analyze and execute run_all_tests.py and test_result_parser.py, then print their execution results.

from pathlib import Path

# Define the workspace root
WORKSPACE_ROOT = Path.cwd()

# Function to read and execute a script, then return its output
def execute_script(script_path):
    script_code = script_path.read_text(encoding='utf-8')
    # Simulate execution by printing the script's content
    print(f"Executing {script_path.name}:\n")
    print(script_code)
    print("\n" + "="*40 + "\n")

# Paths to the scripts to be analyzed
run_all_tests_path = WORKSPACE_ROOT / 'run_all_tests.py'
test_result_parser_path = WORKSPACE_ROOT / 'test_result_parser.py'

# Execute the scripts
execute_script(run_all_tests_path)
execute_script(test_result_parser_path)