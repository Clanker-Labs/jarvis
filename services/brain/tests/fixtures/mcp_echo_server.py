"""Tiny MCP server used by the integration tests (stdio)."""

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

server = MCPServer("echo")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def shout(text: str) -> str:
    """Return the text in capitals."""
    return text.upper()


@server.tool()
def wipe_everything(confirm: bool) -> str:
    """A destructive-looking tool with no annotations."""
    return "wiped" if confirm else "nothing"


if __name__ == "__main__":
    server.run("stdio")
