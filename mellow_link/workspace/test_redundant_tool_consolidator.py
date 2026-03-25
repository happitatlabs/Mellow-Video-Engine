# Description: Analyze and summarize potential redundant tools in the temp_tools directory using pathlib.

from pathlib import Path

# Define the workspace root path
WORKSPACE_ROOT = Path.cwd()

# Define the path to the temp_tools directory
temp_tools_path = WORKSPACE_ROOT / 'temp_tools'

# Function to list and summarize Python files in the temp_tools directory
def summarize_temp_tools():
    # Dictionary to store the summary of each tool
    tool_summary = {}

    # Iterate over all Python files in the temp_tools directory
    for tool_file in temp_tools_path.glob('*.py'):
        # Read the content of the tool file
        content = tool_file.read_text(encoding='utf-8')

        # Extract the first line of the file as a summary
        first_line = content.splitlines()[0] if content else 'No content available'

        # Store the summary in the dictionary
        tool_summary[tool_file.name] = first_line

    # Print the summary of each tool
    for tool_name, summary in tool_summary.items():
        print(f"Tool: {tool_name}\nSummary: {summary}\n")

# Execute the function to summarize the tools
summarize_temp_tools()