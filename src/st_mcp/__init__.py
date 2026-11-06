import asyncio
import sys


def main():
    """Main entry point for the package."""
    # Imported here so that `import st_mcp.sql.connstr` (used by the offline
    # self-tests) does not need the mcp server package installed.
    from . import server

    # 设置Windows上的事件循环策略
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(server.main())


# Optionally expose other important items at package level
__all__ = [
    "main",
    "server",
]