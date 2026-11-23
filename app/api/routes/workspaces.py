"""
workspaces.py

FastAPI router for Multi-Profile Workspace Boundaries and Row-Level Authorization
(Milestone 6: /api/workspaces).
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    RequestContext,
    get_request_context,
    require_auth,
)
from app.api.schemas import (
    WorkspaceCreateRequest,
    WorkspaceMemberAddRequest,
    WorkspaceMemberResponse,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from app.context.context_store import get_all_context
from app.knowledge.document_registry import get_all_documents
from app.workspace.models import WorkspaceProfile
from app.workspace.store import (
    add_workspace_member,
    create_workspace,
    delete_workspace,
    get_workspace,
    is_user_authorized_for_workspace,
    list_workspace_members,
    list_workspaces_for_user,
    remove_workspace_member,
    update_workspace,
)

logger = logging.getLogger("lifeos.api.workspaces")
router = APIRouter(prefix="/api/workspaces", tags=["Workspaces"])


def _to_workspace_response(ws: WorkspaceProfile) -> WorkspaceResponse:
    doc_count = len(get_all_documents(workspace_id=ws.workspace_id))
    ctx_count = len(get_all_context(workspace_id=ws.workspace_id))
    return WorkspaceResponse(
        workspace_id=ws.workspace_id,
        name=ws.name,
        profile_type=ws.profile_type,
        description=ws.description,
        created_at=ws.created_at,
        updated_at=ws.updated_at,
        is_active=ws.is_active,
        settings=ws.settings,
        document_count=doc_count,
        context_count=ctx_count,
    )


@router.get(
    "",
    response_model=list[WorkspaceResponse],
    summary="List Accessible Workspaces",
    description="Returns all workspace profiles accessible to the authenticated user.",
)
async def list_workspaces(
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    is_admin = (ctx.user.role == "admin")
    workspaces = list_workspaces_for_user(user_id=ctx.user_id, is_admin=is_admin)
    return [_to_workspace_response(ws) for ws in workspaces]


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Workspace Profile",
    description="Creates a new isolated workspace profile with the caller as owner.",
)
async def create_workspace_profile(
    body: WorkspaceCreateRequest,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    ws = create_workspace(
        name=body.name,
        profile_type=body.profile_type,
        description=body.description,
        owner_id=ctx.user_id,
        settings=body.settings,
    )
    return _to_workspace_response(ws)


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Get Workspace Profile",
    description="Retrieves metadata for a specific workspace profile subject to row-level authorization.",
)
async def get_workspace_profile(
    workspace_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    is_admin = (ctx.user.role == "admin")
    if not is_user_authorized_for_workspace(ctx.user_id, workspace_id, required_role="viewer", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.user_id}' is not authorized to access workspace '{workspace_id}'",
        )

    return _to_workspace_response(ws)


@router.put(
    "/{workspace_id}",
    response_model=WorkspaceResponse,
    summary="Update Workspace Profile",
    description="Updates workspace attributes. Requires workspace admin or owner role.",
)
async def update_workspace_profile(
    workspace_id: str,
    body: WorkspaceUpdateRequest,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    is_admin = (ctx.user.role == "admin")
    if not is_user_authorized_for_workspace(ctx.user_id, workspace_id, required_role="admin", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.user_id}' does not have administrative privileges in workspace '{workspace_id}'",
        )

    updated = update_workspace(
        workspace_id=workspace_id,
        name=body.name,
        profile_type=body.profile_type,
        description=body.description,
        is_active=body.is_active,
        settings=body.settings,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update workspace",
        )
    return _to_workspace_response(updated)


@router.delete(
    "/{workspace_id}",
    summary="Delete Workspace Profile",
    description="Deletes an isolated workspace. The canonical default workspace cannot be deleted.",
)
async def delete_workspace_profile(
    workspace_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    if workspace_id == "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default workspace profile.",
        )

    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    is_admin = (ctx.user.role == "admin")
    if not is_user_authorized_for_workspace(ctx.user_id, workspace_id, required_role="owner", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.user_id}' must be an owner of workspace '{workspace_id}' to delete it",
        )

    deleted = delete_workspace(workspace_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete workspace",
        )
    return {"deleted": True, "workspace_id": workspace_id}


@router.get(
    "/{workspace_id}/members",
    response_model=list[WorkspaceMemberResponse],
    summary="List Workspace Members",
    description="Returns member user IDs and roles for the specified workspace.",
)
async def list_members(
    workspace_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    is_admin = (ctx.user.role == "admin")
    if not is_user_authorized_for_workspace(ctx.user_id, workspace_id, required_role="viewer", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.user_id}' is not authorized to view members of workspace '{workspace_id}'",
        )

    members = list_workspace_members(workspace_id)
    return [
        WorkspaceMemberResponse(
            workspace_id=m.workspace_id,
            user_id=m.user_id,
            role=m.role,
            joined_at=m.joined_at,
        )
        for m in members
    ]


@router.post(
    "/{workspace_id}/members",
    response_model=WorkspaceMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add or Update Workspace Member",
    description="Adds a user to a workspace or updates their role. Requires admin role in the workspace.",
)
async def add_or_update_member(
    workspace_id: str,
    body: WorkspaceMemberAddRequest,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    is_admin = (ctx.user.role == "admin")
    if not is_user_authorized_for_workspace(ctx.user_id, workspace_id, required_role="admin", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.user_id}' does not have administrative privileges in workspace '{workspace_id}'",
        )

    member = add_workspace_member(workspace_id=workspace_id, user_id=body.user_id, role=body.role)
    return WorkspaceMemberResponse(
        workspace_id=member.workspace_id,
        user_id=member.user_id,
        role=member.role,
        joined_at=member.joined_at,
    )


@router.delete(
    "/{workspace_id}/members/{user_id}",
    summary="Remove Workspace Member",
    description="Removes a member from the workspace. Requires workspace admin/owner role.",
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    ws = get_workspace(workspace_id)
    if not ws:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workspace '{workspace_id}' not found",
        )

    is_admin = (ctx.user.role == "admin")
    # User can remove themselves, or an admin/owner can remove others
    if ctx.user_id != user_id and not is_user_authorized_for_workspace(ctx.user_id, workspace_id, required_role="admin", is_admin=is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User '{ctx.user_id}' cannot remove '{user_id}' from workspace '{workspace_id}'",
        )

    removed = remove_workspace_member(workspace_id=workspace_id, user_id=user_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Member '{user_id}' not found in workspace '{workspace_id}'",
        )
    return {"success": True, "removed_user": user_id, "workspace_id": workspace_id}
