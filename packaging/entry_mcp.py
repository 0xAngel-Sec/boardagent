"""PyInstaller entry point: boardagent-mcp (stdio)."""
import asyncio

from boardagent.mcp_server import main

if __name__ == "__main__":
    asyncio.run(main())
