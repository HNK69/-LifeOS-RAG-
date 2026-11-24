"""
cli.py

Unified production command-line interface for LifeOS Milestone 10.
Commands:
  lifeos serve          Start the production FastAPI application server
  lifeos doctor         Execute pre-flight system diagnostics across all subsystems
  lifeos backup create  Create an atomic point-in-time backup archive of all data
  lifeos backup restore Restore LifeOS from a backup archive with SHA-256 integrity checks
  lifeos backup list    List available backup archives
  lifeos backup prune   Prune expired backup archives based on retention policy
  lifeos status         Show high-level system, storage, and workspace status
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.ops.doctor import run_diagnostics
from app.storage.backup import create_backup, list_backups, prune_backups, restore_backup

logger = logging.getLogger("lifeos.cli")


def _find_free_port() -> int:
    """Find an available ephemeral port on 127.0.0.1."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start the production LifeOS server."""
    import os
    import secrets
    import uvicorn
    try:
        import config as cfg
    except ImportError:
        import app.config as cfg

    sidecar_mode = getattr(args, "sidecar_mode", False)

    if sidecar_mode:
        # Enforce security boundary: sidecar strictly binds to 127.0.0.1
        if args.host and args.host not in ("127.0.0.1", "localhost"):
            sys.stderr.write(f"Error: Sidecar mode strictly requires 127.0.0.1 (received '{args.host}').\n")
            return 1
        host = "127.0.0.1"
        port = args.port
        if not port or port == 0:
            port = _find_free_port()

        workers = 1
        log_fmt = args.log_format or getattr(cfg, "LOG_FORMAT", "text")

        # Ephemeral session token for IPC authentication
        session_token = getattr(args, "token", None) or secrets.token_urlsafe(32)
        cfg.API_KEY = session_token
        cfg.API_AUTH_ENABLED = True
        os.environ["LIFEOS_API_KEY"] = session_token
        os.environ["LIFEOS_API_AUTH_ENABLED"] = "true"
        os.environ["LIFEOS_LOCAL_ONLY"] = "true"

        # Emit structured JSON handshake to stdout for Tauri/parent process
        handshake = {
            "event": "sidecar_ready",
            "status": "ready",
            "host": host,
            "port": port,
            "token": session_token,
            "pid": os.getpid(),
        }
        sys.stdout.write(json.dumps(handshake) + "\n")
        sys.stdout.flush()

        uvicorn.run(
            "app.api.server:app",
            host=host,
            port=port,
            workers=workers,
            log_level="info",
            reload=False,
        )
        return 0

    host = args.host or getattr(cfg, "API_HOST", "0.0.0.0")
    port = args.port or getattr(cfg, "API_PORT", 8000)
    workers = args.workers or getattr(cfg, "API_WORKERS", 1)
    log_fmt = args.log_format or getattr(cfg, "LOG_FORMAT", "text")

    print(f"Starting LifeOS Server on {host}:{port} (workers={workers}, log_format={log_fmt})...")
    uvicorn.run(
        "app.api.server:app",
        host=host,
        port=port,
        workers=workers,
        log_level="info",
        reload=args.reload,
    )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run pre-flight diagnostics."""
    report = run_diagnostics(strict_mode=args.strict)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary_text())

    return 0 if report.overall_status in ("HEALTHY", "DEGRADED") else 1


def _cmd_backup(args: argparse.Namespace) -> int:
    """Handle backup subcommands."""
    subaction = args.backup_action

    if subaction == "create":
        dest = args.dest
        label = args.label
        path = create_backup(destination_dir=dest, label=label)
        print(f"Successfully created backup archive: {path}")
        return 0

    elif subaction == "restore":
        archive = args.archive
        safety = not args.no_safety_backup
        print(f"Restoring from archive: {archive} (safety_backup={safety})...")
        res = restore_backup(archive_path=archive, create_safety_backup=safety)
        print("Restore completed successfully:")
        print(json.dumps(res, indent=2))
        return 0

    elif subaction == "list":
        dest = args.dest
        backups = list_backups(destination_dir=dest)
        if not backups:
            print("No backups found.")
            return 0

        print(f"Available Backups ({len(backups)}):")
        print(f"{'Filename':40} | {'Size':10} | {'Created At'}")
        print("-" * 75)
        for b in backups:
            size_mb = b['size_bytes'] / (1024 * 1024)
            print(f"{b['filename']:40} | {size_mb:8.2f}MB | {b['created_at']}")
        return 0

    elif subaction == "prune":
        dest = args.dest
        days = args.days
        pruned = prune_backups(destination_dir=dest, retention_days=days)
        print(f"Pruned {pruned} expired backup archive(s).")
        return 0

    else:
        print(f"Unknown backup action '{subaction}'. Use --help for usage.")
        return 1


def _cmd_status(args: argparse.Namespace) -> int:
    """Output high-level operational status and database metrics."""
    try:
        import config as cfg
    except ImportError:
        import app.config as cfg

    data_dir = Path(getattr(cfg, "DATA_DIR", "data"))
    db_files = list(data_dir.glob("*.db")) if data_dir.exists() else []

    status_data = {
        "data_dir": str(data_dir.resolve()),
        "sqlite_databases_count": len(db_files),
        "databases": [f.name for f in db_files],
        "api_environment": getattr(cfg, "API_ENVIRONMENT", "production"),
        "routing_mode": getattr(cfg, "ROUTING_MODE", "hybrid_cascade"),
        "pii_redaction": getattr(cfg, "PII_REDACTION_ENABLED", True),
    }

    if args.json:
        print(json.dumps(status_data, indent=2))
    else:
        print("LifeOS Operational Status:")
        print(f"  Environment:  {status_data['api_environment']}")
        print(f"  Routing Mode: {status_data['routing_mode']}")
        print(f"  PII Redact:   {status_data['pii_redaction']}")
        print(f"  Data Dir:     {status_data['data_dir']}")
        print(f"  Active DBs:   {', '.join(status_data['databases']) if status_data['databases'] else 'None'}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="lifeos",
        description="LifeOS: Personal Intelligence Platform CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # 1. lifeos serve
    serve_parser = subparsers.add_parser("serve", help="Start the production API server")
    serve_parser.add_argument("--host", default=None, help="Server bind host")
    serve_parser.add_argument("--port", type=int, default=None, help="Server bind port")
    serve_parser.add_argument("--workers", type=int, default=None, help="Number of worker processes")
    serve_parser.add_argument("--log-format", choices=["text", "json"], default=None, help="Log format")
    serve_parser.add_argument("--reload", action="store_true", help="Enable dev auto-reloading")
    serve_parser.add_argument("--sidecar-mode", action="store_true", help="Run as managed desktop sidecar (binds 127.0.0.1, emits handshake JSON)")
    serve_parser.add_argument("--token", default=None, help="Session token for sidecar mode authentication")

    # 2. lifeos doctor
    doc_parser = subparsers.add_parser("doctor", help="Run pre-flight system diagnostics")
    doc_parser.add_argument("--strict", action="store_true", help="Fail on warnings as well as errors")
    doc_parser.add_argument("--json", action="store_true", help="Output diagnostic report in JSON format")

    # 3. lifeos backup
    backup_parser = subparsers.add_parser("backup", help="Manage disaster recovery backups")
    backup_sub = backup_parser.add_subparsers(dest="backup_action", help="Backup actions")

    b_create = backup_sub.add_parser("create", help="Create a complete point-in-time backup")
    b_create.add_argument("--label", default=None, help="Optional tag label in archive name")
    b_create.add_argument("--dest", default=None, help="Destination directory for archive")

    b_restore = backup_sub.add_parser("restore", help="Restore LifeOS from a backup archive")
    b_restore.add_argument("archive", help="Path to .tar.gz backup archive")
    b_restore.add_argument("--no-safety-backup", action="store_true", help="Skip creating a pre-restore safety snapshot")

    b_list = backup_sub.add_parser("list", help="List existing backups")
    b_list.add_argument("--dest", default=None, help="Backups directory")

    b_prune = backup_sub.add_parser("prune", help="Prune expired backups")
    b_prune.add_argument("--dest", default=None, help="Backups directory")
    b_prune.add_argument("--days", type=int, default=None, help="Retention window in days")

    # 4. lifeos status
    status_parser = subparsers.add_parser("status", help="Show system status and database metrics")
    status_parser.add_argument("--json", action="store_true", help="Output status in JSON format")

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point function."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "serve":
        return _cmd_serve(args)
    elif args.subcommand == "doctor":
        return _cmd_doctor(args)
    elif args.subcommand == "backup":
        return _cmd_backup(args)
    elif args.subcommand == "status":
        return _cmd_status(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
