"""
VRAM Watchdog - GPU Memory Monitor

This module provides real-time GPU VRAM monitoring to prevent
out-of-memory conditions during AI workloads. It uses nvidia-smi
or pynvml for NVIDIA GPUs.

Design:
    - Runs as background async task
    - Emits events when thresholds are crossed
    - Provides synchronous queries for current status
    - Supports callback registration for event handling

Ported from legacy model_service.py GPU monitoring patterns.
"""

import asyncio
import logging
import subprocess
import shutil
from typing import Optional, Dict, Any, Callable, List, Awaitable, Union
from dataclasses import dataclass
from enum import Enum, auto
from datetime import datetime

logger = logging.getLogger(__name__)


# =============================================================================
# VRAM Status Enum
# =============================================================================

class VRAMStatus(Enum):
    """VRAM usage status levels."""

    NORMAL = auto()      # Usage below warning threshold
    WARNING = auto()     # Usage above warning, below critical
    CRITICAL = auto()    # Usage above critical threshold
    UNKNOWN = auto()     # Unable to determine (no GPU or error)

    def is_alert(self) -> bool:
        """Check if status requires attention."""
        return self in (VRAMStatus.WARNING, VRAMStatus.CRITICAL)


# =============================================================================
# GPU Info Container
# =============================================================================

@dataclass
class GPUInfo:
    """
    Container for GPU information.

    Attributes:
        device_id: GPU device index
        name: GPU model name
        total_memory_mb: Total VRAM in MB
        used_memory_mb: Currently used VRAM in MB
        free_memory_mb: Available VRAM in MB
        temperature_c: GPU temperature in Celsius (optional)
        utilization_percent: GPU compute utilization (optional)
        power_draw_w: Current power draw in Watts (optional)
        timestamp: When this info was collected
    """

    device_id: int
    name: str
    total_memory_mb: float
    used_memory_mb: float
    free_memory_mb: float
    temperature_c: Optional[float] = None
    utilization_percent: Optional[float] = None
    power_draw_w: Optional[float] = None
    timestamp: datetime = None

    def __post_init__(self):
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now()

    @property
    def usage_percent(self) -> float:
        """Calculate VRAM usage as percentage."""
        if self.total_memory_mb <= 0:
            return 0.0
        return (self.used_memory_mb / self.total_memory_mb) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "device_id": self.device_id,
            "name": self.name,
            "total_memory_mb": round(self.total_memory_mb, 2),
            "used_memory_mb": round(self.used_memory_mb, 2),
            "free_memory_mb": round(self.free_memory_mb, 2),
            "usage_percent": round(self.usage_percent, 2),
            "temperature_c": self.temperature_c,
            "utilization_percent": self.utilization_percent,
            "power_draw_w": self.power_draw_w,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# =============================================================================
# VRAM Watchdog Class
# =============================================================================

# Type alias for callbacks
CallbackType = Union[Callable[[GPUInfo], Any], Callable[[GPUInfo], Awaitable[Any]]]


