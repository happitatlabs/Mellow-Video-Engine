"""
Metrics Collector - Request-path metrics with async flush to DB.

CRITICAL INVARIANTS:
- push() MUST NEVER call get_memory_db() or save_metric() on any code path.
  Request path only appends to in-memory deque; no DB I/O.
- Flush runs in background: DB writes are done in run_in_executor (one batch per
  call) so the event loop is not blocked; SQLite write serialization is per-batch.
- Shutdown: stop() cancels the flush task then runs _flush_sync() in the caller
  so remaining events are written. Graceful shutdown (lifespan) is required for
  final flush; SIGINT/crash may lose in-memory queue.

Env flags (default OFF / safe):
  MELLOW_METRICS_ENABLED=1
  MELLOW_METRICS_ASYNC_FLUSH=1
  MELLOW_METRICS_FLUSH_INTERVAL_MS=500
  MELLOW_METRICS_FLUSH_BATCH_SIZE=50
  MELLOW_METRICS_MAX_QUEUE_SIZE=5000  (overflow: drop oldest, log warning)
"""

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Deque, Any, List

logger = logging.getLogger(__name__)


@dataclass
class MetricEvent:
    metric_id: str
    category: str
    value: float
    unit: str
    timestamp: Optional[datetime] = None


_collector: Optional["MetricsCollector"] = None
_collector_lock = threading.Lock()


def get_metrics_collector() -> Optional["MetricsCollector"]:
    """Return the global MetricsCollector instance if enabled and initialized."""
    with _collector_lock:
        return _collector


def init_metrics_collector(
    enabled: bool,
    async_flush: bool = True,
    flush_interval_ms: int = 500,
    flush_batch_size: int = 50,
    max_queue_size: int = 5000,
) -> Optional["MetricsCollector"]:
    """
    Initialize the global collector. Call once at app startup.
    Returns None if not enabled.
    """
    global _collector
    with _collector_lock:
        if _collector is not None:
            return _collector
        if not enabled:
            return None
        _collector = MetricsCollector(
            async_flush=async_flush,
            flush_interval_ms=flush_interval_ms,
            flush_batch_size=flush_batch_size,
            max_queue_size=max_queue_size,
        )
        logger.info(
            "[MetricsCollector] Initialized (async_flush=%s, interval_ms=%s, batch_size=%s, max_queue=%s)",
            async_flush, flush_interval_ms, flush_batch_size, max_queue_size,
        )
        return _collector


def shutdown_metrics_collector() -> None:
    """
    Stop background flush and clear global reference. Call from graceful shutdown
    (e.g. FastAPI lifespan). Performs one synchronous flush of remaining queue
    so no data loss. SIGINT/crash without this call will lose queued metrics.
    """
    global _collector
    with _collector_lock:
        if _collector is not None:
            _collector.stop()
            _collector = None
            logger.info("[MetricsCollector] Shutdown complete")


