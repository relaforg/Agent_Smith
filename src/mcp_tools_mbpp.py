from mcp.server import MCPServer


mcp = MCPServer("mbpp_mcp")


@mcp.tool()
def run_tests():
    return ""


if __name__ == "__main__":
    mcp.run()
