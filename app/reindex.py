"""
reindex.py

LifeOS manual reindex entry point.
"""
import sys
from pathlib import Path

# Ensure app directory is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from knowledge.reindex import reindex

if __name__ == "__main__":
    result = reindex(raise_on_error=False)
    if not result.get("success"):
        sys.exit(1)
