"""Registers every tool's spec so server.py can loop over one list
instead of importing each module by name."""
from linux_mcp.tools import (applications, browser, desktop, filesystem,
                              network, packages, process, services, shell,
                              system, web)

ALL_TOOL_SPECS = [
    system.TOOL_SPEC,
    filesystem.READ_TOOL_SPEC,
    filesystem.WRITE_TOOL_SPEC,
    shell.TOOL_SPEC,
    process.LIST_TOOL_SPEC,
    process.KILL_TOOL_SPEC,
    services.TOOL_SPEC,
    packages.SEARCH_TOOL_SPEC,
    packages.INSTALL_TOOL_SPEC,
    network.TOOL_SPEC,
    applications.TOOL_SPEC,
    browser.TOOL_SPEC,
    web.TOOL_SPEC,
    desktop.TOOL_SPEC,
    
]
