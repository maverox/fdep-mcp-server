"""
FDEP MCP Server - Advanced Haskell Code Analysis Tool

A comprehensive Model Context Protocol (MCP) server for analyzing Haskell codebases
through Spider plugin FDEP output, providing 29 powerful analysis tools.
"""

__version__ = "0.1.0"
__author__ = "FDEP MCP Team"

import asyncio
from .server import main as async_main
from .config import config

def main():
    """Entry point that handles async main function."""
    asyncio.run(async_main())

__all__ = ["main", "config"]