class VRAMWatchdog:
    """
    Async VRAM monitoring service.

    Monitors GPU memory usage and emits warnings when thresholds
    are exceeded. Designed to prevent OOM errors during LLM and
    image generation workloads.

    Attributes:
        warning_threshold: Percentage to trigger warning (default 80%)
        critical_threshold: Percentage to trigger critical (default 95%)
        poll_interval: Seconds between checks (default 1.0)

    Usage:
        watchdog = VRAMWatchdog()
        watchdog.on_warning(callback_fn)
        await watchdog.start()
        ...
        await watchdog.stop()
    """

    DEFAULT_WARNING_THRESHOLD: float = 80.0
    DEFAULT_CRITICAL_THRESHOLD: float = 95.0
    DEFAULT_POLL_INTERVAL: float = 1.0

    # Check for pynvml availability
    _pynvml_available: Optional[bool] = None
    _pynvml = None

    def __init__(
        self,
        warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
        critical_threshold: float = DEFAULT_CRITICAL_THRESHOLD,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        device_id: int = 0
    ):
        """
        Initialize VRAM Watchdog.

        Args:
            warning_threshold: VRAM % to trigger warning events
            critical_threshold: VRAM % to trigger critical events
            poll_interval: Seconds between VRAM checks
            device_id: GPU device index to monitor (default 0)
        """
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.poll_interval = poll_interval
        self.device_id = device_id

        self._is_running: bool = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._warning_callbacks: List[CallbackType] = []
        self._critical_callbacks: List[CallbackType] = []
        self._recovery_callbacks: List[CallbackType] = []
        self._current_status: VRAMStatus = VRAMStatus.UNKNOWN
        self._previous_status: VRAMStatus = VRAMStatus.UNKNOWN
        self._last_gpu_info: Optional[GPUInfo] = None
        self._start_time: Optional[datetime] = None

        # Initialize pynvml if available
        self._init_pynvml()

    def _init_pynvml(self) -> None:
        """Initialize pynvml library if available."""
        if VRAMWatchdog._pynvml_available is None:
            try:
                import pynvml  # type: ignore[reportMissingImports]
                pynvml.nvmlInit()
                VRAMWatchdog._pynvml = pynvml
                VRAMWatchdog._pynvml_available = True
                logger.info("[VRAMWatchdog] pynvml initialized successfully")
            except ImportError:
                VRAMWatchdog._pynvml_available = False
                logger.info("[VRAMWatchdog] pynvml not available, using nvidia-smi fallback")
            except Exception as e:
                VRAMWatchdog._pynvml_available = False
                logger.warning(f"[VRAMWatchdog] pynvml init failed: {e}")

    # ==================== Lifecycle ====================

    async def start(self) -> None:
        """
        Start the VRAM monitoring background task.

        Creates an async task that periodically checks GPU memory
        and invokes callbacks when thresholds are crossed.

        Raises:
            RuntimeError: If watchdog is already running
        """
        if self._is_running:
            raise RuntimeError("VRAMWatchdog is already running")

        # Check GPU availability
        if not self.is_gpu_available():
            logger.warning("[VRAMWatchdog] No GPU detected, starting in limited mode")

        self._is_running = True
        self._start_time = datetime.now()
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        logger.info(
            f"[VRAMWatchdog] Started monitoring GPU {self.device_id} "
            f"(warning: {self.warning_threshold}%, critical: {self.critical_threshold}%)"
        )

    async def stop(self) -> None:
        """
        Stop the VRAM monitoring background task.

        Gracefully cancels the monitoring task and cleans up resources.
        """
        if not self._is_running:
            return

        self._is_running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        logger.info("[VRAMWatchdog] Stopped")

    def is_running(self) -> bool:
        """Check if watchdog is currently monitoring."""
        return self._is_running

    # ==================== Callbacks ====================

    def on_warning(self, callback: CallbackType) -> None:
        """
        Register callback for warning threshold events.

        Args:
            callback: Function to call when warning threshold crossed.
                     Receives GPUInfo as argument. Can be sync or async.

        Note:
            Callback is invoked once when crossing threshold,
            not repeatedly while above threshold.
        """
        self._warning_callbacks.append(callback)
        logger.debug("[VRAMWatchdog] Warning callback registered")

    def on_critical(self, callback: CallbackType) -> None:
        """
        Register callback for critical threshold events.

        Args:
            callback: Function to call when critical threshold crossed.
                     Receives GPUInfo as argument. Can be sync or async.

        Note:
            Critical events should trigger emergency responses
            like pausing GPU workloads.
        """
        self._critical_callbacks.append(callback)
        logger.debug("[VRAMWatchdog] Critical callback registered")

    def on_recovery(self, callback: CallbackType) -> None:
        """
        Register callback for when VRAM returns to normal.

        Args:
            callback: Function to call when usage drops below warning.
        """
        self._recovery_callbacks.append(callback)
        logger.debug("[VRAMWatchdog] Recovery callback registered")

    async def _invoke_callbacks(
        self,
        callbacks: List[CallbackType],
        gpu_info: GPUInfo
    ) -> None:
        """Invoke all callbacks in list, handling both sync and async."""
        for callback in callbacks:
            try:
                result = callback(gpu_info)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"[VRAMWatchdog] Callback error: {e}")

    # ==================== Monitoring ====================

    async def _monitor_loop(self) -> None:
        """
        Internal monitoring loop.

        Loop Steps:
            1. Query current GPU status
            2. Compare against thresholds
            3. Detect status transitions
            4. Invoke appropriate callbacks
            5. Sleep for poll_interval
        """
        logger.debug("[VRAMWatchdog] Monitor loop started")

        while self._is_running:
            try:
                # Query GPU info
                gpu_info = await self.get_current_usage()

                if gpu_info:
                    self._last_gpu_info = gpu_info

                    # Determine new status
                    new_status = self._determine_status(gpu_info.usage_percent)

                    # Check for status transitions
                    if new_status != self._current_status:
                        await self._handle_status_transition(
                            self._current_status, new_status, gpu_info
                        )
                        self._previous_status = self._current_status
                        self._current_status = new_status

                # Sleep until next poll
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[VRAMWatchdog] Monitor loop error: {e}")
                await asyncio.sleep(self.poll_interval)

        logger.debug("[VRAMWatchdog] Monitor loop stopped")

    def _determine_status(self, usage_percent: float) -> VRAMStatus:
        """Determine VRAM status from usage percentage."""
        if usage_percent >= self.critical_threshold:
            return VRAMStatus.CRITICAL
        elif usage_percent >= self.warning_threshold:
            return VRAMStatus.WARNING
        else:
            return VRAMStatus.NORMAL

    async def _handle_status_transition(
        self,
        old_status: VRAMStatus,
        new_status: VRAMStatus,
        gpu_info: GPUInfo
    ) -> None:
        """Handle status transition and invoke callbacks."""
        logger.info(
            f"[VRAMWatchdog] Status transition: {old_status.name} -> {new_status.name} "
            f"(VRAM: {gpu_info.usage_percent:.1f}%)"
        )

        # Invoke appropriate callbacks based on transition
        if new_status == VRAMStatus.CRITICAL:
            await self._invoke_callbacks(self._critical_callbacks, gpu_info)

        elif new_status == VRAMStatus.WARNING and old_status == VRAMStatus.NORMAL:
            await self._invoke_callbacks(self._warning_callbacks, gpu_info)

        elif new_status == VRAMStatus.NORMAL and old_status in (VRAMStatus.WARNING, VRAMStatus.CRITICAL):
            await self._invoke_callbacks(self._recovery_callbacks, gpu_info)

    async def get_current_usage(self) -> Optional[GPUInfo]:
        """
        Get current GPU memory usage.

        Returns:
            GPUInfo with current stats, or None if unavailable
        """
        if VRAMWatchdog._pynvml_available:
            return await self._query_pynvml()
        else:
            return await self._query_nvidia_smi()

    def get_status(self) -> VRAMStatus:
        """
        Get current VRAM status level.

        Returns:
            Current VRAMStatus enum value
        """
        return self._current_status

    def get_last_info(self) -> Optional[GPUInfo]:
        """
        Get most recent GPU info from last poll.

        Returns:
            Last GPUInfo collected, or None if never polled
        """
        return self._last_gpu_info

    # ==================== GPU Interaction ====================

    async def _query_nvidia_smi(self) -> Optional[GPUInfo]:
        """
        Query GPU info using nvidia-smi command.

        Fallback method when pynvml is not available.

        Returns:
            GPUInfo parsed from nvidia-smi output
        """
        try:
            # Check if nvidia-smi is available
            nvidia_smi = shutil.which("nvidia-smi")
            if not nvidia_smi:
                return None

            # Run nvidia-smi query
            cmd = [
                nvidia_smi,
                f"--id={self.device_id}",
                "--query-gpu=name,memory.total,memory.used,memory.free,temperature.gpu,utilization.gpu,power.draw",
                "--format=csv,noheader,nounits"
            ]

            # Run async subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.warning(f"[VRAMWatchdog] nvidia-smi error: {stderr.decode()}")
                return None

            # Parse output
            output = stdout.decode().strip()
            parts = [p.strip() for p in output.split(",")]

            if len(parts) < 4:
                return None

            name = parts[0]
            total_mb = float(parts[1])
            used_mb = float(parts[2])
            free_mb = float(parts[3])

            # Optional fields
            temp_c = float(parts[4]) if len(parts) > 4 and parts[4] else None
            util_pct = float(parts[5]) if len(parts) > 5 and parts[5] else None
            power_w = float(parts[6]) if len(parts) > 6 and parts[6] else None

            return GPUInfo(
                device_id=self.device_id,
                name=name,
                total_memory_mb=total_mb,
                used_memory_mb=used_mb,
                free_memory_mb=free_mb,
                temperature_c=temp_c,
                utilization_percent=util_pct,
                power_draw_w=power_w,
            )

        except Exception as e:
            logger.error(f"[VRAMWatchdog] nvidia-smi query failed: {e}")
            return None

    async def _query_pynvml(self) -> Optional[GPUInfo]:
        """
        Query GPU info using pynvml library.

        Preferred method for GPU monitoring (faster, more reliable).

        Returns:
            GPUInfo from pynvml queries
        """
        if not VRAMWatchdog._pynvml_available or not VRAMWatchdog._pynvml:
            return None

        try:
            pynvml = VRAMWatchdog._pynvml

            # Get device handle
            handle = pynvml.nvmlDeviceGetHandleByIndex(self.device_id)

            # Get device name
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode('utf-8')

            # Get memory info
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_mb = mem_info.total / (1024 * 1024)
            used_mb = mem_info.used / (1024 * 1024)
            free_mb = mem_info.free / (1024 * 1024)

            # Get temperature (optional)
            try:
                temp_c = pynvml.nvmlDeviceGetTemperature(
                    handle, pynvml.NVML_TEMPERATURE_GPU
                )
            except Exception:
                temp_c = None

            # Get utilization (optional)
            try:
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                util_pct = util.gpu
            except Exception:
                util_pct = None

            # Get power draw (optional)
            try:
                power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
                power_w = power_mw / 1000.0
            except Exception:
                power_w = None

            return GPUInfo(
                device_id=self.device_id,
                name=name,
                total_memory_mb=total_mb,
                used_memory_mb=used_mb,
                free_memory_mb=free_mb,
                temperature_c=temp_c,
                utilization_percent=util_pct,
                power_draw_w=power_w,
            )

        except Exception as e:
            logger.error(f"[VRAMWatchdog] pynvml query failed: {e}")
            return None

    @staticmethod
    def is_gpu_available() -> bool:
        """
        Check if a compatible NVIDIA GPU is available.

        Returns:
            True if nvidia-smi or pynvml can detect a GPU
        """
        # Check pynvml first
        if VRAMWatchdog._pynvml_available:
            try:
                pynvml = VRAMWatchdog._pynvml
                device_count = pynvml.nvmlDeviceGetCount()
                return device_count > 0
            except Exception:
                pass

        # Fallback to nvidia-smi
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [nvidia_smi, "-L"],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0 and b"GPU" in result.stdout
            except Exception:
                pass

        return False

    @staticmethod
    def get_gpu_count() -> int:
        """
        Get the number of available GPUs.

        Returns:
            Number of GPUs, or 0 if none available
        """
        if VRAMWatchdog._pynvml_available and VRAMWatchdog._pynvml:
            try:
                return VRAMWatchdog._pynvml.nvmlDeviceGetCount()
            except Exception:
                pass

        # Fallback to nvidia-smi
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [nvidia_smi, "-L"],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode == 0:
                    lines = result.stdout.decode().strip().split('\n')
                    return len([l for l in lines if l.startswith("GPU")])
            except Exception:
                pass

        return 0

    # ==================== Utilities ====================

    def set_thresholds(
        self,
        warning: Optional[float] = None,
        critical: Optional[float] = None
    ) -> None:
        """
        Update monitoring thresholds at runtime.

        Args:
            warning: New warning threshold (0-100)
            critical: New critical threshold (0-100)

        Raises:
            ValueError: If critical <= warning or values out of range
        """
        new_warning = warning if warning is not None else self.warning_threshold
        new_critical = critical if critical is not None else self.critical_threshold

        # Validate
        if not (0 <= new_warning <= 100):
            raise ValueError(f"Warning threshold must be 0-100, got {new_warning}")
        if not (0 <= new_critical <= 100):
            raise ValueError(f"Critical threshold must be 0-100, got {new_critical}")
        if new_critical <= new_warning:
            raise ValueError(
                f"Critical ({new_critical}) must be greater than warning ({new_warning})"
            )

        self.warning_threshold = new_warning
        self.critical_threshold = new_critical

        logger.info(
            f"[VRAMWatchdog] Thresholds updated: "
            f"warning={new_warning}%, critical={new_critical}%"
        )

    async def force_check(self) -> Optional[GPUInfo]:
        """
        Force an immediate VRAM check outside normal polling.

        Returns:
            Current GPUInfo

        Useful for checking status before starting a large task.
        """
        gpu_info = await self.get_current_usage()

        if gpu_info:
            self._last_gpu_info = gpu_info
            new_status = self._determine_status(gpu_info.usage_percent)

            if new_status != self._current_status:
                await self._handle_status_transition(
                    self._current_status, new_status, gpu_info
                )
                self._previous_status = self._current_status
                self._current_status = new_status

        return gpu_info

    def to_dict(self) -> Dict[str, Any]:
        """
        Export current watchdog state as dictionary.

        Returns:
            Dict with current status, thresholds, and last GPU info
        """
        uptime = None
        if self._start_time:
            uptime = (datetime.now() - self._start_time).total_seconds()

        return {
            "is_running": self._is_running,
            "device_id": self.device_id,
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
            "poll_interval": self.poll_interval,
            "current_status": self._current_status.name,
            "previous_status": self._previous_status.name,
            "uptime_seconds": uptime,
            "last_gpu_info": self._last_gpu_info.to_dict() if self._last_gpu_info else None,
            "callbacks_registered": {
                "warning": len(self._warning_callbacks),
                "critical": len(self._critical_callbacks),
                "recovery": len(self._recovery_callbacks),
            },
            "pynvml_available": VRAMWatchdog._pynvml_available,
        }

    async def health_check(self) -> Dict[str, Any]:
        """
        Perform health check of the watchdog.

        Returns:
            Dict with health status
        """
        gpu_info = await self.get_current_usage()

        return {
            "healthy": gpu_info is not None,
            "is_running": self._is_running,
            "current_status": self._current_status.name,
            "gpu_available": self.is_gpu_available(),
            "last_check": self._last_gpu_info.timestamp.isoformat() if self._last_gpu_info else None,
        }


# =============================================================================
# Factory Function
# =============================================================================

def create_watchdog(
    warning_threshold: float = 80.0,
    critical_threshold: float = 99.0,
    poll_interval: float = 1.0,
    device_id: int = 0
) -> VRAMWatchdog:
    """
    Factory function to create a VRAMWatchdog instance.

    Args:
        warning_threshold: VRAM % to trigger warning
        critical_threshold: VRAM % to trigger critical
        poll_interval: Seconds between checks
        device_id: GPU device to monitor

    Returns:
        Configured VRAMWatchdog instance
    """
    return VRAMWatchdog(
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        poll_interval=poll_interval,
        device_id=device_id,
    )
