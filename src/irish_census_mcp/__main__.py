"""Entry point: `python -m irish_census_mcp` runs the server over stdio."""

from .server import run as main

if __name__ == "__main__":
    main()
