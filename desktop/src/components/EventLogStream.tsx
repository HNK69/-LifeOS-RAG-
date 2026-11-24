import React from "react";
import { LifeOSEventPayload } from "../api/types";

interface EventLogStreamProps {
  events: LifeOSEventPayload[];
  onClear: () => void;
}

export const EventLogStream: React.FC<EventLogStreamProps> = ({ events, onClear }) => {
  return (
    <div
      style={{
        backgroundColor: "#1e293b",
        color: "#f8fafc",
        borderRadius: "8px",
        padding: "16px",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
        fontSize: "12px",
        display: "flex",
        flexDirection: "column",
        height: "100%",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "12px",
          borderBottom: "1px solid #334155",
          paddingBottom: "8px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span style={{ fontWeight: 600, color: "#94a3b8" }}>Event Bus Stream</span>
          <span
            style={{
              backgroundColor: "#334155",
              color: "#cbd5e1",
              padding: "1px 6px",
              borderRadius: "4px",
              fontSize: "11px",
            }}
          >
            {events.length} captured
          </span>
        </div>
        <button
          onClick={onClear}
          style={{
            backgroundColor: "transparent",
            border: "1px solid #475569",
            color: "#94a3b8",
            borderRadius: "4px",
            padding: "2px 8px",
            cursor: "pointer",
            fontSize: "11px",
          }}
        >
          Clear
        </button>
      </div>

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
        }}
      >
        {events.length === 0 ? (
          <div style={{ color: "#64748b", fontStyle: "italic", textAlign: "center", marginTop: "24px" }}>
            Waiting for live events (ingestion, pairing, consent)...
          </div>
        ) : (
          events.map((evt) => (
            <div
              key={evt.event_id}
              style={{
                backgroundColor: "#0f172a",
                padding: "8px 12px",
                borderRadius: "6px",
                borderLeft: "3px solid #38bdf8",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "4px" }}>
                <span style={{ fontWeight: 600, color: "#38bdf8" }}>{evt.event_type}</span>
                <span style={{ color: "#64748b", fontSize: "10px" }}>{evt.timestamp}</span>
              </div>
              <div style={{ color: "#cbd5e1", wordBreak: "break-all" }}>
                {JSON.stringify(evt.payload)}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
