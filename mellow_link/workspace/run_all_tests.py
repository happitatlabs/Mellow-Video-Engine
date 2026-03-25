# Description: List all test_*.py files and print the current source code of run_all_tests.py in the workspace.

from pathlib import Path

# Define the workspace root path
WORKSPACE_ROOT = Path.cwd()

# Find all test_*.py files in the workspace
test_files = list(WORKSPACE_ROOT.rglob('test_*.py'))

# Print the list of test files
print("List of test_*.py files:")
for test_file in test_files:
    print(test_file.relative_to(WORKSPACE_ROOT))

# Print the current source code of run_all_tests.py
run_all_tests_path = WORKSPACE_ROOT / 'run_all_tests.py'
if run_all_tests_path.exists():
    print("\nCurrent source code of run_all_tests.py:")
    print(run_all_tests_path.read_text(encoding='utf-8'))
else:
    print("\nrun_all_tests.py not found in the workspace.")