"""
System Control Utilities for Mellow-Link

Provides process management for launching and monitoring external applications
like the Open-LLM-VTuber avatar service.
"""

import logging
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import psutil

# Windows-specific process creation flags
if sys.platform == "win32":
    CREATE_NEW_PROCESS_GROUP = subprocess.CREATE_NEW_PROCESS_GROUP
    CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE
else:
    # On non-Windows platforms, these flags are not used
    CREATE_NEW_PROCESS_GROUP = 0
    CREATE_NEW_CONSOLE = 0

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

AVATAR_EXE_NAME = "run_server.py"
AVATAR_WORKING_DIR = Path(r"D:\AI_Project\Open-LLM-VTuber")
AVATAR_VENV_PYTHON = Path(r"D:\AI_Project\Open-LLM-VTuber\venv\Scripts\python.exe")

# Default port - can be overridden by settings
DEFAULT_AVATAR_WS_PORT = 12393


# =============================================================================
# Process Utilities
# =============================================================================

def is_port_active(port: int, host: str = "localhost", timeout: float = 1.0) -> bool:
    """
    Check if a port is actively listening (server is running).

    Args:
        port: The port number to check
        host: The host to connect to (default: localhost)
        timeout: Connection timeout in seconds

    Returns:
        True if the port is accepting connections, False otherwise.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            is_active = result == 0
            if is_active:
                logger.debug(f"[SystemControl] Port {port} is active on {host}")
            return is_active
    except socket.error as e:
        logger.debug(f"[SystemControl] Socket error checking port {port}: {e}")
        return False
    except Exception as e:
        logger.error(f"[SystemControl] Error checking port {port}: {e}")
        return False


def wait_for_port(port: int, host: str = "localhost", timeout: float = 30.0, interval: float = 1.0) -> bool:
    """
    Wait for a port to become active.

    Args:
        port: The port number to wait for
        host: The host to connect to
        timeout: Maximum time to wait in seconds
        interval: Time between checks in seconds

    Returns:
        True if the port becomes active within timeout, False otherwise.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        if is_port_active(port, host):
            logger.info(f"[SystemControl] Port {port} is now active")
            return True
        time.sleep(interval)
    logger.warning(f"[SystemControl] Timeout waiting for port {port} to become active")
    return False


def is_process_running(process_name: str) -> bool:
    """
    Check if a process with the given name is currently running.

    Args:
        process_name: The name of the executable (e.g., "run_server.py")

    Returns:
        True if the process is running, False otherwise.
    """
    process_name_lower = process_name.lower()

    try:
        for proc in psutil.process_iter(['name']):
            try:
                if proc.info['name'] and proc.info['name'].lower() == process_name_lower:
                    logger.debug(f"[SystemControl] Found running process: {process_name} (PID: {proc.pid})")
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Process may have terminated or we don't have access
                continue
    except Exception as e:
        logger.error(f"[SystemControl] Error checking process status: {e}")
        return False

    return False


