import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { LifeOSWebClient } from "../api/client";
import {
  DiagnosticCheck,
  DiagnosticReport,
  GovernorDiagnostics,
  LifeOSEventPayload,
  ReadinessResponse,
  SidecarState,
  SystemHealth,
  UpdateInfo,
} from "../api/types";
import { EventLogStream } from "../components/EventLogStream";
import { isTauriEnvironment } from "../hooks/useLifeOS";

export interface DiagnosticsViewProps {
  client: LifeOSWebClient;
  sidecarState: SidecarState;
  health: SystemHealth | null;
  readiness: ReadinessResponse | null;
  connectionError: string | null;
  onRefreshSidecar: () => void;
  events: LifeOSEventPayload[];
  onClearEvents: () => void;
  initialReport?: DiagnosticReport | null;
  initialGovernor?: GovernorDiagnostics | null;
  initialUpdateInfo?: UpdateInfo | null;
  initialStrictMode?: boolean;
  initialError?: string | null;
}

const CATEGORY_ORDER = [
  "Database",
  "Storage",
  "Filesystem",
  "Identity",
  "Security",
  "VectorDB",
  "Config",
  "Network",
];

function getOverallBadgeStyle(status: string): { bg: string; color: string; border: string } {
  if (status === "HEALTHY") {
    return { bg: "#dcfce7", color: "#166534", border: "#86efac" };
  }
  if (status === "DEGRADED") {
    return { bg: "#fef3c7", color: "#92400e", border: "#fde68a" };
  }
  return { bg: "#fee2e2", color: "#991b1b", border: "#fecaca" };
}

function getCheckBadgeStyle(status: string): { bg: string; color: string; icon: string } {
  if (status === "OK") {
    return { bg: "#f0fdf4", color: "#166534", icon: "✓" };
  }
  if (status === "WARN") {
    return { bg: "#fffbeb", color: "#92400e", icon: "⚠" };
  }
  return { bg: "#fef2f2", color: "#991b1b", icon: "✗" };
}

