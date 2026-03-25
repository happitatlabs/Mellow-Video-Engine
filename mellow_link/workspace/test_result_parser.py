# Description: Parse and print standard output format of pytest or unittest results using pathlib.

from pathlib import Path

WORKSPACE_ROOT = Path.cwd()

def parse_test_output(output):
    lines = output.splitlines()
    results = []
    for line in lines:
        if "===" in line or "FAILURES" in line or "ERRORS" in line:
            results.append(line)
        elif line.startswith("FAILED") or line.startswith("PASSED"):
            results.append(line)
    return results

def main():
    test_output_file = WORKSPACE_ROOT / "test_output.txt"
    if test_output_file.exists():
        output = test_output_file.read_text(encoding='utf-8')
        parsed_results = parse_test_output(output)
        for result in parsed_results:
            print(result)
    else:
        print("No test output file found.")

main()