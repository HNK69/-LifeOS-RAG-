"""
__init__.py

LifeOS Milestone 9: Plugin & Sidecar Extensibility Sandbox.
"""

from app.plugins.ipc import HostBroker, parse_message, serialize_message
from app.plugins.manager import (
    PluginManager,
    SidecarProcess,
    get_plugin_manager,
    reset_plugin_manager,
    sanitize_child_environment,
)
from app.plugins.models import (
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    PluginCapability,
    PluginManifest,
    PluginState,
    PluginStatus,
    ToolDeclaration,
)
from app.plugins.tool_adapter import SidecarTool

__all__ = [
    "PluginCapability",
    "PluginState",
    "ToolDeclaration",
    "PluginManifest",
    "PluginStatus",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcNotification",
    "JsonRpcError",
    "HostBroker",
    "parse_message",
    "serialize_message",
    "SidecarTool",
    "SidecarProcess",
    "PluginManager",
    "get_plugin_manager",
    "reset_plugin_manager",
    "sanitize_child_environment",
]
