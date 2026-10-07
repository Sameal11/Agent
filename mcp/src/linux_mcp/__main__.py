"""Entry point for `python -m linux_mcp` or the `linux-mcp` console script."""
import asyncio

from linux_mcp.server import run_stdio


def run():
    asyncio.run(run_stdio())


if __name__ == "__main__":
    run()
