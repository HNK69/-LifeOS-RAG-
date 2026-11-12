"""
metrics.py

In-memory operational metrics collector for LifeOS Phase 12.
Provides thread-safe collection of request counters, latencies, error distributions,
subsystem events, and worker states with zero external dependencies.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any


class MetricsCollector:
    """Thread-safe operational metrics registry."""

    def __init__(self):
        self._lock = threading.Lock()
        self._start_time = time.time()
        self.reset()

    def reset(self) -> None:
        """Reset all metrics (used for lifecycle restarts and testing)."""
        with self._lock:
            self._start_time = time.time()
            self._requests_total = 0
            self._requests_by_status: dict[str, int] = {
                "2xx": 0,
                "4xx": 0,
                "5xx": 0,
                "other": 0,
            }
            self._requests_by_endpoint: dict[str, int] = {}
            self._latencies_ms: list[float] = []
            self._auth_failures = 0
            self._rate_limit_exceeded = 0
            self._queries_success = 0
            self._queries_failed = 0
            self._ingestions_success = 0
            self._ingestions_failed = 0
            self._reindexes_success = 0
            self._reindexes_failed = 0

    def record_request(self, method: str, path: str, status_code: int, duration_ms: float) -> None:
        """Record an incoming HTTP request execution."""
        with self._lock:
            self._requests_total += 1
            endpoint = f"{method} {path}"
            self._requests_by_endpoint[endpoint] = self._requests_by_endpoint.get(endpoint, 0) + 1

            if 200 <= status_code < 300:
                self._requests_by_status["2xx"] += 1
            elif 400 <= status_code < 500:
                self._requests_by_status["4xx"] += 1
                if status_code == 401:
                    self._auth_failures += 1
                elif status_code == 429:
                    self._rate_limit_exceeded += 1
            elif 500 <= status_code < 600:
                self._requests_by_status["5xx"] += 1
            else:
                self._requests_by_status["other"] += 1

            # Bounded latency ring (retain latest 1,000 for quantile calculation)
            if len(self._latencies_ms) >= 1000:
                self._latencies_ms.pop(0)
            self._latencies_ms.append(duration_ms)

    def record_query(self, success: bool = True) -> None:
        with self._lock:
            if success:
                self._queries_success += 1
            else:
                self._queries_failed += 1

    def record_ingestion(self, success: bool = True) -> None:
        with self._lock:
            if success:
                self._ingestions_success += 1
            else:
                self._ingestions_failed += 1

    def record_reindex(self, success: bool = True) -> None:
        with self._lock:
            if success:
                self._reindexes_success += 1
            else:
                self._reindexes_failed += 1

    def get_metrics(self) -> dict[str, Any]:
        """Compute and return an operational snapshot of system performance."""
        with self._lock:
            uptime = round(time.time() - self._start_time, 2)
            latencies = sorted(self._latencies_ms)
            count = len(latencies)

            avg_lat = round(sum(latencies) / count, 2) if count > 0 else 0.0
            p50_lat = round(latencies[int(count * 0.50)], 2) if count > 0 else 0.0
            p95_lat = round(latencies[int(count * 0.95)], 2) if count > 0 else 0.0

            # Safe check of background worker status without external side effects
            watcher_active = False
            try:
                from app.knowledge.watcher import is_watcher_running
                watcher_active = is_watcher_running()
            except Exception:
                pass

            reindex_active = False
            try:
                from app.knowledge.coordination import is_reindexing_active
                reindex_active = is_reindexing_active()
            except Exception:
                pass

            return {
                "uptime_seconds": uptime,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "http_requests": {
                    "total": self._requests_total,
                    "by_status": dict(self._requests_by_status),
                    "by_endpoint": dict(self._requests_by_endpoint),
                    "auth_failures": self._auth_failures,
                    "rate_limit_exceeded": self._rate_limit_exceeded,
                },
                "latency_ms": {
                    "count": count,
                    "average": avg_lat,
                    "p50": p50_lat,
                    "p95": p95_lat,
                },
                "subsystems": {
                    "queries": {
                        "success": self._queries_success,
                        "failed": self._queries_failed,
                    },
                    "ingestions": {
                        "success": self._ingestions_success,
                        "failed": self._ingestions_failed,
                    },
                    "reindexing": {
                        "success": self._reindexes_success,
                        "failed": self._reindexes_failed,
                        "is_active": reindex_active,
                    },
                    "watcher": {
                        "is_active": watcher_active,
                    },
                },
            }


metrics_collector = MetricsCollector()
