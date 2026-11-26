import React from "react";
import { SidecarState, WorkspaceProfile } from "../api/types";
import { ConnectionState } from "../api/events";
import { SidecarStatusBadge } from "./SidecarStatusBadge";
import { WorkspaceSwitcher } from "../views/WorkspacesView";

interface HeaderProps {
  sidecarState: SidecarState;
  sseState: ConnectionState;
  workspaces?: WorkspaceProfile[];
  activeWorkspaceId?: string;
  onWorkspaceChange?: (workspaceId: string) => void;
}

export const Header: React.FC<HeaderProps> = ({
  sidecarState,
  sseState,
  workspaces = [],
  activeWorkspaceId = "default",
  onWorkspaceChange,
}) => {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 20px",
        backgroundColor: "#ffffff",
        borderBottom: "1px solid #e5e7eb",
        boxShadow: "0 1px 2px 0 rgba(0, 0, 0, 0.05)",
        flexWrap: "wrap",
        gap: "12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
        <h1 style={{ margin: 0, fontSize: "18px", fontWeight: 700, color: "#111827" }}>
          LifeOS Desktop
        </h1>
        <span
          style={{
            fontSize: "11px",
            backgroundColor: "#e0f2fe",
            color: "#0369a1",
            padding: "2px 8px",
            borderRadius: "4px",
            fontWeight: 600,
          }}
        >
          v1.0.0 (Local-First)
        </span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
        {onWorkspaceChange && (
          <WorkspaceSwitcher
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            onSelectWorkspace={onWorkspaceChange}
          />
        )}
        <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#6b7280" }}>
          <span>SSE:</span>
          <span
            style={{
              fontWeight: 600,
              color:
                sseState === "connected"
                  ? "#16a34a"
                  : sseState === "reconnecting"
                  ? "#d97706"
                  : "#dc2626",
            }}
          >
            {sseState}
          </span>
        </div>
        <SidecarStatusBadge state={sidecarState} />
      </div>
    </header>
  );
};
