import React, { useCallback, useEffect, useState } from "react";
import { LifeOSWebClient } from "../api/client";
import {
  AuditVerifyResponse,
  ConsentGrant,
  PrivacyExportResponse,
  PrivacyForgetResponse,
  PrivacySummary,
} from "../api/types";

export interface PrivacyViewProps {
  client: LifeOSWebClient;
  initialSummary?: PrivacySummary | null;
  initialConsents?: ConsentGrant[];
  initialAuditVerify?: AuditVerifyResponse | null;
  initialExportData?: PrivacyExportResponse | null;
  initialForgetModalOpen?: boolean;
  initialForgetResult?: PrivacyForgetResponse | null;
}

export const PrivacyView: React.FC<PrivacyViewProps> = ({
  client,
  initialSummary = null,
  initialConsents = [],
  initialAuditVerify = null,
  initialExportData = null,
  initialForgetModalOpen = false,
  initialForgetResult = null,
}) => {
  const [summary, setSummary] = useState<PrivacySummary | null>(initialSummary);
  const [consents, setConsents] = useState<ConsentGrant[]>(initialConsents);
  const [auditVerify, setAuditVerify] = useState<AuditVerifyResponse | null>(initialAuditVerify);
  const [exportData, setExportData] = useState<PrivacyExportResponse | null>(initialExportData);
  const [forgetResult, setForgetResult] = useState<PrivacyForgetResponse | null>(initialForgetResult);

  const [isLoading, setIsLoading] = useState(false);
  const [isVerifying, setIsVerifying] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [revokingIds, setRevokingIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [modalError, setModalError] = useState<string | null>(null);

  // Cascading Forget Modal State
  const [forgetModalOpen, setForgetModalOpen] = useState(initialForgetModalOpen);
  const [forgetTargetType, setForgetTargetType] = useState<"path" | "person" | "all">("path");
  const [forgetTargetValue, setForgetTargetValue] = useState("");
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [isPurging, setIsPurging] = useState(false);

  const loadPrivacyState = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [sum, grants, verify] = await Promise.all([
        client.getPrivacySummary(),
        client.listConsents(),
        client.verifyAuditTrail(),
      ]);
      setSummary(sum);
      setConsents(grants);
      setAuditVerify(verify);
    } catch (err: any) {
      setError(err?.message || "Failed to load privacy dashboard state");
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    loadPrivacyState();
  }, [loadPrivacyState]);

  const handleVerifyAudit = async () => {
    setIsVerifying(true);
    setError(null);
    try {
      const res = await client.verifyAuditTrail();
      setAuditVerify(res);
    } catch (err: any) {
      setError(err?.message || "Audit verification failed");
    } finally {
      setIsVerifying(false);
    }
  };

  const handleRevokeConsent = async (consentId: string) => {
    setRevokingIds((prev) => {
      const next = new Set(prev);
      next.add(consentId);
      return next;
    });
    setError(null);
    try {
      await client.revokeConsent(consentId, "Revoked from Privacy Dashboard");
      setConsents((prev) =>
        prev.map((c) => (c.consent_id === consentId ? { ...c, status: "revoked" } : c))
      );
      const updatedSummary = await client.getPrivacySummary();
      setSummary(updatedSummary);
    } catch (err: any) {
      setError(err?.message || `Failed to revoke consent ${consentId}`);
    } finally {
      setRevokingIds((prev) => {
        const next = new Set(prev);
        next.delete(consentId);
        return next;
      });
    }
  };

  const handleGdprExport = async () => {
    setIsExporting(true);
    setError(null);
    try {
      const bundle = await client.exportUserData();
      setExportData(bundle);
    } catch (err: any) {
      setError(err?.message || "GDPR data export failed");
    } finally {
      setIsExporting(false);
    }
  };

  const handleDownloadExport = () => {
    if (!exportData || typeof Blob === "undefined" || typeof URL === "undefined") {
      return;
    }
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `lifeos-gdpr-export-${exportData.device_id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const handleExecuteForget = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!confirmPurge) {
      setModalError("Please confirm permanent purge before continuing.");
      return;
    }
    if ((forgetTargetType === "path" || forgetTargetType === "person") && !forgetTargetValue.trim()) {
      setModalError(`Please enter a target ${forgetTargetType} value.`);
      return;
    }

    setIsPurging(true);
    setModalError(null);
    try {
      const res = await client.forgetUserData({
        target_type: forgetTargetType,
        target_value: forgetTargetType === "all" ? undefined : forgetTargetValue.trim(),
        confirm_permanent_purge: confirmPurge,
      });
      setForgetResult(res);
      setForgetModalOpen(false);
      setForgetTargetValue("");
      setConfirmPurge(false);
      setModalError(null);
      await loadPrivacyState();
    } catch (err: any) {
      setModalError(err?.message || "Cascading forget purge failed");
    } finally {
      setIsPurging(false);
    }
  };

  const auditStatus: "loading" | "valid" | "invalid" | "unknown" =
    isLoading || isVerifying
      ? "loading"
      : auditVerify !== null && auditVerify !== undefined
      ? auditVerify.valid === false
        ? "invalid"
        : "valid"
      : summary !== null && summary !== undefined && typeof summary.audit_chain_valid === "boolean"
      ? summary.audit_chain_valid === false
        ? "invalid"
        : "valid"
      : "unknown";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px", overflowY: "auto" }}>
      {/* Header Controls */}
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
            Privacy Dashboard &amp; Cryptographic Governance
          </h2>
          <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>
            Local-first consent enforcement, tamper-evident audit chain, GDPR export, and right-to-be-forgotten purge.
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={loadPrivacyState}
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
            {isLoading ? "Refreshing..." : "Refresh"}
          </button>

          <button
            type="button"
            onClick={handleGdprExport}
            disabled={isExporting}
            style={{
              backgroundColor: "#2563eb",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              padding: "7px 14px",
              fontSize: "12px",
              fontWeight: 600,
              cursor: isExporting ? "not-allowed" : "pointer",
            }}
          >
            {isExporting ? "Exporting..." : "GDPR Export"}
          </button>

          <button
            type="button"
            onClick={() => {
              setError(null);
              setModalError(null);
              setForgetModalOpen(true);
            }}
            style={{
              backgroundColor: "#dc2626",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              padding: "7px 14px",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Cascading Forget...
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

      {forgetResult && (
        <div
          style={{
            backgroundColor: "#f0fdf4",
            color: "#166534",
            border: "1px solid #bbf7d0",
            padding: "10px 14px",
            borderRadius: "6px",
            fontSize: "13px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <strong>Purge Completed:</strong> {forgetResult.message} (Docs:{" "}
            {forgetResult.purged_documents_count}, Context: {forgetResult.purged_context_count}, People:{" "}
            {forgetResult.purged_people_count})
          </div>
          <button
            type="button"
            onClick={() => setForgetResult(null)}
            style={{
              backgroundColor: "transparent",
              border: "none",
              color: "#166534",
              cursor: "pointer",
              fontWeight: 600,
            }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Privacy Posture Card + Audit Chain Verification */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: "16px" }}>
        <section
          style={{
            backgroundColor: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "16px",
          }}
        >
          <h3 style={{ margin: "0 0 12px 0", fontSize: "14px", fontWeight: 600, color: "#1e293b" }}>
            Privacy Posture
          </h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "12px", fontSize: "13px" }}>
            <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", fontSize: "11px" }}>Execution Mode</div>
              <div style={{ fontWeight: 600, color: summary?.local_first_mode !== false ? "#16a34a" : "#d97706", marginTop: "4px" }}>
                {summary?.local_first_mode !== false ? "Local-First (Strict)" : "Hybrid"}
              </div>
            </div>
            <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", fontSize: "11px" }}>Telemetry</div>
              <div style={{ fontWeight: 600, color: summary?.telemetry_enabled ? "#dc2626" : "#16a34a", marginTop: "4px" }}>
                {summary?.telemetry_enabled ? "Enabled" : "Disabled (0 bytes)"}
              </div>
            </div>
            <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", fontSize: "11px" }}>Cloud Sync</div>
              <div style={{ fontWeight: 600, color: summary?.cloud_sync_enabled ? "#d97706" : "#0284c7", marginTop: "4px" }}>
                {summary?.cloud_sync_enabled ? "Enabled" : "Disabled"}
              </div>
            </div>
            <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", fontSize: "11px" }}>Consented Folders</div>
              <div style={{ fontWeight: 700, fontSize: "16px", color: "#1e293b", marginTop: "4px" }}>
                {summary?.active_consented_sources ?? consents.filter((c) => c.status === "active").length}
              </div>
            </div>
            <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", fontSize: "11px" }}>Indexed Documents</div>
              <div style={{ fontWeight: 700, fontSize: "16px", color: "#1e293b", marginTop: "4px" }}>
                {summary?.total_indexed_documents ?? 0}
              </div>
            </div>
            <div style={{ padding: "10px", backgroundColor: "#f8fafc", borderRadius: "6px", border: "1px solid #e2e8f0" }}>
              <div style={{ color: "#64748b", fontSize: "11px" }}>Audit Events</div>
              <div style={{ fontWeight: 700, fontSize: "16px", color: "#1e293b", marginTop: "4px" }}>
                {summary?.total_audit_events ?? auditVerify?.verified_records ?? 0}
              </div>
            </div>
          </div>
        </section>

        {/* Cryptographic Audit Chain Verification Badge */}
        <section
          style={{
            backgroundColor: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "16px",
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          <div>
            <h3 style={{ margin: "0 0 10px 0", fontSize: "14px", fontWeight: 600, color: "#1e293b" }}>
              SHA-256 Audit Chain
            </h3>
            <div
              data-testid="audit-chain-badge"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                padding: "8px 12px",
                borderRadius: "6px",
                backgroundColor:
                  auditStatus === "valid"
                    ? "#dcfce7"
                    : auditStatus === "invalid"
                    ? "#fee2e2"
                    : "#f1f5f9",
                color:
                  auditStatus === "valid"
                    ? "#15803d"
                    : auditStatus === "invalid"
                    ? "#b91c1c"
                    : "#475569",
                border: `1px solid ${
                  auditStatus === "valid"
                    ? "#86efac"
                    : auditStatus === "invalid"
                    ? "#fca5a5"
                    : "#cbd5e1"
                }`,
                fontWeight: 600,
                fontSize: "13px",
              }}
            >
              <span>
                {auditStatus === "valid"
                  ? "✓"
                  : auditStatus === "invalid"
                  ? "✗"
                  : auditStatus === "loading"
                  ? "…"
                  : "?"}
              </span>
              <span>
                {auditStatus === "valid"
                  ? "Audit Chain Verified"
                  : auditStatus === "invalid"
                  ? "Integrity Violation"
                  : auditStatus === "loading"
                  ? "Checking..."
                  : "Unknown"}
              </span>
            </div>
            <div style={{ marginTop: "10px", fontSize: "12px", color: "#475569" }}>
              Verified Records: <strong>{auditVerify?.verified_records ?? summary?.total_audit_events ?? 0}</strong>
            </div>
            {auditVerify?.error && (
              <div style={{ marginTop: "6px", fontSize: "12px", color: "#b91c1c" }}>
                Error: {auditVerify.error}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={handleVerifyAudit}
            disabled={isVerifying}
            style={{
              marginTop: "12px",
              backgroundColor: "#f8fafc",
              color: "#1e293b",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              padding: "7px 12px",
              fontSize: "12px",
              fontWeight: 600,
              cursor: isVerifying ? "not-allowed" : "pointer",
            }}
          >
            {isVerifying ? "Verifying SHA-256 Chain..." : "Re-verify Integrity"}
          </button>
        </section>
      </div>

      {/* GDPR Export Summary Panel */}
      {exportData && (
        <section
          style={{
            backgroundColor: "#eff6ff",
            border: "1px solid #bfdbfe",
            borderRadius: "8px",
            padding: "14px 16px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
            <div style={{ fontWeight: 600, fontSize: "14px", color: "#1e3a8a" }}>
              GDPR User Data Export Ready ({exportData.exported_at})
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                type="button"
                onClick={handleDownloadExport}
                style={{
                  backgroundColor: "#2563eb",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "4px",
                  padding: "4px 10px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Download JSON
              </button>
              <button
                type="button"
                onClick={() => setExportData(null)}
                style={{
                  backgroundColor: "#ffffff",
                  color: "#475569",
                  border: "1px solid #cbd5e1",
                  borderRadius: "4px",
                  padding: "4px 10px",
                  fontSize: "12px",
                  cursor: "pointer",
                }}
              >
                Close
              </button>
            </div>
          </div>
          <div style={{ fontSize: "12px", color: "#1e293b", display: "flex", gap: "16px", flexWrap: "wrap" }}>
            <span>Device: <strong>{exportData.device_id ?? "—"}</strong></span>
            <span>Consents: <strong>{Array.isArray(exportData.consents) ? exportData.consents.length : 0}</strong></span>
            <span>Documents: <strong>{Array.isArray(exportData.documents) ? exportData.documents.length : 0}</strong></span>
            <span>Memories: <strong>{Array.isArray(exportData.context_memories) ? exportData.context_memories.length : 0}</strong></span>
            <span>People: <strong>{Array.isArray(exportData.people) ? exportData.people.length : 0}</strong></span>
            <span>Audit Entries: <strong>{Array.isArray(exportData.audit_log) ? exportData.audit_log.length : 0}</strong></span>
          </div>
        </section>
      )}

      {/* Consented Folders Table */}
      <section
        style={{
          backgroundColor: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: "8px",
          padding: "16px",
        }}
      >
        <h3 style={{ margin: "0 0 12px 0", fontSize: "14px", fontWeight: 600, color: "#1e293b" }}>
          Consented Data Sources ({consents.length})
        </h3>
        {consents.length === 0 ? (
          <div style={{ fontSize: "13px", color: "#64748b", padding: "12px 0" }}>
            No consented folders or connectors registered yet.
          </div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "1px solid #e2e8f0", color: "#64748b", fontSize: "12px" }}>
                  <th style={{ padding: "8px 10px" }}>Resource Path</th>
                  <th style={{ padding: "8px 10px" }}>Scope</th>
                  <th style={{ padding: "8px 10px" }}>Purposes</th>
                  <th style={{ padding: "8px 10px" }}>Status</th>
                  <th style={{ padding: "8px 10px", textAlign: "right" }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {consents.map((grant) => {
                  const isActive = grant.status === "active";
                  const isRevoking = revokingIds.has(grant.consent_id);
                  return (
                    <tr key={grant.consent_id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                      <td style={{ padding: "10px", fontWeight: 500, color: "#0f172a", wordBreak: "break-all" }}>
                        {grant.resource_path}
                      </td>
                      <td style={{ padding: "10px", color: "#475569" }}>{grant.scope_type}</td>
                      <td style={{ padding: "10px", color: "#475569" }}>
                        {Array.isArray(grant.purposes) ? grant.purposes.join(", ") : "—"}
                      </td>
                      <td style={{ padding: "10px" }}>
                        <span
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "2px 8px",
                            borderRadius: "9999px",
                            backgroundColor: isActive ? "#dcfce7" : "#f1f5f9",
                            color: isActive ? "#15803d" : "#64748b",
                          }}
                        >
                          {grant.status}
                        </span>
                      </td>
                      <td style={{ padding: "10px", textAlign: "right" }}>
                        {isActive ? (
                          <button
                            type="button"
                            onClick={() => handleRevokeConsent(grant.consent_id)}
                            disabled={isRevoking}
                            style={{
                              backgroundColor: "#fee2e2",
                              color: "#b91c1c",
                              border: "1px solid #fca5a5",
                              borderRadius: "4px",
                              padding: "4px 10px",
                              fontSize: "12px",
                              fontWeight: 600,
                              cursor: isRevoking ? "not-allowed" : "pointer",
                            }}
                          >
                            {isRevoking ? "Revoking..." : "Revoke"}
                          </button>
                        ) : (
                          <span style={{ fontSize: "12px", color: "#94a3b8" }}>Revoked</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Cascading Forget Modal */}
      {forgetModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Cascading Forget Data Purge"
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
            onSubmit={handleExecuteForget}
            style={{
              backgroundColor: "#ffffff",
              borderRadius: "10px",
              border: "1px solid #e2e8f0",
              maxWidth: "460px",
              width: "100%",
              padding: "20px",
              boxShadow: "0 10px 25px rgba(0, 0, 0, 0.15)",
              display: "flex",
              flexDirection: "column",
              gap: "14px",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h3 style={{ margin: 0, fontSize: "16px", fontWeight: 700, color: "#991b1b" }}>
                Cascading Forget (Right to be Forgotten)
              </h3>
              <button
                type="button"
                onClick={() => {
                  setModalError(null);
                  setForgetModalOpen(false);
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

            <div style={{ fontSize: "12px", color: "#475569", backgroundColor: "#fef2f2", padding: "10px", borderRadius: "6px", border: "1px solid #fecaca" }}>
              Permanently removes matching documents, vector embeddings, person records, and context memories. This cannot be undone.
            </div>

            <div>
              <label htmlFor="forget-target-type" style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                Target Scope
              </label>
              <select
                id="forget-target-type"
                value={forgetTargetType}
                onChange={(e) => setForgetTargetType(e.target.value as "path" | "person" | "all")}
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  borderRadius: "6px",
                  border: "1px solid #cbd5e1",
                  fontSize: "13px",
                }}
              >
                <option value="path">Specific Path / Folder (path)</option>
                <option value="person">Person Entity (person)</option>
                <option value="all">Complete Local Data Purge (all)</option>
              </select>
            </div>

            {(forgetTargetType === "path" || forgetTargetType === "person") && (
              <div>
                <label htmlFor="forget-target-value" style={{ display: "block", fontSize: "12px", fontWeight: 600, color: "#334155", marginBottom: "4px" }}>
                  {forgetTargetType === "path" ? "Path to Purge" : "Person Name / ID"}
                </label>
                <input
                  id="forget-target-value"
                  type="text"
                  value={forgetTargetValue}
                  onChange={(e) => setForgetTargetValue(e.target.value)}
                  placeholder={forgetTargetType === "path" ? "C:\\Users\\...\\PrivateFolder" : "Jane Doe"}
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
            )}

            <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", color: "#1e293b", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={confirmPurge}
                onChange={(e) => setConfirmPurge(e.target.checked)}
              />
              <span>I confirm permanent purge of the selected local data</span>
            </label>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "4px" }}>
              <button
                type="button"
                onClick={() => {
                  setModalError(null);
                  setForgetModalOpen(false);
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
                disabled={isPurging || !confirmPurge}
                style={{
                  backgroundColor: isPurging || !confirmPurge ? "#fca5a5" : "#dc2626",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  padding: "7px 14px",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: isPurging || !confirmPurge ? "not-allowed" : "pointer",
                }}
              >
                {isPurging ? "Purging..." : "Execute Permanent Purge"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
