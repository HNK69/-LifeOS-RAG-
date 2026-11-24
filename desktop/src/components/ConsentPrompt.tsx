import React, { useState } from "react";
import { LifeOSWebClient } from "../api/client";

export interface DiscoveredSource {
  resource_path: string;
  scope_type: "folder" | "file";
  suggested_name: string;
  file_count: number;
  discovered_at: string;
}

interface ConsentPromptProps {
  client: LifeOSWebClient;
  discovery: DiscoveredSource | null;
  onDismiss: () => void;
  onAuthorized: (path: string) => void;
}

export const ConsentPrompt: React.FC<ConsentPromptProps> = ({
  client,
  discovery,
  onDismiss,
  onAuthorized,
}) => {
  const [isAuthorizing, setIsAuthorizing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!discovery) {
    return null;
  }

  const handleAuthorize = async () => {
    setIsAuthorizing(true);
    setError(null);
    try {
      await client.grantConsent(
        discovery.resource_path,
        discovery.scope_type,
        ["general"],
        ["indexing", "query"]
      );
      onAuthorized(discovery.resource_path);
    } catch (err: any) {
      setError(err?.message || "Failed to grant consent");
    } finally {
      setIsAuthorizing(false);
    }
  };

  return (
    <div
      role="alert"
      style={{
        backgroundColor: "#eff6ff",
        border: "1px solid #bfdbfe",
        borderRadius: "8px",
        padding: "14px 18px",
        marginBottom: "16px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        boxShadow: "0 1px 3px 0 rgba(0, 0, 0, 0.05)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", flex: 1 }}>
        <div
          style={{
            backgroundColor: "#3b82f6",
            color: "#ffffff",
            width: "32px",
            height: "32px",
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: "14px",
            flexShrink: 0,
          }}
        >
          📂
        </div>
        <div>
          <div style={{ fontWeight: 600, color: "#1e3a8a", fontSize: "14px" }}>
            New Source Folder Detected
          </div>
          <div style={{ color: "#3b82f6", fontSize: "12px", marginTop: "2px", wordBreak: "break-all" }}>
            <span style={{ fontWeight: 500, color: "#1e293b" }}>{discovery.resource_path}</span>
            <span style={{ color: "#64748b", marginLeft: "6px" }}>
              ({discovery.file_count} {discovery.file_count === 1 ? "file" : "files"} detected)
            </span>
          </div>
          {error && (
            <div style={{ color: "#b91c1c", fontSize: "12px", marginTop: "4px" }}>
              {error}
            </div>
          )}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: 0, marginLeft: "16px" }}>
        <button
          onClick={handleAuthorize}
          disabled={isAuthorizing}
          style={{
            backgroundColor: "#2563eb",
            color: "#ffffff",
            border: "none",
            borderRadius: "6px",
            padding: "6px 14px",
            fontSize: "12px",
            fontWeight: 600,
            cursor: isAuthorizing ? "not-allowed" : "pointer",
            opacity: isAuthorizing ? 0.7 : 1,
          }}
        >
          {isAuthorizing ? "Authorizing..." : "Authorize & Index"}
        </button>
        <button
          onClick={onDismiss}
          disabled={isAuthorizing}
          style={{
            backgroundColor: "#ffffff",
            color: "#475569",
            border: "1px solid #cbd5e1",
            borderRadius: "6px",
            padding: "6px 12px",
            fontSize: "12px",
            fontWeight: 500,
            cursor: "pointer",
          }}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
};
