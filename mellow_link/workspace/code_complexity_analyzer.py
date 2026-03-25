# Description: Calculate cyclomatic complexity of Python files in the workspace using radon and pathlib.

from pathlib import Path
from radon.complexity import cc_visit, cc_rank

WORKSPACE_ROOT = Path.cwd()

def calculate_cyclomatic_complexity(file_path):
    code = file_path.read_text(encoding='utf-8')
    complexities = cc_visit(code)
    return [(comp.name, comp.complexity, cc_rank(comp.complexity)) for comp in complexities]

def main():
    python_files = WORKSPACE_ROOT.rglob('*.py')
    for py_file in python_files:
        complexities = calculate_cyclomatic_complexity(py_file)
        print(f"File: {py_file}")
        for name, complexity, rank in complexities:
            print(f"  Function: {name}, Complexity: {complexity}, Rank: {rank}")

if __name__ == "__main__":
    main()