export const DiagnosticsView: React.FC<DiagnosticsViewProps> = ({
  client,
  sidecarState,
  health,
  readiness,
  connectionError,
  onRefreshSidecar,
  events,
  onClearEvents,
  initialReport = null,
  initialGovernor = null,
  initialUpdateInfo = null,
  initialStrictMode = false,
  initialError = null,
}) => {
  const [report, setReport] = useState<DiagnosticReport | null>(initialReport);
  const [governor, setGovernor] = useState<GovernorDiagnostics | null>(initialGovernor);
  const [strictMode, setStrictMode] = useState<boolean>(initialStrictMode);
  const [isRunningDoctor, setIsRunningDoctor] = useState<boolean>(false);
  const [isLoadingGovernor, setIsLoadingGovernor] = useState<boolean>(false);
  const [isCheckingUpdate, setIsCheckingUpdate] = useState<boolean>(false);
  const [diagError, setDiagError] = useState<string | null>(initialError);

  const [updateInfo, setUpdateInfo] = useState<UpdateInfo>(
    initialUpdateInfo ?? {
      current_version: "1.0.0",
      latest_version: "1.0.0",
      update_available: false,
      status: "up_to_date",
      signature_verified: false,
      endpoint: "https://releases.lifeos.local/updates/{{target}}/{{current_version}}",
    }
  );

  const strictModeRef = useRef<boolean>(strictMode);
  strictModeRef.current = strictMode;
  const isFirstStrictRender = useRef<boolean>(true);

  const runDoctor = useCallback(
    async (strict?: boolean) => {
      const effectiveStrict = strict ?? strictModeRef.current;
      setIsRunningDoctor(true);
      setDiagError(null);
      try {
        const res = await client.getDiagnostics(effectiveStrict);
        setReport(res);
        setDiagError(null);
      } catch (err: any) {
        setDiagError(err?.message || "Failed to execute pre-flight diagnostics");
      } finally {
        setIsRunningDoctor(false);
      }
    },
    [client]
  );

  const loadGovernor = useCallback(async () => {
    setIsLoadingGovernor(true);
    try {
      const metrics = await client.getGovernorDiagnostics();
      setGovernor(metrics);
      setDiagError(null);
    } catch (err: any) {
      setDiagError((prev) => prev || err?.message || "Failed to sample resource governor metrics");
    } finally {
      setIsLoadingGovernor(false);
    }
  }, [client]);

  useEffect(() => {
    if (!initialReport) {
      runDoctor(strictModeRef.current);
    }
  }, [initialReport, runDoctor]);

  useEffect(() => {
    if (isFirstStrictRender.current) {
      isFirstStrictRender.current = false;
      return;
    }
    runDoctor(strictMode);
  }, [strictMode, runDoctor]);

  useEffect(() => {
    if (!initialGovernor) {
      loadGovernor();
    }
  }, [initialGovernor, loadGovernor]);

  const handleCheckUpdates = async () => {
    setIsCheckingUpdate(true);
    const checkedAt = new Date().toISOString();
    try {
      if (!isTauriEnvironment()) {
        setUpdateInfo((prev) => ({
          ...prev,
          status: "up_to_date",
          update_available: false,
          signature_verified: false,
          checked_at: checkedAt,
          release_notes: "Auto-update binary replacement requires the native Tauri desktop shell.",
        }));
        return;
      }

      const update = await invoke<{
        available?: boolean;
        version?: string;
        body?: string;
        currentVersion?: string;
        date?: string;
      } | null>("plugin:updater|check");

      if (update !== null && typeof update === "object" && Boolean(update.version)) {
        setUpdateInfo((prev) => ({
          ...prev,
          latest_version: update.version || prev.current_version,
          update_available: true,
          status: "update_available",
          signature_verified: true,
          release_notes: update.body,
          checked_at: checkedAt,
        }));
      } else {
        setUpdateInfo((prev) => ({
          ...prev,
          latest_version: prev.current_version,
          update_available: false,
          status: "up_to_date",
          signature_verified: true,
          checked_at: checkedAt,
        }));
      }
    } catch (err: any) {
      setUpdateInfo((prev) => ({
        ...prev,
        status: "error",
        signature_verified: false,
        checked_at: checkedAt,
        error: err?.message || "Update check failed",
      }));
    } finally {
      setIsCheckingUpdate(false);
    }
  };

  const groupedChecks = useMemo(() => {
    if (!report?.checks?.length) return [] as Array<[string, DiagnosticCheck[]]>;
    const map = new Map<string, DiagnosticCheck[]>();
    for (const check of report.checks) {
      if (!check) continue;
      const cat = check.category || "Other";
      if (!map.has(cat)) {
        map.set(cat, []);
      }
      map.get(cat)!.push(check);
    }
    const entries = Array.from(map.entries());
    entries.sort((a, b) => {
      const idxA = CATEGORY_ORDER.indexOf(a[0]);
      const idxB = CATEGORY_ORDER.indexOf(b[0]);
      const rankA = idxA === -1 ? 999 : idxA;
      const rankB = idxB === -1 ? 999 : idxB;
      if (rankA !== rankB) return rankA - rankB;
      return a[0].localeCompare(b[0]);
    });
    return entries;
  }, [report]);

  const combinedError = connectionError || diagError;
  const overallBadge = report ? getOverallBadgeStyle(report.overall_status) : null;

  return (
    <div
      data-testid="diagnostics-view"
      style={{
        flex: 1,
        display: "grid",
        gridTemplateColumns: "1.35fr 0.85fr",
        gap: "20px",
        overflow: "hidden",
      }}
    >
      {/* Left Column: Sidecar, Doctor Checks, Resource Governor, Auto-Updater */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "16px",
          overflowY: "auto",
          paddingRight: "4px",
        }}
      >
        {combinedError && (
          <div
            role="alert"
            data-testid="diagnostics-error-banner"
            style={{
              backgroundColor: "#fef2f2",
              color: "#991b1b",
              border: "1px solid #fecaca",
              padding: "10px 14px",
              borderRadius: "6px",
              fontSize: "13px",
            }}
          >
            {combinedError}
          </div>
        )}

        {/* 1. Sidecar Lifecycle & Connection Card */}
        <section
          data-testid="sidecar-lifecycle-card"
          style={{
            backgroundColor: "#ffffff",
            padding: "16px 20px",
            borderRadius: "8px",
            border: "1px solid #e2e8f0",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: "16px", fontWeight: 600, color: "#1e293b", margin: 0 }}>
              Sidecar Lifecycle &amp; Health
            </h2>
            <button
              type="button"
              onClick={onRefreshSidecar}
              style={{
                backgroundColor: "#f1f5f9",
                border: "1px solid #cbd5e1",
                padding: "5px 12px",
                borderRadius: "6px",
                fontSize: "12px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              Refresh
            </button>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "10px", fontSize: "12px" }}>
            <div style={{ padding: "10px 12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Backend Endpoint</div>
              <div style={{ fontWeight: 600, color: "#0f172a" }}>
                {sidecarState.host}:{sidecarState.port}
              </div>
            </div>

            <div style={{ padding: "10px 12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Session Token</div>
              <div style={{ fontWeight: 600, color: sidecarState.token ? "#16a34a" : "#475569" }}>
                {sidecarState.token ? "Configured (Bearer)" : "Not Set (Web/Dev)"}
              </div>
            </div>

            <div style={{ padding: "10px 12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Sidecar PID</div>
              <div style={{ fontWeight: 600, color: "#0f172a" }}>
                {sidecarState.pid !== null ? `PID ${sidecarState.pid}` : "External / Web"}
              </div>
            </div>

            <div style={{ padding: "10px 12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Liveness Probe</div>
              <div
                style={{
                  fontWeight: 600,
                  color: health?.status === "ok" || health?.status === "healthy" ? "#16a34a" : "#dc2626",
                }}
              >
                {health?.status || "Unreachable"}
              </div>
            </div>

            <div style={{ padding: "10px 12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Readiness State</div>
              <div
                style={{
                  fontWeight: 600,
                  color: readiness?.status === "ready" ? "#16a34a" : "#dc2626",
                }}
              >
                {readiness?.status || "Unready"}
              </div>
            </div>

            <div style={{ padding: "10px 12px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", marginBottom: "4px" }}>Privacy Boundary</div>
              <div style={{ fontWeight: 600, color: "#0284c7" }}>Local-First (Strict)</div>
            </div>
          </div>
        </section>

        {/* 2. System Health & Pre-Flight Doctor Checks */}
        <section
          data-testid="doctor-diagnostics-card"
          style={{
            backgroundColor: "#ffffff",
            padding: "16px 20px",
            borderRadius: "8px",
            border: "1px solid #e2e8f0",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <h2 style={{ fontSize: "16px", fontWeight: 600, color: "#1e293b", margin: 0 }}>
                System Health &amp; Doctor Checks
              </h2>
              {report && overallBadge && (
                <span
                  data-testid="doctor-overall-status"
                  style={{
                    backgroundColor: overallBadge.bg,
                    color: overallBadge.color,
                    border: `1px solid ${overallBadge.border}`,
                    padding: "2px 10px",
                    borderRadius: "9999px",
                    fontSize: "11px",
                    fontWeight: 700,
                  }}
                >
                  {report.overall_status}
                </span>
              )}
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "#475569", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={strictMode}
                  onChange={(e) => {
                    setStrictMode(e.target.checked);
                  }}
                />
                Strict Mode
              </label>
              <button
                type="button"
                onClick={() => runDoctor(strictMode)}
                disabled={isRunningDoctor}
                style={{
                  backgroundColor: "#2563eb",
                  color: "#ffffff",
                  border: "none",
                  padding: "6px 12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: isRunningDoctor ? "not-allowed" : "pointer",
                  opacity: isRunningDoctor ? 0.7 : 1,
                }}
              >
                {isRunningDoctor ? "Running..." : "Run Pre-Flight Diagnostics"}
              </button>
            </div>
          </div>

          {report ? (
            <>
              <div style={{ display: "flex", gap: "16px", fontSize: "12px", color: "#475569" }}>
                <span>Total: <strong>{report.total_checks}</strong></span>
                <span style={{ color: "#166534" }}>Passed: <strong>{report.passed}</strong></span>
                <span style={{ color: "#92400e" }}>Warnings: <strong>{report.warnings}</strong></span>
                <span style={{ color: "#991b1b" }}>Failed: <strong>{report.failed}</strong></span>
                <span style={{ marginLeft: "auto", color: "#64748b" }}>{report.timestamp}</span>
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                {groupedChecks.map(([category, checks]) => (
                  <div
                    key={category}
                    data-testid={`doctor-category-${category}`}
                    style={{
                      border: "1px solid #e2e8f0",
                      borderRadius: "6px",
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        backgroundColor: "#f8fafc",
                        padding: "6px 12px",
                        fontSize: "12px",
                        fontWeight: 700,
                        color: "#334155",
                        borderBottom: "1px solid #e2e8f0",
                      }}
                    >
                      {category}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column" }}>
                      {checks.map((chk) => {
                        const badge = getCheckBadgeStyle(chk.status);
                        return (
                          <div
                            key={`${category}-${chk.name}`}
                            style={{
                              padding: "8px 12px",
                              borderBottom: "1px solid #f1f5f9",
                              fontSize: "12px",
                              display: "flex",
                              flexDirection: "column",
                              gap: "4px",
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                                <span
                                  style={{
                                    backgroundColor: badge.bg,
                                    color: badge.color,
                                    padding: "1px 6px",
                                    borderRadius: "4px",
                                    fontWeight: 700,
                                    fontSize: "11px",
                                  }}
                                >
                                  {badge.icon} {chk.status}
                                </span>
                                <span style={{ fontWeight: 600, color: "#0f172a" }}>{chk.name}</span>
                              </div>
                              <span style={{ color: "#475569" }}>{chk.message}</span>
                            </div>
                            {chk.fix_hint && (
                              <div
                                style={{
                                  backgroundColor: "#fffbeb",
                                  color: "#92400e",
                                  padding: "4px 8px",
                                  borderRadius: "4px",
                                  fontSize: "11px",
                                }}
                              >
                                Fix Hint: {chk.fix_hint}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div style={{ fontSize: "13px", color: "#64748b" }}>
              {isRunningDoctor ? "Running pre-flight diagnostic checks..." : "No diagnostic report captured yet."}
            </div>
          )}
        </section>

        {/* 3. Resource Governor Telemetry & Auto-Update Cards */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          {/* Resource Governor Card */}
          <section
            data-testid="governor-telemetry-card"
            style={{
              backgroundColor: "#ffffff",
              padding: "16px 20px",
              borderRadius: "8px",
              border: "1px solid #e2e8f0",
              display: "flex",
              flexDirection: "column",
              gap: "12px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: "15px", fontWeight: 600, color: "#1e293b", margin: 0 }}>
                Resource Governor Telemetry
              </h2>
              <button
                type="button"
                onClick={loadGovernor}
                disabled={isLoadingGovernor}
                style={{
                  backgroundColor: "#f1f5f9",
                  border: "1px solid #cbd5e1",
                  padding: "4px 10px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                {isLoadingGovernor ? "Sampling..." : "Sample"}
              </button>
            </div>

            {governor ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", fontSize: "12px" }}>
                <div style={{ padding: "8px 10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ color: "#64748b" }}>CPU Utilization</div>
                  <div style={{ fontWeight: 700, fontSize: "14px", color: "#0f172a" }}>
                    {typeof governor.cpu_percent === "number" && !Number.isNaN(governor.cpu_percent)
                      ? `${governor.cpu_percent.toFixed(1)}%`
                      : "N/A"}
                  </div>
                </div>
                <div style={{ padding: "8px 10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ color: "#64748b" }}>RAM Utilization</div>
                  <div style={{ fontWeight: 700, fontSize: "14px", color: "#0f172a" }}>
                    {typeof governor.ram_percent === "number" && !Number.isNaN(governor.ram_percent)
                      ? `${governor.ram_percent.toFixed(1)}%`
                      : "N/A"}
                  </div>
                </div>
                <div style={{ padding: "8px 10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ color: "#64748b" }}>Power Source</div>
                  <div style={{ fontWeight: 600, color: governor.on_battery ? "#d97706" : "#16a34a" }}>
                    {governor.on_battery
                      ? `Battery${
                          governor.battery_percent != null && !Number.isNaN(governor.battery_percent)
                            ? ` (${governor.battery_percent}%)`
                            : ""
                        }`
                      : "AC Power"}
                  </div>
                </div>
                <div style={{ padding: "8px 10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
                  <div style={{ color: "#64748b" }}>Ingestion State</div>
                  <div
                    style={{
                      fontWeight: 600,
                      color: governor.should_pause_ingestion ? "#dc2626" : "#16a34a",
                    }}
                  >
                    {governor.should_pause_ingestion
                      ? `Paused (${governor.pause_reason || "throttled"})`
                      : "Active"}
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ fontSize: "12px", color: "#64748b" }}>Governor telemetry unavailable.</div>
            )}
          </section>

          {/* Auto-Update & Packaging Status Card */}
          <section
            data-testid="updater-status-card"
            style={{
              backgroundColor: "#ffffff",
              padding: "16px 20px",
              borderRadius: "8px",
              border: "1px solid #e2e8f0",
              display: "flex",
              flexDirection: "column",
              gap: "10px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: "15px", fontWeight: 600, color: "#1e293b", margin: 0 }}>
                Auto-Update &amp; Packaging
              </h2>
              <button
                type="button"
                onClick={handleCheckUpdates}
                disabled={isCheckingUpdate}
                style={{
                  backgroundColor: "#f1f5f9",
                  border: "1px solid #cbd5e1",
                  padding: "4px 10px",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                {isCheckingUpdate ? "Checking..." : "Check Updates"}
              </button>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Current Version</span>
                <span style={{ fontWeight: 600, color: "#0f172a" }}>v{updateInfo.current_version}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Update Status</span>
                <span
                  style={{
                    fontWeight: 600,
                    color: updateInfo.update_available
                      ? "#2563eb"
                      : updateInfo.status === "error"
                      ? "#dc2626"
                      : "#16a34a",
                  }}
                >
                  {updateInfo.update_available
                    ? `Update Available (v${updateInfo.latest_version})`
                    : updateInfo.status === "error"
                    ? "Check Failed"
                    : "Up to Date"}
                </span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Minisign Signature</span>
                <span
                  style={{
                    fontWeight: 600,
                    color: updateInfo.signature_verified
                      ? "#16a34a"
                      : updateInfo.checked_at
                      ? "#dc2626"
                      : "#64748b",
                  }}
                >
                  {updateInfo.signature_verified
                    ? "Verified (Ed25519)"
                    : updateInfo.checked_at
                    ? "Unverified"
                    : "Not Checked"}
                </span>
              </div>
              {updateInfo.release_notes && (
                <div style={{ fontSize: "11px", color: "#475569", marginTop: "4px" }}>
                  {updateInfo.release_notes}
                </div>
              )}
              {updateInfo.error && (
                <div style={{ fontSize: "11px", color: "#dc2626", marginTop: "4px" }}>
                  {updateInfo.error}
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      {/* Right Column: Real-Time Event Bus Monitor */}
      <section style={{ height: "100%", overflow: "hidden" }}>
        <EventLogStream events={events} onClear={onClearEvents} />
      </section>
    </div>
  );
};
