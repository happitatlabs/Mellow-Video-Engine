# Description: Analyze functional similarity and code redundancy in temp_tools directory scripts using pathlib.

from pathlib import Path
import ast
import difflib

WORKSPACE_ROOT = Path.cwd()
TEMP_TOOLS_DIR = WORKSPACE_ROOT / "temp_tools"

def analyze_script_similarity():
    scripts = list(TEMP_TOOLS_DIR.glob("*.py"))
    script_contents = {script: script.read_text(encoding="utf-8") for script in scripts}
    script_asts = {script: ast.parse(content) for script, content in script_contents.items()}

    similarities = []

    for script1, ast1 in script_asts.items():
        for script2, ast2 in script_asts.items():
            if script1 >= script2:
                continue

            similarity_ratio = difflib.SequenceMatcher(
                None, script_contents[script1], script_contents[script2]
            ).ratio()

            if similarity_ratio > 0.7:
                similarities.append((script1.name, script2.name, similarity_ratio))

    for script1, script2, ratio in similarities:
        print(f"Similarity between {script1} and {script2}: {ratio:.2f}")

analyze_script_similarity()