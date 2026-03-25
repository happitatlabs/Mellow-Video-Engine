# Description: Analyze 'run_all_tests.py' to design test cases by extracting its functionality using pathlib.

from pathlib import Path

# Define the workspace root
WORKSPACE_ROOT = Path.cwd()

# Define the path to 'run_all_tests.py'
run_all_tests_path = WORKSPACE_ROOT / 'run_all_tests.py'

# Read the content of 'run_all_tests.py'
run_all_tests_content = run_all_tests_path.read_text(encoding='utf-8')

# Print the content of 'run_all_tests.py' to analyze its functionality
print(run_all_tests_content)