# Description: Analyze and consolidate redundant tool scripts in the workspace using pathlib.

from pathlib import Path
import difflib

WORKSPACE_ROOT = Path.cwd()

def list_python_files(directory):
    return [f for f in directory.rglob('*.py') if f.is_file()]

def read_file_content(file_path):
    return file_path.read_text(encoding='utf-8')

def find_redundant_files(files):
    redundant_pairs = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            content1 = read_file_content(files[i])
            content2 = read_file_content(files[j])
            similarity = difflib.SequenceMatcher(None, content1, content2).ratio()
            if similarity > 0.9:  # Threshold for redundancy
                redundant_pairs.append((files[i], files[j], similarity))
    return redundant_pairs

def consolidate_redundancies(redundant_pairs):
    for file1, file2, similarity in redundant_pairs:
        print(f"Redundant files: {file1} and {file2} with similarity: {similarity:.2f}")

def main():
    workspace_path = WORKSPACE_ROOT / 'mellow_link' / 'workspace'
    python_files = list_python_files(workspace_path)
    redundant_files = find_redundant_files(python_files)
    consolidate_redundancies(redundant_files)

if __name__ == "__main__":
    main()