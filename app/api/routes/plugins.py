"""
plugins.py

REST API endpoints for LifeOS Milestone 9: Plugin & Sidecar Extensibility Sandbox.
Provides lifecycle controls, registration, status monitoring, and tool execution.
"""

from __future__ import annotations

import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import AuthenticatedUser, authenticate_request
from app.api.schemas import (
    PluginActionResponse,
    PluginInvokeRequest,
    PluginInvokeResponse,
    PluginRegisterRequest,
    PluginStatusResponse,
)
from app.plugins.manager import get_plugin_manager
from app.plugins.models import PluginManifest, ToolDeclaration

logger = logging.getLogger("lifeos.api.routes.plugins")

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("", response_model=list[PluginStatusResponse])
def list_plugins(
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> list[PluginStatusResponse]:
    """List all registered plugins, their running state, and active capabilities."""
    manager = get_plugin_manager()
    statuses = manager.list_plugins()
    return [PluginStatusResponse(**s.to_dict()) for s in statuses]


@router.post("/register", response_model=PluginStatusResponse, status_code=status.HTTP_201_CREATED)
def register_plugin(
    req: PluginRegisterRequest,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginStatusResponse:
    """Register a new plugin manifest into the LifeOS ecosystem."""
    manager = get_plugin_manager()
    tools = [
        ToolDeclaration(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in req.tools
    ]
    manifest = PluginManifest(
        plugin_id=req.plugin_id,
        name=req.name,
        version=req.version,
        description=req.description,
        author=req.author,
        entrypoint=req.entrypoint,
        workspace_scope=req.workspace_scope,
        requested_capabilities=req.requested_capabilities,
        tools=tools,
    )
    status_obj = manager.register_plugin(manifest)
    return PluginStatusResponse(**status_obj.to_dict())


@router.get("/{plugin_id}/status", response_model=PluginStatusResponse)
def get_plugin_status(
    plugin_id: str,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginStatusResponse:
    """Retrieve detailed status, metrics, and health of a specific plugin."""
    manager = get_plugin_manager()
    try:
        status_obj = manager.get_plugin_status(plugin_id)
        return PluginStatusResponse(**status_obj.to_dict())
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{plugin_id}/start", response_model=PluginActionResponse)
def start_plugin(
    plugin_id: str,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginActionResponse:
    """Start a registered plugin sidecar process."""
    manager = get_plugin_manager()
    try:
        success = manager.start_plugin(plugin_id)
        if not success:
            status_obj = manager.get_plugin_status(plugin_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to start plugin '{plugin_id}': {status_obj.last_error or 'Unknown error'}",
            )
        return PluginActionResponse(plugin_id=plugin_id, status="running", message="Plugin started successfully.")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{plugin_id}/stop", response_model=PluginActionResponse)
def stop_plugin(
    plugin_id: str,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginActionResponse:
    """Gracefully terminate a running plugin sidecar."""
    manager = get_plugin_manager()
    try:
        manager.stop_plugin(plugin_id)
        return PluginActionResponse(plugin_id=plugin_id, status="stopped", message="Plugin stopped successfully.")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{plugin_id}/restart", response_model=PluginActionResponse)
def restart_plugin(
    plugin_id: str,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginActionResponse:
    """Restart a plugin sidecar with exponential backoff on repeated restarts."""
    manager = get_plugin_manager()
    try:
        success = manager.restart_plugin(plugin_id)
        if not success:
            status_obj = manager.get_plugin_status(plugin_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to restart plugin '{plugin_id}': {status_obj.last_error or 'Unknown error'}",
            )
        return PluginActionResponse(plugin_id=plugin_id, status="running", message="Plugin restarted successfully.")
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{plugin_id}/invoke", response_model=PluginInvokeResponse)
def invoke_plugin_tool(
    plugin_id: str,
    req: PluginInvokeRequest,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginInvokeResponse:
    """Invoke an exported tool on an active plugin sidecar."""
    manager = get_plugin_manager()
    try:
        result = manager.invoke_tool(
            plugin_id=plugin_id,
            tool_name=req.tool_name,
            arguments=req.arguments,
        )
        return PluginInvokeResponse(
            plugin_id=plugin_id,
            tool_name=req.tool_name,
            success=True,
            result=result,
            error=None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        return PluginInvokeResponse(
            plugin_id=plugin_id,
            tool_name=req.tool_name,
            success=False,
            result=None,
            error=str(exc),
        )


@router.delete("/{plugin_id}", response_model=PluginActionResponse)
def unregister_plugin(
    plugin_id: str,
    current_user: AuthenticatedUser = Depends(authenticate_request),
) -> PluginActionResponse:
    """Unregister a plugin, terminate its sidecar, and clean up registrations."""
    manager = get_plugin_manager()
    success = manager.unregister_plugin(plugin_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Plugin '{plugin_id}' not found.")
    return PluginActionResponse(plugin_id=plugin_id, status="unregistered", message="Plugin unregistered successfully.")
