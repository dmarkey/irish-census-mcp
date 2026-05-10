"""MCP server for the Irish National Archives census APIs (1821-1926)."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("irish-census-mcp")
except PackageNotFoundError:
    # Running from a source tree that isn't installed (uv sync always installs)
    __version__ = "0.0.0+unknown"
