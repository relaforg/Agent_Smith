from mcp.server import MCPServer
from typing import List, Dict


mcp = MCPServer("swebench_mcp")


@mcp.tool()
def read_file(filepath: str, start_line: int, end_line: int) -> str:
    """Read the content of a file with line numbers."""
    ...


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str):
    """Replace an exact string in a file with a new string."""
    ...


@mcp.tool()
def list_files(directory: str, pattern: str) -> List[str]:
    """List files in a directory matching a given pattern."""
    ...


@mcp.tool()
def search_code(pattern: str, file_pattern: str) -> str:
    """Perform a grep-like search in the codebase."""
    ...


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """Find the definition of a function or a class."""
    ...


@mcp.tool()
def find_references(name: str, filepath: str, line: int) -> str:
    """Find all usages of a symbol (function or class)."""
    ...


@mcp.tool()
def run_tests():
    """Execute the evaluation script."""
    return "dfdfdf"


@mcp.tool()
def get_patch():
    """Retrieve the unified git diff of all changes made to the repository,
    depending on the implementation"""
    ...


@mcp.tool()
def run_command(command, workdir) -> Dict[str, str]:
    """Execute a shell command in the specified working directory.
    Returns the command’s stdout, stderr, and exit code."""
    ...


if __name__ == "__main__":
    mcp.run()
