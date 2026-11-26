import React, { useCallback, useEffect, useState } from "react";
import { LifeOSWebClient } from "../api/client";
import { WorkspaceProfile } from "../api/types";

export function isWorkspaceLocalOnly(ws: WorkspaceProfile | undefined | null): boolean {
  if (!ws) {
    return false;
  }
  return Boolean(
    ws.settings?.local_only === true ||
      ws.profile_type === "restricted" ||
      ws.profile_type === "confidential"
  );
}

export interface WorkspaceSwitcherProps {
  workspaces: WorkspaceProfile[];
  activeWorkspaceId: string;
  onSelectWorkspace: (workspaceId: string) => void;
}

export const WorkspaceSwitcher: React.FC<WorkspaceSwitcherProps> = ({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
}) => {
  const activeWs = workspaces.find((w) => w.workspace_id === activeWorkspaceId);
  const localOnly = isWorkspaceLocalOnly(activeWs);

  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
      <label htmlFor="header-workspace-switcher" style={{ fontSize: "12px", color: "#64748b", fontWeight: 500 }}>
        Workspace:
      </label>
      <select
        id="header-workspace-switcher"
        aria-label="Active Workspace Switcher"
        value={activeWorkspaceId}
        onChange={(e) => onSelectWorkspace(e.target.value)}
        style={{
          padding: "4px 10px",
          fontSize: "12px",
          fontWeight: 600,
          borderRadius: "6px",
          border: "1px solid #cbd5e1",
          backgroundColor: "#f8fafc",
          color: "#1e293b",
          cursor: "pointer",
        }}
      >
        {workspaces.length === 0 ? (
          <option value={activeWorkspaceId || "default"}>
            {activeWorkspaceId && activeWorkspaceId !== "default" ? activeWorkspaceId : "Default Workspace"}
          </option>
        ) : (
          <>
            {!activeWs && activeWorkspaceId && (
              <option value={activeWorkspaceId}>{activeWorkspaceId}</option>
            )}
            {workspaces.map((ws) => (
              <option key={ws.workspace_id} value={ws.workspace_id}>
                {ws.name} ({ws.profile_type})
              </option>
            ))}
          </>
        )}
      </select>

      {localOnly && (
        <span
          data-testid="local-only-badge"
          style={{
            fontSize: "11px",
            fontWeight: 600,
            padding: "2px 8px",
            borderRadius: "9999px",
            backgroundColor: "#fef3c7",
            color: "#92400e",
            border: "1px solid #fcd34d",
          }}
        >
          Local-Only
        </span>
      )}
    </div>
  );
};

export interface WorkspacesViewProps {
  client: LifeOSWebClient;
  workspaces?: WorkspaceProfile[];
  activeWorkspaceId: string;
  onSelectWorkspace: (workspaceId: string) => void;
  onWorkspacesUpdated?: (workspaces: WorkspaceProfile[]) => void;
  initialCreateDialogOpen?: boolean;
}

