"""
LifeOS Phase 11 API Package.
"""

import sys
from pathlib import Path

# Ensure repo root and app directory are on sys.path for standalone uvicorn runs
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_APP_DIR = _BASE_DIR / "app"

for _p in [str(_BASE_DIR), str(_APP_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.api.server import app, create_app

__all__ = ["app", "create_app"]
