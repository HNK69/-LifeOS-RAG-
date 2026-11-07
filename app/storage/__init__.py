"""
Storage package for LifeOS.
Provides centralized database connection management and persistent store interfaces.
"""
from storage.sqlite import ManagedConnection, connect_sqlite

__all__ = ["ManagedConnection", "connect_sqlite"]