export const WorkspacesView: React.FC<WorkspacesViewProps> = ({
  client,
  workspaces: externalWorkspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onWorkspacesUpdated,
  initialCreateDialogOpen = false,
}) => {
  const [workspaces, setWorkspaces] = useState<WorkspaceProfile[]>(externalWorkspaces || []);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  // Create Dialog State
  const [createOpen, setCreateOpen] = useState(initialCreateDialogOpen);
  const [name, setName] = useState("");
  const [profileType, setProfileType] = useState<"personal" | "work" | "restricted">("personal");
  const [description, setDescription] = useState("");
  const [localOnly, setLocalOnly] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    if (externalWorkspaces) {
      setWorkspaces(externalWorkspaces);
    }
  }, [externalWorkspaces]);

  const loadWorkspaces = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const list = await client.listWorkspaces();
      setWorkspaces(list);
      if (onWorkspacesUpdated) {
        onWorkspacesUpdated(list);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load workspaces");
    } finally {
      setIsLoading(false);
    }
  }, [client, onWorkspacesUpdated]);

  useEffect(() => {
    loadWorkspaces();
  }, [loadWorkspaces]);

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      setModalError("Workspace name is required.");
      return;
    }

    setIsCreating(true);
    setModalError(null);
    try {
      const created = await client.createWorkspace({
        name: trimmedName,
        profile_type: profileType,
        description: description.trim() || undefined,
        settings: {
          local_only: localOnly || profileType === "restricted",
        },
      });
      const updatedList = [...workspaces, created];
      setWorkspaces(updatedList);
      if (onWorkspacesUpdated) {
        onWorkspacesUpdated(updatedList);
      }
      onSelectWorkspace(created.workspace_id);
      setCreateOpen(false);
      setModalError(null);
      setName("");
      setDescription("");
      setProfileType("personal");
      setLocalOnly(false);
    } catch (err: any) {
      setModalError(err?.message || "Failed to create workspace");
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteWorkspace = async (workspaceId: string) => {
    if (workspaceId === "default") {
      return;
    }
    setDeletingId(workspaceId);
    setError(null);
    try {
      await client.deleteWorkspace(workspaceId);
      const remaining = workspaces.filter((w) => w.workspace_id !== workspaceId);
      setWorkspaces(remaining);
      if (onWorkspacesUpdated) {
        onWorkspacesUpdated(remaining);
      }
      if (activeWorkspaceId === workspaceId && remaining.length > 0) {
        onSelectWorkspace(remaining[0].workspace_id);
      }
    } catch (err: any) {
      setError(err?.message || `Failed to delete workspace ${workspaceId}`);
    } finally {
      setDeletingId(null);
    }
  };

  const getProfileBadgeStyle = (type: string) => {
    switch (type) {
      case "work":
        return { bg: "#dbeafe", color: "#1e40af" };
      case "restricted":
      case "confidential":
        return { bg: "#fee2e2", color: "#991b1b" };
      default:
        return { bg: "#dcfce7", color: "#166534" };
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px", overflowY: "auto" }}>
      {/* Header Toolbar */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          backgroundColor: "#ffffff",
          padding: "14px 18px",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div>
          <h2 style={{ margin: 0, fontSize: "16px", fontWeight: 600, color: "#1e293b" }}>
            Workspaces &amp; Profile Isolation
          </h2>
          <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>
            Switch isolated knowledge contexts or provision restricted local-only workspaces.
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <WorkspaceSwitcher
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            onSelectWorkspace={onSelectWorkspace}
          />
          <button
            type="button"
            onClick={loadWorkspaces}
            disabled={isLoading}
            style={{
              backgroundColor: "#f1f5f9",
              color: "#334155",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              padding: "7px 12px",
              fontSize: "12px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            {isLoading ? "Loading..." : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => {
              setError(null);
              setModalError(null);
              setCreateOpen(true);
            }}
            style={{
              backgroundColor: "#2563eb",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              padding: "7px 14px",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            + Create Workspace
          </button>
        </div>
      </div>

      {error && (
        <div
          role="alert"
          style={{
            backgroundColor: "#fef2f2",
            color: "#991b1b",
            border: "1px solid #fecaca",
            padding: "10px 14px",
            borderRadius: "6px",
            fontSize: "13px",
          }}
        >
          {error}
        </div>
      )}

      {/* Workspaces Grid */}
      {workspaces.length === 0 ? (
        <div
          style={{
            backgroundColor: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "32px",
            textAlign: "center",
            color: "#64748b",
            fontSize: "13px",
          }}
        >
          No workspaces loaded yet. Click Refresh or create a new workspace profile.
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
            gap: "16px",
          }}
        >
          {workspaces.map((ws) => {
            const isActive = ws.workspace_id === activeWorkspaceId;
            const wsLocalOnly = isWorkspaceLocalOnly(ws);
            const pBadge = getProfileBadgeStyle(ws.profile_type);

            return (
              <div
                key={ws.workspace_id}
                data-testid={`workspace-card-${ws.workspace_id}`}
                style={{
                  backgroundColor: "#ffffff",
                  border: `2px solid ${isActive ? "#2563eb" : "#e2e8f0"}`,
                  borderRadius: "8px",
                  padding: "16px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  gap: "12px",
                }}
              >
                <div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "8px" }}>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: "15px", color: "#0f172a" }}>{ws.name}</div>
                      <div style={{ fontSize: "11px", color: "#64748b", fontFamily: "ui-monospace, monospace" }}>
                        {ws.workspace_id}
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", justifyContent: "flex-end" }}>
                      <span
                        style={{
                          fontSize: "11px",
                          fontWeight: 600,
                          padding: "2px 8px",
                          borderRadius: "9999px",
                          backgroundColor: pBadge.bg,
                          color: pBadge.color,
                        }}
                      >
                        {ws.profile_type}
                      </span>
                      {wsLocalOnly && (
                        <span
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "2px 8px",
                            borderRadius: "9999px",
                            backgroundColor: "#fef3c7",
                            color: "#92400e",
                            border: "1px solid #fcd34d",
                          }}
                        >
                          Local-Only
                        </span>
                      )}
                    </div>
                  </div>

                  {ws.description && (
                    <div style={{ fontSize: "13px", color: "#475569", marginTop: "8px" }}>
                      {ws.description}
                    </div>
                  )}

                  <div
                    style={{
                      display: "flex",
                      gap: "16px",
                      marginTop: "12px",
                      fontSize: "12px",
                      color: "#334155",
                      backgroundColor: "#f8fafc",
                      padding: "8px 10px",
                      borderRadius: "6px",
                    }}
                  >
                    <span>Documents: <strong>{ws.document_count}</strong></span>
                    <span>Context Memories: <strong>{ws.context_count}</strong></span>
                  </div>
                </div>

                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px" }}>
                  <button
                    type="button"
                    onClick={() => onSelectWorkspace(ws.workspace_id)}
                    disabled={isActive}
                    style={{
                      flex: 1,
                      backgroundColor: isActive ? "#dbeafe" : "#2563eb",
                      color: isActive ? "#1e40af" : "#ffffff",
                      border: "none",
                      borderRadius: "6px",
                      padding: "7px 12px",
                      fontSize: "12px",
                      fontWeight: 600,
                      cursor: isActive ? "default" : "pointer",
                    }}
                  >
                    {isActive ? "Active Workspace" : "Switch to Workspace"}
                  </button>

                  {ws.workspace_id !== "default" && (
                    <button
                      type="button"
                      onClick={() => handleDeleteWorkspace(ws.workspace_id)}
                      disabled={deletingId === ws.workspace_id}
                      style={{
                        backgroundColor: "#fee2e2",
                        color: "#b91c1c",
                        border: "1px solid #fca5a5",
                        borderRadius: "6px",
                        padding: "7px 10px",
                        fontSize: "12px",
                        fontWeight: 600,
                        cursor: deletingId === ws.workspace_id ? "not-allowed" : "pointer",
                      }}
                    >
                      {deletingId === ws.workspace_id ? "..." : "Delete"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create Workspace Dialog */}
      {createOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Create Workspace Profile"
          style={{
            position: "fixed",
            inset: 0,
            backgroundColor: "rgba(15, 23, 42, 0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
            padding: "16px",
          }}
        >
          <form
            onSubmit={handleCreateWorkspace}
            style={{
              backgroundColor: "#ffffff",
              borderRadius: "10px",
              border: "1px solid #e2e8f0",
              maxWidth: "440px",
              width: "100%",
              padding: "20px",
              boxShadow: "0 10px 25px rgba(0, 0, 0, 0.15)",
              display: "flex",
              flexDirection: "column",
              gap: "14px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#1e293b" }}>
                Create Workspace Profile
              </h3>
              <button
                type="button"
                onClick={() => {
                  setModalError(null);
                  setCreateOpen(false);
                }}
                style={{
                  backgroundColor: "transparent",
                  border: "none",
                  fontSize: "16px",
                  cursor: "pointer",
                  color: "#64748b",
                }}
              >
                ×
              </button>
            </div>

            {modalError && (
              <div
                role="alert"
                style={{
                  backgroundColor: "#fef2f2",
                  color: "#991b1b",
                  border: "1px solid #fecaca",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                }}
              >
                {modalError}
              </div>
            )}

            <div>
              <label htmlFor="ws-create-name" style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                Workspace Name
              </label>
              <input
                id="ws-create-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Legal Research or Personal Vault"
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  padding: "8px 10px",
                  borderRadius: "6px",
                  border: "1px solid #cbd5e1",
                  fontSize: "13px",
                }}
              />
            </div>

            <div>
              <label htmlFor="ws-create-type" style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                Profile Type
              </label>
              <select
                id="ws-create-type"
                value={profileType}
                onChange={(e) => {
                  const val = e.target.value as "personal" | "work" | "restricted";
                  setProfileType(val);
                  if (val === "restricted") {
                    setLocalOnly(true);
                  }
                }}
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  borderRadius: "6px",
                  border: "1px solid #cbd5e1",
                  fontSize: "13px",
                }}
              >
                <option value="personal">Personal</option>
                <option value="work">Work</option>
                <option value="restricted">Restricted (Local-Only)</option>
              </select>
            </div>

            <div>
              <label htmlFor="ws-create-desc" style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                Description (Optional)
              </label>
              <textarea
                id="ws-create-desc"
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Isolated workspace for sensitive documents..."
                style={{
                  width: "100%",
                  boxSizing: "border-box",
                  padding: "8px 10px",
                  borderRadius: "6px",
                  border: "1px solid #cbd5e1",
                  fontSize: "13px",
                  fontFamily: "inherit",
                }}
              />
            </div>

            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", color: "#1e293b", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={localOnly || profileType === "restricted"}
                onChange={(e) => setLocalOnly(e.target.checked)}
              />
              <span>Enforce strict Local-Only mode (disable cross-device sync &amp; cloud egress)</span>
            </label>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "4px" }}>
              <button
                type="button"
                onClick={() => {
                  setModalError(null);
                  setCreateOpen(false);
                }}
                style={{
                  backgroundColor: "#ffffff",
                  color: "#475569",
                  border: "1px solid #cbd5e1",
                  borderRadius: "6px",
                  padding: "7px 14px",
                  fontSize: "12px",
                  cursor: "pointer",
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={isCreating || !name.trim()}
                style={{
                  backgroundColor: isCreating || !name.trim() ? "#93c5fd" : "#2563eb",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  padding: "7px 14px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: isCreating || !name.trim() ? "not-allowed" : "pointer",
                }}
              >
                {isCreating ? "Creating..." : "Create Workspace"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
