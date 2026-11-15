from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .connection import run_in_catia


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Make the COM runner available through the MCP request context."""
    yield run_in_catia


def create_server(*, port: int = 8000) -> FastMCP:
    return FastMCP(
        "catia-mcp",
        host="127.0.0.1",
        port=port,
        json_response=True,
        lifespan=lifespan,
    )