class MetricsCollector:
    """
    In-memory queue for metrics; background task flushes to MemoryDatabase.
    Push is non-blocking (no DB write on request path when async_flush=True).
    """

    def __init__(
        self,
        async_flush: bool = True,
        flush_interval_ms: int = 500,
        flush_batch_size: int = 50,
        max_queue_size: int = 5000,
    ):
        self._queue: Deque[MetricEvent] = deque()
        self._lock = threading.Lock()
        self._db_write_lock = threading.Lock()
        self._async_flush = async_flush
        self._flush_interval_ms = flush_interval_ms
        self._flush_batch_size = flush_batch_size
        self._max_queue_size = max(1, max_queue_size)
        self._running = False
        self._flush_task: Optional[asyncio.Task] = None

    def push(
        self,
        category: str,
        value: float,
        unit: str,
        metric_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """
        Enqueue one metric. Never blocks; NEVER calls get_memory_db() or save_metric().
        Request path only: append to in-memory deque. Flush is done by background
        task or shutdown _flush_sync() only.
        """
        mid = metric_id or str(uuid.uuid4())
        ts = timestamp or datetime.utcnow()
        event = MetricEvent(metric_id=mid, category=category, value=value, unit=unit, timestamp=ts)
        with self._lock:
            if len(self._queue) >= self._max_queue_size:
                self._queue.popleft()
                logger.warning(
                    "[MetricsCollector] Queue overflow (max=%s): dropped oldest metric",
                    self._max_queue_size,
                )
            self._queue.append(event)
        logger.debug("[MetricsCollector] Queued %s=%s %s (id=%s)", category, value, unit, mid[:8])

    def push_ttft(self, ttft_ms: float, request_id: Optional[str] = None) -> None:
        """Convenience: enqueue TTFT in ms."""
        self.push("TTFT_MS", ttft_ms, "ms", metric_id=request_id)

    def push_tps(self, tps: float, request_id: Optional[str] = None) -> None:
        """Convenience: enqueue TPS (tokens per second)."""
        self.push("TPS", tps, "tokens/s", metric_id=request_id)

    def push_tokens(self, tokens_in: int, tokens_out: int, request_id: Optional[str] = None) -> None:
        """Convenience: enqueue TOKENS_IN and TOKENS_OUT (one ID for the request)."""
        base_id = request_id or str(uuid.uuid4())
        self.push("TOKENS_IN", float(tokens_in), "tokens", metric_id=f"{base_id}_in")
        self.push("TOKENS_OUT", float(tokens_out), "tokens", metric_id=f"{base_id}_out")

    def push_observation_violation(self, request_id: Optional[str] = None) -> None:
        """Convenience: enqueue one OBSERVATION_VIOLATION (value=1)."""
        self.push("OBSERVATION_VIOLATION", 1.0, "count", metric_id=request_id)

    def push_infer_ms(self, infer_ms: float, request_id: Optional[str] = None) -> None:
        """Phase 1: total inference duration (non-stream path)."""
        self.push("INFER_MS", infer_ms, "ms", metric_id=request_id)

    def push_tps_approx(self, tps_approx: float, request_id: Optional[str] = None) -> None:
        """Phase 1: tokens_out / duration_sec (non-stream path)."""
        self.push("TPS_APPROX", tps_approx, "tokens/s", metric_id=request_id)

    def push_ttft_measured(self, measured: bool, request_id: Optional[str] = None) -> None:
        """Phase 1: 1=stream (TTFT measured), 0=chat (TTFT not measured)."""
        self.push("TTFT_MEASURED", 1.0 if measured else 0.0, "bool", metric_id=request_id)

    def start_background_flush(self) -> None:
        """Start the async flush loop. Call from lifespan after event loop is running."""
        if not self._async_flush or self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())
        logger.info("[MetricsCollector] Background flush started")

    def stop(self) -> None:
        """
        Stop flush loop and perform one final synchronous flush. Call from
        shutdown only. (1) Cancel flush task, (2) wait for it to finish so no
        concurrent _write_batch_sync, (3) then run final sync flush.
        """
        self._running = False
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            for _ in range(100):
                if self._flush_task.done():
                    break
                time.sleep(0.05)
        self._flush_sync()

    def _flush_sync(self) -> None:
        """Flush all queued events to DB (sync). Used on shutdown only. Holds _db_write_lock so no concurrent write with _write_batch_sync."""
        with self._db_write_lock:
            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            count = 0
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    batch = []
                    for _ in range(min(self._flush_batch_size, len(self._queue))):
                        batch.append(self._queue.popleft())
                for ev in batch:
                    try:
                        db.save_metric(ev.metric_id, ev.category, ev.value, ev.unit, ev.timestamp)
                        count += 1
                    except Exception as e:
                        logger.warning("[MetricsCollector] Flush save failed: %s", e)
            if count:
                logger.info("[MetricsCollector] Sync flush wrote %d metrics", count)

    def _write_batch_sync(self, batch: List[MetricEvent]) -> None:
        """Run in executor: write one batch to DB. Holds _db_write_lock so never concurrent with _flush_sync."""
        with self._db_write_lock:
            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            for ev in batch:
                try:
                    db.save_metric(ev.metric_id, ev.category, ev.value, ev.unit, ev.timestamp)
                except Exception as e:
                    logger.warning("[MetricsCollector] Batch write failed for %s: %s", ev.metric_id[:8], e)

    async def _flush_loop(self) -> None:
        """
        Background: every flush_interval_ms, flush one batch in executor.
        One run_in_executor per batch (not per event) to avoid blocking event
        loop and to serialize SQLite writes per batch (no lock contention).
        """
        interval_sec = self._flush_interval_ms / 1000.0
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                break
            batch = []
            with self._lock:
                for _ in range(min(self._flush_batch_size, len(self._queue))):
                    batch.append(self._queue.popleft())
            if not batch:
                continue
            try:
                await loop.run_in_executor(None, self._write_batch_sync, batch)
            except Exception as e:
                logger.warning("[MetricsCollector] Async flush batch failed: %s", e)
            logger.debug("[MetricsCollector] Flushed %d metrics", len(batch))
