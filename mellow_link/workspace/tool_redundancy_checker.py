# Description: Analyze code similarity and duplication in temp_tools directory using pathlib

from pathlib import Path
import difflib

WORKSPACE_ROOT = Path.cwd()
TEMP_TOOLS_DIR = WORKSPACE_ROOT / "temp_tools"

def get_python_files(directory):
    return [file for file in directory.glob("*.py")]

def read_file_content(file_path):
    return file_path.read_text(encoding='utf-8')

def analyze_similarity(file1_content, file2_content):
    return difflib.SequenceMatcher(None, file1_content, file2_content).ratio()

def main():
    python_files = get_python_files(TEMP_TOOLS_DIR)
    num_files = len(python_files)
    
    if num_files < 2:
        print("Not enough files to analyze.")
        return
    
    for i in range(num_files):
        for j in range(i + 1, num_files):
            file1 = python_files[i]
            file2 = python_files[j]
            content1 = read_file_content(file1)
            content2 = read_file_content(file2)
            similarity = analyze_similarity(content1, content2)
            if similarity > 0.8:  # Threshold for similarity
                print(f"High similarity ({similarity:.2f}) between {file1.name} and {file2.name}")

main()