import React from "react";
import { SidecarState } from "../api/types";

export const SidecarStatusBadge: React.FC<{ state: SidecarState }> = ({ state }) => {
  const getBadgeStyle = () => {
    switch (state.status) {
      case "ready":
        return { bg: "#dcfce7", text: "#15803d", border: "#86efac", label: "Sidecar Ready" };
      case "starting":
        return { bg: "#fef9c3", text: "#854d0e", border: "#fde047", label: "Starting..." };
      case "error":
        return { bg: "#fee2e2", text: "#b91c1c", border: "#fca5a5", label: "Connection Error" };
      default:
        return { bg: "#f3f4f6", text: "#4b5563", border: "#d1d5db", label: "Disconnected" };
    }
  };

  const style = getBadgeStyle();

  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 10px",
        borderRadius: "9999px",
        fontSize: "12px",
        fontWeight: 500,
        backgroundColor: style.bg,
        color: style.text,
        border: `1px solid ${style.border}`,
      }}
    >
      <span
        style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          backgroundColor: style.text,
        }}
      />
      <span>{style.label}</span>
      {state.status === "ready" && (
        <span style={{ opacity: 0.75, fontSize: "11px" }}>
          (127.0.0.1:{state.port})
        </span>
      )}
    </div>
  );
};
