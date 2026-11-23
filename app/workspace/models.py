"""
models.py

Domain models and typed schemas for Multi-Profile Workspace Boundaries
and Row-Level Authorization in LifeOS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class WorkspaceProfile:
    """Represents an isolated workspace profile boundary."""
    workspace_id: str                   # Unique identifier e.g. 'default', 'ws_work_123'
    name: str                           # Human-readable name e.g. 'Personal', 'Work Q4'
    profile_type: str = "personal"      # 'personal', 'work', 'health', 'finance', 'confidential'
    description: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_active: bool = True
    settings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_local_only(self) -> bool:
        """Returns True if workspace settings or profile type mandates strict local processing."""
        return bool(
            self.settings.get("local_only", False)
            or self.settings.get("is_local_only", False)
            or self.profile_type in ("confidential", "local_only")
        )



@dataclass
class WorkspaceMembership:
    """Tracks a user's role and authorization within a workspace boundary."""
    user_id: str
    workspace_id: str
    role: str = "member"                # 'owner', 'admin', 'member', 'viewer'
    joined_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

