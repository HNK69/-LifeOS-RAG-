import React, { useState } from "react";
import { Header } from "./components/Header";
import { EventLogStream } from "./components/EventLogStream";
import { useLifeOS } from "./hooks/useLifeOS";
import { useEventStream } from "./hooks/useEventStream";

export const App: React.FC = () => {
  const [baseUrl] = useState("http://127.0.0.1:8000");
  const { sidecarState, health, readiness, error, refresh } = useLifeOS(baseUrl);
  const { connectionState: sseState, events, clearEvents } = useEventStream(baseUrl);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "#f8fafc",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
      }}
    >
      <Header sidecarState={sidecarState} sseState={sseState} />

      <main style={{ flex: 1, padding: "20px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px", overflow: "hidden" }}>
        {/* Left: System & Sidecar Status */}
        <section
          style={{
            backgroundColor: "#ffffff",
            padding: "20px",
            borderRadius: "8px",
            border: "1px solid #e2e8f0",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            overflowY: "auto",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: "16px", fontWeight: 600, color: "#1e293b", margin: 0 }}>
              Sidecar Lifecycle & Health
            </h2>
            <button
              onClick={refresh}
              style={{
                backgroundColor: "#f1f5f9",
                border: "1px solid #cbd5e1",
                padding: "4px 10px",
                borderRadius: "4px",
                fontSize: "12px",
                cursor: "pointer",
              }}
            >
              Refresh
            </button>
          </div>

          {error && (
            <div
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

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", fontSize: "13px" }}>
            <div style={{ padding: "12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Backend Host</div>
              <div style={{ fontWeight: 600 }}>{sidecarState.host}:{sidecarState.port}</div>
            </div>
            <div style={{ padding: "12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Liveness Probe</div>
              <div style={{ fontWeight: 600, color: (health?.status === "ok" || health?.status === "healthy") ? "#16a34a" : "#dc2626" }}>
                {health?.status || "Unreachable"}
              </div>
            </div>
            <div style={{ padding: "12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Readiness State</div>
              <div style={{ fontWeight: 600, color: readiness?.status === "ready" ? "#16a34a" : "#dc2626" }}>
                {readiness?.status || "Unready"}
              </div>
            </div>
            <div style={{ padding: "12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Privacy Mode</div>
              <div style={{ fontWeight: 600, color: "#0284c7" }}>Local-First (Strict)</div>
            </div>
          </div>

          {readiness?.databases && (
            <div style={{ marginTop: "12px" }}>
              <h3 style={{ fontSize: "14px", fontWeight: 600, color: "#334155", marginBottom: "8px" }}>
                Verified Database Registries
              </h3>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "8px", fontSize: "12px" }}>
                {Object.entries(readiness.databases).map(([db, ok]) => (
                  <div
                    key={db}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      padding: "6px 10px",
                      borderRadius: "4px",
                      backgroundColor: ok ? "#f0fdf4" : "#fef2f2",
                      color: ok ? "#166534" : "#991b1b",
                    }}
                  >
                    <span>{ok ? "✓" : "✗"}</span>
                    <span>{db}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        {/* Right: Real-time Event Stream */}
        <section style={{ height: "100%", overflow: "hidden" }}>
          <EventLogStream events={events} onClear={clearEvents} />
        </section>
      </main>
    </div>
  );
};
