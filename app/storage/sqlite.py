"""
sqlite.py

Centralized, production-grade SQLite connection factory for LifeOS.

Guarantees:
- WAL (Write-Ahead Logging) journal mode for concurrent readers and writers
- Explicit busy_timeout to prevent immediate 'database is locked' errors
- Managed connection lifecycle: commits on context-manager exit, rolls back on error,
  and guarantees close() in finally to prevent file descriptor and lock leaks
- Foreign key constraint enforcement where relational integrity is required
- Thread and process-safe connection configuration
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ManagedConnection(sqlite3.Connection):
    """
    SQLite connection that automatically closes itself upon exiting a transaction context.
    Acts as a standard sqlite3.Connection in all contexts, while ensuring no connection leaks.
    """

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool | None:
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def connect_sqlite(
    db_path: Path | str,
    enable_foreign_keys: bool = False,
    timeout: float = 10.0,
    wal_mode: bool = True,
) -> ManagedConnection:
    """
    Open and configure a SQLite connection with production concurrency defaults.

    Args:
        db_path: Filesystem path to the SQLite database file.
        enable_foreign_keys: If True, executes PRAGMA foreign_keys = ON.
        timeout: Busy handler timeout in seconds (default 10.0s).
        wal_mode: If True, sets PRAGMA journal_mode = WAL and synchronous = NORMAL.

    Returns:
        Configured ManagedConnection instance with row_factory set to sqlite3.Row.
    """
    path_str = str(db_path)

    if path_str != ":memory:" and not path_str.startswith("file:"):
        path = Path(db_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        target_path = str(path)
    else:
        target_path = path_str

    conn = sqlite3.connect(
        target_path,
        timeout=timeout,
        factory=ManagedConnection,
    )
    conn.row_factory = sqlite3.Row

    if wal_mode and target_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")

    if enable_foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON")

    return conn
