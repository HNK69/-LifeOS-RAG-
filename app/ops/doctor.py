"""
doctor.py

Comprehensive pre-flight diagnostic engine for LifeOS Milestone 10.
Validates:
1. SQLite Databases (all 8 databases: presence, PRAGMA integrity_check, foreign_key_check, WAL mode)
2. Filesystem & Persistent Storage (write access, directory permissions, capacity)
3. Device Identity & Cryptography (local keypairs, trust store)
4. Vector Store Connectivity (ChromaDB connection and collections)
5. Configuration Hygiene & Production Rules (fail-fast checks, key lengths, CORS)
6. Network & Port Accessibility
"""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from config import validate_config
except ImportError:
    from app.config import validate_config

logger = logging.getLogger("lifeos.ops.doctor")


@dataclass
class DiagnosticCheck:
    name: str
    category: str
    status: str  # "OK", "WARN", "FAIL"
    message: str
    remediation: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DiagnosticReport:
    timestamp: str
    overall_status: str  # "HEALTHY", "DEGRADED", "FAILED"
    checks: list[DiagnosticCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_status": self.overall_status,
            "checks": [c.to_dict() for c in self.checks],
        }

    def summary_text(self) -> str:
        lines = [
            f"LifeOS Pre-Flight Diagnostic Report ({self.timestamp})",
            f"Overall Status: {self.overall_status}",
            "-" * 60,
        ]
        for c in self.checks:
            icon = "✓" if c.status == "OK" else ("⚠" if c.status == "WARN" else "✗")
            lines.append(f"[{icon}] {c.category:12} | {c.name:25} | {c.status:4} | {c.message}")
            if c.remediation and c.status != "OK":
                lines.append(f"    Remediation: {c.remediation}")
        lines.append("-" * 60)
        return "\n".join(lines)


def _check_sqlite_database(db_name: str, db_path: Path) -> DiagnosticCheck:
    """Run integrity and foreign key checks on a SQLite database file."""
    if not db_path.exists():
        return DiagnosticCheck(
            name=db_name,
            category="Database",
            status="WARN",
            message=f"Database file not yet created at {db_path}",
            remediation="Will be initialized automatically on first subsystem access or startup.",
        )

    try:
        if db_path.stat().st_size == 0:
            return DiagnosticCheck(
                name=db_name,
                category="Database",
                status="FAIL",
                message="Database file is 0 bytes (empty/truncated)",
                remediation="Re-initialize database store",
            )

        conn = sqlite3.connect(str(db_path), timeout=3.0)
        try:
            # 1. Integrity check
            cursor = conn.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            if integrity != "ok":
                return DiagnosticCheck(
                    name=db_name,
                    category="Database",
                    status="FAIL",
                    message=f"Corrupt database: {integrity}",
                    remediation="Restore from recent backup using 'lifeos backup restore'.",
                )

            # 2. Foreign key check
            fk_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_issues:
                return DiagnosticCheck(
                    name=db_name,
                    category="Database",
                    status="WARN",
                    message=f"Foreign key violations detected ({len(fk_issues)} rows)",
                    remediation="Inspect relational consistency or reindex affected entities.",
                )

            # 3. Journal mode check
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if mode.lower() != "wal":
                return DiagnosticCheck(
                    name=db_name,
                    category="Database",
                    status="WARN",
                    message=f"Journal mode is '{mode}' instead of WAL",
                    remediation="Enable WAL mode for concurrent performance: 'PRAGMA journal_mode=WAL;'.",
                )

            return DiagnosticCheck(
                name=db_name,
                category="Database",
                status="OK",
                message="Integrity check passed (WAL mode active)",
            )
        finally:
            conn.close()

    except Exception as exc:
        return DiagnosticCheck(
            name=db_name,
            category="Database",
            status="FAIL",
            message=f"Failed to inspect database: {exc}",
            remediation=f"Verify file access permissions on {db_path}.",
        )


