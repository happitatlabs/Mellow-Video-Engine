# Description: Identify and safely delete duplicate files in temp_tools based on functional similarity using pathlib.

from pathlib import Path
import ast
import difflib

WORKSPACE_ROOT = Path.cwd()
TEMP_TOOLS_DIR = WORKSPACE_ROOT / "temp_tools"

def get_functional_similarity(file1_content, file2_content):
    """Calculate functional similarity between two Python files based on their AST."""
    try:
        tree1 = ast.parse(file1_content)
        tree2 = ast.parse(file2_content)
    except SyntaxError:
        return 0.0

    def get_function_names(tree):
        return sorted(node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))

    functions1 = get_function_names(tree1)
    functions2 = get_function_names(tree2)

    return difflib.SequenceMatcher(None, functions1, functions2).ratio()

def identify_duplicates(temp_tools_dir):
    """Identify duplicate files in the temp_tools directory based on functional similarity."""
    files = list(temp_tools_dir.glob("*.py"))
    duplicates = []

    for i, file1 in enumerate(files):
        file1_content = file1.read_text(encoding="utf-8")
        for file2 in files[i+1:]:
            file2_content = file2.read_text(encoding="utf-8")
            similarity = get_functional_similarity(file1_content, file2_content)
            if similarity > 0.8:  # Threshold for considering files as duplicates
                duplicates.append((file1, file2, similarity))

    return duplicates

def main():
    duplicates = identify_duplicates(TEMP_TOOLS_DIR)
    for file1, file2, similarity in duplicates:
        print(f"Duplicate found: {file1} and {file2} with similarity {similarity:.2f}")

if __name__ == "__main__":
    main()