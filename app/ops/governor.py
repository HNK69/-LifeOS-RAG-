"""
governor.py

Resource Governor for LifeOS Milestone C2.
Monitors dynamic host resource utilization:
- CPU percentage
- RAM pressure
- Battery status (AC line vs. battery) and battery percentage
- Thermal throttling status
- User activity / idle duration

Enforces intelligent throttling and pause/resume behavior on heavy background
tasks (such as document ingestion and vector extraction) without consuming
retries, expiring job leases, or starving interactive user workflows.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("lifeos.ops.governor")


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class SystemMetrics:
    """Snapshot of current host hardware and user activity state."""
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    on_battery: bool = False
    battery_percent: float | None = None
    thermal_throttling: bool = False
    user_idle_seconds: float = 9999.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": round(self.cpu_percent, 2),
            "ram_percent": round(self.ram_percent, 2),
            "on_battery": self.on_battery,
            "battery_percent": self.battery_percent,
            "thermal_throttling": self.thermal_throttling,
            "user_idle_seconds": round(self.user_idle_seconds, 2),
        }


# =============================================================================
# WINDOWS CTYPES STRUCTURES
# =============================================================================

if sys.platform == "win32":
    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("SystemStatusFlag", ctypes.c_byte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        ]

    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    class _LASTINPUTINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_uint),
            ("dwTime", ctypes.c_ulong),
        ]

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", ctypes.c_ulong),
            ("dwHighDateTime", ctypes.c_ulong),
        ]


# =============================================================================
# HARDWARE SAMPLER
# =============================================================================

class HardwareSampler:
    """Low-overhead, zero-dependency host resource sampler."""

    def __init__(self):
        self._prev_idle: int | None = None
        self._prev_total: int | None = None
        self._last_sample_time: float = 0.0
        self._cached_cpu: float = 0.0

    def sample(self) -> SystemMetrics:
        """Sample host metrics using platform APIs."""
        if sys.platform == "win32":
            return self._sample_windows()
        return self._sample_posix()

    def _sample_windows(self) -> SystemMetrics:
        # 1. Battery & Power
        on_battery = False
        battery_pct: float | None = None
        thermal_throttling = False

        try:
            sps = _SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
                # ACLineStatus: 0=Offline (Battery), 1=Online (AC), 255=Unknown
                on_battery = sps.ACLineStatus == 0
                if sps.BatteryLifePercent != 255:
                    battery_pct = float(sps.BatteryLifePercent)
                # BatteryFlag: 8=Charging, 128=No system battery
                # If battery temperature or status indicates throttle
                if sps.BatteryFlag & 4:  # Critical battery state
                    thermal_throttling = True
        except Exception as exc:
            logger.debug(f"Power status sampling error: {exc}")

        # 2. RAM Load
        ram_percent = 0.0
        try:
            mse = _MEMORYSTATUSEX()
            mse.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mse)):
                ram_percent = float(mse.dwMemoryLoad)
        except Exception as exc:
            logger.debug(f"Memory sampling error: {exc}")

        # 3. CPU Load
        cpu_percent = self._cached_cpu
        now = time.time()
        if now - self._last_sample_time >= 0.5:
            try:
                idle = _FILETIME()
                kern = _FILETIME()
                user = _FILETIME()
                if ctypes.windll.kernel32.GetSystemTimes(
                    ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)
                ):
                    i_val = (idle.dwHighDateTime << 32) + idle.dwLowDateTime
                    k_val = (kern.dwHighDateTime << 32) + kern.dwLowDateTime
                    u_val = (user.dwHighDateTime << 32) + user.dwLowDateTime
                    tot_val = k_val + u_val

                    if self._prev_idle is not None and self._prev_total is not None:
                        d_idle = i_val - self._prev_idle
                        d_total = tot_val - self._prev_total
                        if d_total > 0:
                            cpu_percent = max(0.0, min(100.0, ((d_total - d_idle) * 100.0) / d_total))
                            self._cached_cpu = cpu_percent

                    self._prev_idle = i_val
                    self._prev_total = tot_val
                    self._last_sample_time = now
            except Exception as exc:
                logger.debug(f"CPU sampling error: {exc}")

        # 4. User Idle Time
        user_idle_seconds = 9999.0
        try:
            lii = _LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                tick_count = ctypes.windll.kernel32.GetTickCount()
                millis = max(0, tick_count - lii.dwTime)
                user_idle_seconds = millis / 1000.0
        except Exception as exc:
            logger.debug(f"User idle sampling error: {exc}")

        return SystemMetrics(
            cpu_percent=cpu_percent,
            ram_percent=ram_percent,
            on_battery=on_battery,
            battery_percent=battery_pct,
            thermal_throttling=thermal_throttling,
            user_idle_seconds=user_idle_seconds,
        )

    def _sample_posix(self) -> SystemMetrics:
        """Fallback sampler for POSIX / Linux environments."""
        ram_percent = 0.0
        on_battery = False

        # Linux /proc/meminfo
        if os.path.exists("/proc/meminfo"):
            try:
                mem: dict[str, int] = {}
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            key = parts[0].strip()
                            val = int(parts[1].split()[0].strip())
                            mem[key] = val
                total = mem.get("MemTotal", 1)
                avail = mem.get("MemAvailable", total)
                ram_percent = ((total - avail) / total) * 100.0
            except Exception:
                pass

        # Linux battery supply
        if os.path.exists("/sys/class/power_supply"):
            try:
                for entry in os.listdir("/sys/class/power_supply"):
                    if entry.startswith("BAT"):
                        stat_path = os.path.join("/sys/class/power_supply", entry, "status")
                        if os.path.exists(stat_path):
                            with open(stat_path) as f:
                                status = f.read().strip().lower()
                                if status == "discharging":
                                    on_battery = True
                                    break
            except Exception:
                pass

        return SystemMetrics(
            cpu_percent=10.0,
            ram_percent=ram_percent,
            on_battery=on_battery,
            battery_percent=50.0 if on_battery else None,
            thermal_throttling=False,
            user_idle_seconds=9999.0,
        )


# =============================================================================
# HARDWARE GOVERNOR
# =============================================================================

class HardwareGovernor:
    """
    Intelligent hardware governor managing background workload pacing.
    Decides whether compute-intensive background ingestion should proceed
    or temporarily pause to preserve system responsiveness and battery.
    """

    def __init__(
        self,
        enabled: bool | None = None,
        max_cpu_percent: float | None = None,
        max_ram_percent: float | None = None,
        pause_on_battery: bool | None = None,
        min_user_idle_seconds: float | None = None,
        sampler: HardwareSampler | None = None,
    ):
        try:
            import config as cfg
        except ImportError:
            import app.config as cfg

        self.enabled = enabled if enabled is not None else getattr(cfg, "GOVERNOR_ENABLED", True)
        self.max_cpu_percent = max_cpu_percent if max_cpu_percent is not None else getattr(cfg, "GOVERNOR_MAX_CPU_PERCENT", 80.0)
        self.max_ram_percent = max_ram_percent if max_ram_percent is not None else getattr(cfg, "GOVERNOR_MAX_RAM_PERCENT", 85.0)
        self.pause_on_battery = pause_on_battery if pause_on_battery is not None else getattr(cfg, "GOVERNOR_PAUSE_ON_BATTERY", True)
        self.min_user_idle_seconds = min_user_idle_seconds if min_user_idle_seconds is not None else getattr(cfg, "GOVERNOR_MIN_USER_IDLE_SECONDS", 0.0)

        self._sampler = sampler or HardwareSampler()
        self._override_metrics: SystemMetrics | None = None
        self._lock = threading.Lock()

    def set_override_metrics(self, metrics: SystemMetrics | None) -> None:
        """Set or clear forced metrics for testing and deterministic validation."""
        with self._lock:
            self._override_metrics = metrics

    def sample_metrics(self) -> SystemMetrics:
        """Return the current metrics (override if configured, or real sampled)."""
        with self._lock:
            if self._override_metrics is not None:
                return self._override_metrics
        return self._sampler.sample()

    def should_pause_ingestion(self) -> tuple[bool, str | None]:
        """
        Evaluate current metrics against configured thresholds.
        Returns:
            (True, reason_str) if ingestion should pause.
            (False, None) if ingestion may safely proceed.
        """
        if not self.enabled:
            return False, None

        metrics = self.sample_metrics()

        # 1. Thermal safety throttle
        if metrics.thermal_throttling:
            return True, "thermal_throttle"

        # 2. Battery power preservation
        if self.pause_on_battery and metrics.on_battery:
            return True, "on_battery"

        # 3. CPU pressure threshold
        if metrics.cpu_percent > self.max_cpu_percent:
            return True, "high_cpu"

        # 4. RAM pressure threshold
        if metrics.ram_percent > self.max_ram_percent:
            return True, "high_ram"

        # 5. User activity threshold (e.g. active typing/gaming)
        if self.min_user_idle_seconds > 0.0 and metrics.user_idle_seconds < self.min_user_idle_seconds:
            return True, "user_active"

        return False, None


# Global Singleton
_global_governor: HardwareGovernor | None = None
_governor_lock = threading.Lock()


def get_governor() -> HardwareGovernor:
    """Retrieve the global singleton HardwareGovernor instance."""
    global _global_governor
    with _governor_lock:
        if _global_governor is None:
            _global_governor = HardwareGovernor()
        return _global_governor