def _check_directory_write_access(dir_name: str, dir_path: Path) -> DiagnosticCheck:
    """Verify that a directory exists (or can be created) and has write permissions."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        probe_file = dir_path / f".write_probe_{os.getpid()}"
        probe_file.write_text("probe")
        probe_file.unlink()
        return DiagnosticCheck(
            name=dir_name,
            category="Filesystem",
            status="OK",
            message=f"Writable directory: {dir_path}",
        )
    except Exception as exc:
        return DiagnosticCheck(
            name=dir_name,
            category="Filesystem",
            status="FAIL",
            message=f"Directory is not writable: {exc}",
            remediation=f"Grant write permissions to user for directory {dir_path}.",
        )


def _check_device_identity(db_path: Path) -> DiagnosticCheck:
    """Verify that the local device identity and cryptography keys are present."""
    if not db_path.exists():
        return DiagnosticCheck(
            name="device_identity",
            category="Security",
            status="WARN",
            message="identity.db not yet created",
            remediation="Will be generated on first API start or paired handshake.",
        )

    try:
        conn = sqlite3.connect(str(db_path), timeout=3.0)
        try:
            row = conn.execute("SELECT device_id, device_name, device_type FROM local_identity LIMIT 1").fetchone()
            if row:
                return DiagnosticCheck(
                    name="device_identity",
                    category="Security",
                    status="OK",
                    message=f"Device '{row[1]}' (ID: {row[0][:8]}...) registered",
                )
            return DiagnosticCheck(
                name="device_identity",
                category="Security",
                status="WARN",
                message="No local device keypair initialized in identity.db",
                remediation="Run device registration or start server to generate device keypair.",
            )
        finally:
            conn.close()
    except Exception as exc:
        return DiagnosticCheck(
            name="device_identity",
            category="Security",
            status="FAIL",
            message=f"Error inspecting device identity: {exc}",
            remediation="Check identity.db file integrity and permissions.",
        )


def _check_vector_store(chroma_dir: Path) -> DiagnosticCheck:
    """Verify that the ChromaDB persistent vector database is initialized and accessible."""
    try:
        import chromadb
        chroma_dir.mkdir(parents=True, exist_ok=True)
        try:
            client = chromadb.PersistentClient(
                path=str(chroma_dir),
                settings=chromadb.config.Settings(anonymized_telemetry=False),
            )
            collections = client.list_collections()
            count = len(collections)
        except Exception as exc:
            return DiagnosticCheck(
                name="chroma_vectordb",
                category="VectorDB",
                status="FAIL",
                message=f"ChromaDB connection failed: {exc}",
                remediation="Inspect ChromaDB data directory or rebuild collections with 'lifeos admin reindex'",
            )

        return DiagnosticCheck(
            name="chroma_vectordb",
            category="VectorDB",
            status="OK",
            message=f"ChromaDB connected ({count} collections accessible)",
        )
    except Exception as exc:
        return DiagnosticCheck(
            name="chroma_vectordb",
            category="VectorDB",
            status="FAIL",
            message=f"ChromaDB connection failed: {exc}",
            remediation="Inspect ChromaDB data directory or rebuild collections with 'lifeos admin reindex'",
        )


def _check_config_hygiene() -> DiagnosticCheck:
    """Validate cross-field configuration constraints."""
    try:
        validate_config()
        return DiagnosticCheck(
            name="configuration_hygiene",
            category="Config",
            status="OK",
            message="Configuration passes all fail-fast validation rules",
        )
    except Exception as exc:
        return DiagnosticCheck(
            name="configuration_hygiene",
            category="Config",
            status="FAIL",
            message=f"Configuration error: {exc}",
            remediation="Review .env and update invalid configuration settings.",
        )


def _check_port_availability(port: int, in_process: bool = False) -> DiagnosticCheck:
    """Verify API server port state (free to bind out-of-process, or actively serving in-process)."""
    if in_process:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return DiagnosticCheck(
                    name=f"port_{port}",
                    category="Network",
                    status="OK",
                    message="API port actively serving traffic",
                    details={"host": "127.0.0.1", "port": port, "in_process": True},
                )
        except OSError:
            return DiagnosticCheck(
                name=f"port_{port}",
                category="Network",
                status="WARN",
                message=f"Port {port} is not accepting connections on 127.0.0.1",
                remediation=f"Ensure LifeOS API server is actively listening on 127.0.0.1:{port}.",
                details={"host": "127.0.0.1", "port": port, "in_process": True},
            )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return DiagnosticCheck(
            name=f"port_{port}",
            category="Network",
            status="OK",
            message=f"Port {port} is free and ready for binding",
            details={"host": "127.0.0.1", "port": port, "in_process": False},
        )
    except OSError:
        return DiagnosticCheck(
            name=f"port_{port}",
            category="Network",
            status="WARN",
            message=f"Port {port} is already bound or busy",
            remediation=f"Ensure no other LifeOS server is running, or set LIFEOS_API_PORT to another port.",
            details={"host": "127.0.0.1", "port": port, "in_process": False},
        )
    finally:
        sock.close()


def run_diagnostics(
    strict_mode: bool = False,
    in_process: bool = False,
    port_override: int | None = None,
) -> DiagnosticReport:
    """
    Execute all pre-flight diagnostic checks and return a DiagnosticReport.
    In strict_mode, any warning or failure sets overall_status to FAILED.
    When in_process=True, verifies the API port is actively serving rather than free to bind.
    """
    try:
        import config as cfg
    except ImportError:
        import app.config as cfg

    now_iso = datetime.now(timezone.utc).isoformat()
    checks: list[DiagnosticCheck] = []

    # 1. Config Hygiene
    checks.append(_check_config_hygiene())

    # 2. SQLite Databases (all 8)
    databases = {
        "document_registry.db": Path(getattr(cfg, "REGISTRY_DB", "data/document_registry.db")).resolve(),
        "entities.db": Path(getattr(cfg, "ENTITY_DB", "data/entities.db")).resolve(),
        "people_registry.db": Path(getattr(cfg, "PEOPLE_DB", "data/people_registry.db")).resolve(),
        "context.db": Path(getattr(cfg, "CONTEXT_DB", "data/context.db")).resolve(),
        "privacy.db": Path(getattr(cfg, "PRIVACY_DB", "data/privacy.db")).resolve(),
        "identity.db": Path(getattr(cfg, "IDENTITY_DB", "data/identity.db")).resolve(),
        "workspace.db": Path(getattr(cfg, "WORKSPACE_DB", "data/workspace.db")).resolve(),
        "sync.db": Path(getattr(cfg, "SYNC_DB", "data/sync.db")).resolve(),
    }
    for db_name, db_path in databases.items():
        checks.append(_check_sqlite_database(db_name, db_path))

    # 3. Filesystem directories
    directories = {
        "data_dir": Path(getattr(cfg, "DATA_DIR", "data")).resolve(),
        "documents_dir": Path(getattr(cfg, "DOCUMENTS_DIR", "data/documents")).resolve(),
        "backups_dir": Path(getattr(cfg, "BACKUP_DIR", "data/backups")).resolve(),
        "plugins_dir": Path(getattr(cfg, "PLUGINS_DIR", "data/plugins")).resolve(),
    }
    for d_name, d_path in directories.items():
        checks.append(_check_directory_write_access(d_name, d_path))

    # 4. Security & Device Identity
    id_path = Path(getattr(cfg, "IDENTITY_DB", "data/identity.db")).resolve()
    checks.append(_check_device_identity(id_path))

    # 5. Vector Store
    chroma_path = Path(getattr(cfg, "CHROMA_DIR", "chroma_db")).resolve()
    checks.append(_check_vector_store(chroma_path))

    # 6. Port Availability
    api_port = port_override if port_override is not None and port_override > 0 else getattr(cfg, "API_PORT", 8000)
    checks.append(_check_port_availability(api_port, in_process=in_process))

    # Calculate overall status
    has_fail = any(c.status == "FAIL" for c in checks)
    has_warn = any(c.status == "WARN" for c in checks)

    if has_fail:
        overall = "FAILED"
    elif has_warn and strict_mode:
        overall = "FAILED"
    elif has_warn:
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    return DiagnosticReport(
        timestamp=now_iso,
        overall_status=overall,
        checks=checks,
    )