def get_process_info(process_name: str) -> Optional[dict]:
    """
    Get information about a running process.

    Args:
        process_name: The name of the executable

    Returns:
        Dictionary with process info (pid, name, status, memory) or None if not found.
    """
    process_name_lower = process_name.lower()

    try:
        for proc in psutil.process_iter(['pid', 'name', 'status', 'memory_info']):
            try:
                if proc.info['name'] and proc.info['name'].lower() == process_name_lower:
                    memory_info = proc.info.get('memory_info')
                    return {
                        "pid": proc.info['pid'],
                        "name": proc.info['name'],
                        "status": proc.info['status'],
                        "memory_mb": memory_info.rss / (1024 * 1024) if memory_info else 0
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        logger.error(f"[SystemControl] Error getting process info: {e}")

    return None


# =============================================================================
# Avatar Service Management
# =============================================================================

def launch_avatar_service(
    exe_name: str = AVATAR_EXE_NAME,
    working_dir: Path = AVATAR_WORKING_DIR,
    port: int = DEFAULT_AVATAR_WS_PORT
) -> bool:
    """
    Launch the Open-LLM-VTuber avatar service if not already running.

    This function:
    1. Checks if the avatar port (12393) is already active (service running)
    2. Falls back to process name check if port is not active
    3. Verifies the executable and venv Python exist
    4. Launches the process with CREATE_NEW_CONSOLE flag for visible window
    5. Sets the correct working directory for asset loading

    Args:
        exe_name: Name of the executable file (default: "run_server.py")
        working_dir: Working directory for the process (default: configured path)
        port: WebSocket port to check for active service (default: 12393)

    Returns:
        True if the process was launched or is already running, False on error.
    """
    # 1. Check if port is already active (avatar server is running)
    if is_port_active(port):
        logger.info(f"[SystemControl] Avatar service is already running on port {port}. Skipping launch.")
        return True

    # 2. Fallback: Check if process is running but port not yet active
    if is_process_running(exe_name):
        logger.info(f"[SystemControl] Avatar process '{exe_name}' is running (port {port} not yet active). Waiting...")
        # Wait briefly for port to become active
        if wait_for_port(port, timeout=10.0, interval=1.0):
            return True
        logger.warning(f"[SystemControl] Process running but port {port} not active - proceeding anyway")
        return True

    # 3. Build full path to executable
    exe_path = working_dir / exe_name

    # 4. Verify paths exist
    if not exe_path.exists():
        logger.error(f"[SystemControl] Avatar executable not found: {exe_path}")
        return False

    if not working_dir.exists():
        logger.error(f"[SystemControl] Working directory not found: {working_dir}")
        return False

    # 5. Verify venv Python exists
    python_exe = str(AVATAR_VENV_PYTHON)
    if not AVATAR_VENV_PYTHON.exists():
        logger.warning(f"[SystemControl] Venv Python not found: {AVATAR_VENV_PYTHON}, falling back to system Python")
        python_exe = "python"

    # 6. Launch process with CREATE_NEW_CONSOLE for visible window
    try:
        logger.info(f"[SystemControl] Launching avatar service via {python_exe}")
        logger.info(f"[SystemControl] Working directory: {working_dir}")
        logger.info(f"[SystemControl] Target port: {port}")

        # Windows-specific: CREATE_NEW_CONSOLE keeps the window visible
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE

        process = subprocess.Popen(
            [python_exe, str(exe_path)],
            cwd=str(working_dir),
            creationflags=creation_flags,
        )

        logger.info(f"[SystemControl] Avatar service launched successfully (PID: {process.pid})")
        logger.info(f"[SystemControl] Waiting for port {port} to become active...")

        # Wait for the server to start accepting connections (max 30 seconds)
        if wait_for_port(port, timeout=30.0, interval=1.0):
            logger.info(f"[SystemControl] Avatar service is ready on port {port}")
            return True
        else:
            logger.warning(f"[SystemControl] Avatar service started but port {port} not yet active (may need more time)")
            return True  # Process started, port may take longer

    except FileNotFoundError:
        logger.error(f"[SystemControl] Python or executable not found: {python_exe} / {exe_path}")
        return False
    except PermissionError:
        logger.error(f"[SystemControl] Permission denied when launching: {exe_path}")
        return False
    except OSError as e:
        logger.error(f"[SystemControl] OS error launching avatar service: {e}")
        return False
    except Exception as e:
        logger.error(f"[SystemControl] Unexpected error launching avatar service: {e}")
        return False


def stop_avatar_service(exe_name: str = AVATAR_EXE_NAME) -> bool:
    """
    Stop the avatar service if running.

    Args:
        exe_name: Name of the executable to stop

    Returns:
        True if the process was stopped or wasn't running, False on error.
    """
    process_name_lower = exe_name.lower()

    try:
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                if proc.info['name'] and proc.info['name'].lower() == process_name_lower:
                    logger.info(f"[SystemControl] Stopping avatar service (PID: {proc.info['pid']})")
                    proc.terminate()
                    proc.wait(timeout=10)  # Wait up to 10 seconds for graceful shutdown
                    logger.info(f"[SystemControl] Avatar service stopped successfully")
                    return True
            except psutil.TimeoutExpired:
                logger.warning(f"[SystemControl] Process didn't terminate gracefully, killing...")
                proc.kill()
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                logger.error(f"[SystemControl] Error stopping process: {e}")
                return False
    except Exception as e:
        logger.error(f"[SystemControl] Unexpected error stopping avatar service: {e}")
        return False

    # Process wasn't running
    logger.info(f"[SystemControl] Avatar service was not running")
    return True


def get_avatar_status(port: int = DEFAULT_AVATAR_WS_PORT) -> dict:
    """
    Get the current status of the avatar service.

    Checks both port availability and process status for comprehensive info.

    Args:
        port: WebSocket port to check (default: 12393)

    Returns:
        Dictionary with status information including port status.
    """
    port_active = is_port_active(port)
    process_info = get_process_info(AVATAR_EXE_NAME)

    if port_active:
        # Service is definitely running and accepting connections
        return {
            "running": True,
            "port_active": True,
            "port": port,
            "pid": process_info["pid"] if process_info else None,
            "status": "ready",
            "memory_mb": round(process_info["memory_mb"], 2) if process_info else 0
        }
    elif process_info:
        # Process running but port not active (starting up?)
        return {
            "running": True,
            "port_active": False,
            "port": port,
            "pid": process_info["pid"],
            "status": "starting",
            "memory_mb": round(process_info["memory_mb"], 2)
        }
    else:
        # Not running at all
        return {
            "running": False,
            "port_active": False,
            "port": port,
            "pid": None,
            "status": "not_running",
            "memory_mb": 0
        }
