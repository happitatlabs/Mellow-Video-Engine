"""
Utils Module - Mellow-Link

This module contains utility functions for:
- System control (process management)
- Port monitoring
- Avatar service launch/stop
"""

from .system_control import (
    # Port Utilities
    is_port_active,
    wait_for_port,
    # Process Utilities
    is_process_running,
    get_process_info,
    # Avatar Service Management
    launch_avatar_service,
    stop_avatar_service,
    get_avatar_status,
    # Constants
    AVATAR_EXE_NAME,
    AVATAR_WORKING_DIR,
    AVATAR_VENV_PYTHON,
    DEFAULT_AVATAR_WS_PORT,
)

__all__ = [
    # Port Utilities
    "is_port_active",
    "wait_for_port",
    # Process Utilities
    "is_process_running",
    "get_process_info",
    # Avatar Service Management
    "launch_avatar_service",
    "stop_avatar_service",
    "get_avatar_status",
    # Constants
    "AVATAR_EXE_NAME",
    "AVATAR_WORKING_DIR",
    "AVATAR_VENV_PYTHON",
    "DEFAULT_AVATAR_WS_PORT",
]
