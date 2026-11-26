import React, { useCallback, useEffect, useState } from "react";
import { LifeOSWebClient } from "../api/client";
import {
  CryptographicEnvelope,
  LifeOSEventPayload,
  PeerSyncStatus,
  SyncConflict,
  SyncExportResponse,
  SyncImportResponse,
} from "../api/types";
import { QrPairingModal } from "../components/QrPairingModal";

export interface DiffFieldRow {
  key: string;
  localVal: string;
  remoteVal: string;
  isDifferent: boolean;
}

function isPlainRecord(val: unknown): val is Record<string, any> {
  return typeof val === "object" && val !== null && !Array.isArray(val);
}

function canonicalizeValue(val: unknown): unknown {
  if (val === null || typeof val !== "object") {
    return val;
  }
  if (Array.isArray(val)) {
    return val.map(canonicalizeValue);
  }
  const sorted: Record<string, unknown> = {};
  for (const k of Object.keys(val as Record<string, unknown>).sort()) {
    const v = (val as Record<string, unknown>)[k];
    if (v !== undefined) {
      sorted[k] = canonicalizeValue(v);
    }
  }
  return sorted;
}

function areDiffValuesEqual(
  a: unknown,
  aExists: boolean,
  b: unknown,
  bExists: boolean
): boolean {
  if (!aExists && !bExists) {
    return true;
  }
  if (aExists !== bExists) {
    return false;
  }
  if (typeof a !== typeof b) {
    return false;
  }
  if (a === null || b === null || typeof a !== "object") {
    return Object.is(a, b);
  }
  return JSON.stringify(canonicalizeValue(a)) === JSON.stringify(canonicalizeValue(b));
}

function formatDiffDisplayValue(val: unknown, exists: boolean): string {
  if (!exists || val === undefined) {
    return "—";
  }
  if (typeof val === "string") {
    return val;
  }
  return JSON.stringify(canonicalizeValue(val));
}

export function computeDataDiff(
  localData: Record<string, any>,
  remoteData: Record<string, any>
): DiffFieldRow[] {
  const safeLocal: Record<string, any> = isPlainRecord(localData)
    ? localData
    : localData !== undefined && localData !== null
    ? { _value: localData }
    : {};
  const safeRemote: Record<string, any> = isPlainRecord(remoteData)
    ? remoteData
    : remoteData !== undefined && remoteData !== null
    ? { _value: remoteData }
    : {};

  const keys = Array.from(new Set([...Object.keys(safeLocal), ...Object.keys(safeRemote)])).sort();
  return keys.map((key) => {
    const hasLocal = Object.prototype.hasOwnProperty.call(safeLocal, key) && safeLocal[key] !== undefined;
    const hasRemote = Object.prototype.hasOwnProperty.call(safeRemote, key) && safeRemote[key] !== undefined;
    const lRaw = hasLocal ? safeLocal[key] : undefined;
    const rRaw = hasRemote ? safeRemote[key] : undefined;
    return {
      key,
      localVal: formatDiffDisplayValue(lRaw, hasLocal),
      remoteVal: formatDiffDisplayValue(rRaw, hasRemote),
      isDifferent: !areDiffValuesEqual(lRaw, hasLocal, rRaw, hasRemote),
    };
  });
}

export function getTrustBadgeStyle(trustStatus: string | undefined | null): {
  label: string;
  bg: string;
  color: string;
  border: string;
} {
  const normalized = (trustStatus || "unknown").toLowerCase();
  switch (normalized) {
    case "paired":
    case "trusted":
      return { label: "Trusted / Paired", bg: "#dcfce7", color: "#15803d", border: "#86efac" };
    case "pending":
      return { label: "Pending", bg: "#fef3c7", color: "#b45309", border: "#fcd34d" };
    case "revoked":
      return { label: "Revoked", bg: "#fee2e2", color: "#b91c1c", border: "#fca5a5" };
    default:
      return { label: trustStatus || "unknown", bg: "#f1f5f9", color: "#475569", border: "#cbd5e1" };
  }
}

export interface SyncViewProps {
  client: LifeOSWebClient;
  activeWorkspaceId?: string;
  initialPeers?: PeerSyncStatus[];
  initialConflicts?: SyncConflict[];
  initialExportResult?: SyncExportResponse | null;
  initialImportResult?: SyncImportResponse | null;
  events?: LifeOSEventPayload[];
}

export const SyncView: React.FC<SyncViewProps> = ({
  client,
  activeWorkspaceId,
  initialPeers = [],
  initialConflicts = [],
  initialExportResult = null,
  initialImportResult = null,
  events = [],
}) => {
  const [peers, setPeers] = useState<PeerSyncStatus[]>(initialPeers);
  const [conflicts, setConflicts] = useState<SyncConflict[]>(initialConflicts);
  const [selectedPeerId, setSelectedPeerId] = useState<string>(
    initialPeers.length > 0 ? initialPeers[0].peer_device_id : ""
  );
  const [exportResult, setExportResult] = useState<SyncExportResponse | null>(initialExportResult);
  const [importResult, setImportResult] = useState<SyncImportResponse | null>(initialImportResult);
  const [importJson, setImportJson] = useState<string>("");

  const [customEditConflictId, setCustomEditConflictId] = useState<string | null>(null);
  const [customJson, setCustomJson] = useState<string>("");

  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [revokingPeerId, setRevokingPeerId] = useState<string | null>(null);
  const [isQrModalOpen, setIsQrModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSyncState = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [peerList, conflictList] = await Promise.all([
        client.listSyncPeers(),
        client.listSyncConflicts("unresolved", activeWorkspaceId),
      ]);
      setPeers(peerList);
      setConflicts(conflictList);
      if (peerList.length > 0) {
        setSelectedPeerId((prev) => prev || peerList[0].peer_device_id);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load sync peers and conflicts");
    } finally {
      setIsLoading(false);
    }
  }, [client, activeWorkspaceId]);

  useEffect(() => {
    loadSyncState();
  }, [loadSyncState]);

  const handleExportPackage = async () => {
    if (!selectedPeerId) {
      setError("Select a paired peer device before exporting a sync package.");
      return;
    }
    setIsExporting(true);
    setError(null);
    try {
      const res = await client.exportSyncPackage({
        peer_device_id: selectedPeerId,
        workspace_id: activeWorkspaceId,
      });
      setExportResult(res);
    } catch (err: any) {
      setError(err?.message || "Failed to export encrypted sync package");
    } finally {
      setIsExporting(false);
    }
  };

  const handleImportPackage = async (e: React.FormEvent) => {
    e.preventDefault();
    const raw = importJson.trim();
    if (!raw) {
      setError("Paste a CryptographicEnvelope JSON payload to import.");
      return;
    }
    setIsImporting(true);
    setError(null);
    try {
      const parsed = JSON.parse(raw);
      const envelope: CryptographicEnvelope = parsed.envelope ? parsed.envelope : parsed;
      const res = await client.importSyncPackage({ envelope });
      setImportResult(res);
      setImportJson("");
      await loadSyncState();
    } catch (err: any) {
      setError(err?.message || "Failed to import encrypted sync package");
    } finally {
      setIsImporting(false);
    }
  };

  const handleResolveConflict = async (conflictId: string, resolvedValue: Record<string, any>) => {
    setResolvingId(conflictId);
    setError(null);
    try {
      await client.resolveSyncConflict(conflictId, resolvedValue);
      setConflicts((prev) => prev.filter((c) => c.conflict_id !== conflictId));
      setCustomEditConflictId(null);
      setCustomJson("");
    } catch (err: any) {
      setError(err?.message || `Failed to resolve conflict ${conflictId}`);
    } finally {
      setResolvingId(null);
    }
  };

  const handleOpenCustomMerge = (conflict: SyncConflict) => {
    setCustomEditConflictId(conflict.conflict_id);
    setCustomJson(JSON.stringify({ ...conflict.local_data, ...conflict.remote_data }, null, 2));
  };

  const handleSubmitCustomMerge = async (conflictId: string) => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(customJson);
    } catch {
      setError("Custom merge JSON is invalid.");
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setError("Custom merge JSON must be a plain object (key-value map), not null, an array, or a primitive.");
      return;
    }
    await handleResolveConflict(conflictId, parsed as Record<string, any>);
  };

  const handleRevokePeer = async (peerId: string) => {
    setRevokingPeerId(peerId);
    setError(null);
    try {
      await client.revokePeer(peerId);
      setPeers((prev) =>
        prev.map((p) => (p.peer_device_id === peerId ? { ...p, trust_status: "revoked" } : p))
      );
    } catch (err: any) {
      setError(err?.message || `Failed to revoke peer ${peerId}`);
    } finally {
      setRevokingPeerId(null);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px", overflowY: "auto" }}>
      {/* Header */}
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
            Cross-Device Sync &amp; Conflict Resolution
          </h2>
          <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>
            X25519-HKDF-AES256GCM encrypted sync packages and manual vector-clock conflict resolution.
          </div>
        </div>

        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <button
            type="button"
            data-testid="pair-mobile-qr-button"
            onClick={() => setIsQrModalOpen(true)}
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
            Pair Mobile Device (QR)
          </button>

          <button
            type="button"
            onClick={loadSyncState}
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
            {isLoading ? "Refreshing..." : "Refresh Sync State"}
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

      {/* Top Grid: Paired Devices + Manual Encrypted Package Trigger */}
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: "16px" }}>
        {/* Paired Devices List */}
        <section
          style={{
            backgroundColor: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "16px",
          }}
        >
          <h3 style={{ margin: "0 0 12px 0", fontSize: "14px", fontWeight: 600, color: "#1e293b" }}>
            Paired Devices ({peers.length})
          </h3>
          {peers.length === 0 ? (
            <div style={{ fontSize: "13px", color: "#64748b", padding: "12px 0" }}>
              No paired peer devices found.
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {peers.map((peer) => {
                const badge = getTrustBadgeStyle(peer.trust_status);
                const isSelected = selectedPeerId === peer.peer_device_id;
                return (
                  <div
                    key={peer.peer_device_id}
                    onClick={() => setSelectedPeerId(peer.peer_device_id)}
                    style={{
                      padding: "12px",
                      borderRadius: "6px",
                      border: `1px solid ${isSelected ? "#2563eb" : "#e2e8f0"}`,
                      backgroundColor: isSelected ? "#eff6ff" : "#f8fafc",
                      cursor: "pointer",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: "12px",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: "13px", color: "#0f172a" }}>
                        {peer.device_name} <span style={{ color: "#64748b", fontWeight: 400 }}>({peer.device_type})</span>
                      </div>
                      <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px", fontFamily: "ui-monospace, monospace" }}>
                        {peer.peer_device_id}
                      </div>
                      <div style={{ fontSize: "11px", color: "#475569", marginTop: "4px" }}>
                        Last synced: {peer.last_synced_at ? peer.last_synced_at : "Never"}
                      </div>
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
                      <span
                        style={{
                          fontSize: "11px",
                          fontWeight: 600,
                          padding: "2px 8px",
                          borderRadius: "9999px",
                          backgroundColor: badge.bg,
                          color: badge.color,
                          border: `1px solid ${badge.border}`,
                        }}
                      >
                        {badge.label}
                      </span>
                      {peer.unresolved_conflicts > 0 && (
                        <span
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "2px 8px",
                            borderRadius: "9999px",
                            backgroundColor: "#fef3c7",
                            color: "#b45309",
                          }}
                        >
                          {peer.unresolved_conflicts} conflict{peer.unresolved_conflicts === 1 ? "" : "s"}
                        </span>
                      )}
                      {peer.trust_status !== "revoked" && (
                        <button
                          type="button"
                          data-testid={`revoke-peer-${peer.peer_device_id}`}
                          disabled={revokingPeerId === peer.peer_device_id}
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRevokePeer(peer.peer_device_id);
                          }}
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "3px 8px",
                            borderRadius: "5px",
                            backgroundColor: "#fef2f2",
                            color: "#b91c1c",
                            border: "1px solid #fecaca",
                            cursor: revokingPeerId === peer.peer_device_id ? "not-allowed" : "pointer",
                          }}
                        >
                          {revokingPeerId === peer.peer_device_id ? "Revoking..." : "Revoke Trust"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        {/* Encrypted Package Manual Sync Trigger */}
        <section
          style={{
            backgroundColor: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: "8px",
            padding: "16px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <h3 style={{ margin: 0, fontSize: "14px", fontWeight: 600, color: "#1e293b" }}>
            Encrypted Package Sync
          </h3>

          <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
            <select
              aria-label="Target Peer Device"
              value={selectedPeerId}
              onChange={(e) => setSelectedPeerId(e.target.value)}
              style={{
                flex: 1,
                padding: "7px 10px",
                fontSize: "12px",
                borderRadius: "6px",
                border: "1px solid #cbd5e1",
                backgroundColor: "#f8fafc",
              }}
            >
              <option value="">-- Select Paired Peer --</option>
              {peers.map((p) => (
                <option key={p.peer_device_id} value={p.peer_device_id}>
                  {p.device_name} ({p.trust_status || "unknown"})
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={handleExportPackage}
              disabled={isExporting || !selectedPeerId}
              style={{
                backgroundColor: isExporting || !selectedPeerId ? "#93c5fd" : "#2563eb",
                color: "#ffffff",
                border: "none",
                borderRadius: "6px",
                padding: "7px 14px",
                fontSize: "12px",
                fontWeight: 600,
                cursor: isExporting || !selectedPeerId ? "not-allowed" : "pointer",
                whiteSpace: "nowrap",
              }}
            >
              {isExporting ? "Sealing..." : "Export Encrypted Package"}
            </button>
          </div>

          {exportResult && (
            <div
              style={{
                backgroundColor: "#f0fdf4",
                border: "1px solid #bbf7d0",
                borderRadius: "6px",
                padding: "10px",
                fontSize: "12px",
                color: "#166534",
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: "4px" }}>
                Sealed Package Ready (Batch: {exportResult.batch_id})
              </div>
              <div>Algorithm: {exportResult.envelope?.algorithm || "—"}</div>
              <div>Recipient: {exportResult.envelope?.recipient_device_id || "—"}</div>
              <div>Items Sealed: {exportResult.items_count ?? 0}</div>
            </div>
          )}

          <form onSubmit={handleImportPackage} style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            <label htmlFor="sync-import-json" style={{ fontSize: "12px", fontWeight: 500, color: "#475569" }}>
              Import Sealed Envelope (JSON)
            </label>
            <textarea
              id="sync-import-json"
              rows={3}
              value={importJson}
              onChange={(e) => setImportJson(e.target.value)}
              placeholder='{"version": 1, "sender_device_id": "...", "ciphertext": "..."}'
              style={{
                width: "100%",
                boxSizing: "border-box",
                fontFamily: "ui-monospace, monospace",
                fontSize: "11px",
                padding: "8px",
                borderRadius: "6px",
                border: "1px solid #cbd5e1",
              }}
            />
            <button
              type="submit"
              disabled={isImporting || !importJson.trim()}
              style={{
                alignSelf: "flex-end",
                backgroundColor: "#0f172a",
                color: "#ffffff",
                border: "none",
                borderRadius: "6px",
                padding: "6px 14px",
                fontSize: "12px",
                fontWeight: 600,
                cursor: isImporting || !importJson.trim() ? "not-allowed" : "pointer",
              }}
            >
              {isImporting ? "Decrypting & Applying..." : "Import Package"}
            </button>
          </form>

          {importResult && (
            <div
              style={{
                backgroundColor: "#eff6ff",
                border: "1px solid #bfdbfe",
                borderRadius: "6px",
                padding: "10px",
                fontSize: "12px",
                color: "#1e3a8a",
              }}
            >
              <strong>Import Complete:</strong> Imported {importResult.items_imported}, Skipped{" "}
              {importResult.items_skipped}, Conflicts Detected {importResult.conflicts_detected}
            </div>
          )}
        </section>
      </div>

      {/* Visual Side-by-Side Diff Viewer for MANUAL Conflicts */}
      <section
        style={{
          backgroundColor: "#ffffff",
          border: "1px solid #e2e8f0",
          borderRadius: "8px",
          padding: "16px",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
          <h3 style={{ margin: 0, fontSize: "14px", fontWeight: 600, color: "#1e293b" }}>
            Manual Conflict Resolution ({conflicts.length} unresolved)
          </h3>
        </div>

        {conflicts.length === 0 ? (
          <div style={{ fontSize: "13px", color: "#64748b", padding: "12px 0" }}>
            All synced records are consistent. No unresolved conflicts.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {conflicts.map((conflict) => {
              const diffRows = computeDataDiff(conflict.local_data, conflict.remote_data);
              const isBusy = resolvingId === conflict.conflict_id;
              const isCustomOpen = customEditConflictId === conflict.conflict_id;

              return (
                <div
                  key={conflict.conflict_id}
                  data-testid={`conflict-card-${conflict.conflict_id}`}
                  style={{
                    border: "1px solid #cbd5e1",
                    borderRadius: "8px",
                    padding: "14px",
                    backgroundColor: "#f8fafc",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: "8px",
                      marginBottom: "12px",
                    }}
                  >
                    <div>
                      <span style={{ fontWeight: 700, fontSize: "13px", color: "#0f172a" }}>
                        {conflict.entity_type}: {conflict.entity_id}
                      </span>
                      <span
                        style={{
                          marginLeft: "8px",
                          fontSize: "11px",
                          backgroundColor: "#fef3c7",
                          color: "#b45309",
                          padding: "2px 8px",
                          borderRadius: "9999px",
                          fontWeight: 600,
                        }}
                      >
                        {conflict.resolution_strategy || "MANUAL"}
                      </span>
                      <div style={{ fontSize: "11px", color: "#64748b", marginTop: "2px" }}>
                        Workspace: {conflict.workspace_id} | Peer: {conflict.peer_device_id} | Conflict ID: {conflict.conflict_id}
                      </div>
                    </div>

                    <div style={{ display: "flex", gap: "8px" }}>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => handleResolveConflict(conflict.conflict_id, conflict.local_data)}
                        style={{
                          backgroundColor: "#2563eb",
                          color: "#ffffff",
                          border: "none",
                          borderRadius: "6px",
                          padding: "6px 12px",
                          fontSize: "12px",
                          fontWeight: 600,
                          cursor: isBusy ? "not-allowed" : "pointer",
                        }}
                      >
                        Keep Local
                      </button>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => handleResolveConflict(conflict.conflict_id, conflict.remote_data)}
                        style={{
                          backgroundColor: "#0d9488",
                          color: "#ffffff",
                          border: "none",
                          borderRadius: "6px",
                          padding: "6px 12px",
                          fontSize: "12px",
                          fontWeight: 600,
                          cursor: isBusy ? "not-allowed" : "pointer",
                        }}
                      >
                        Accept Remote
                      </button>
                      <button
                        type="button"
                        disabled={isBusy}
                        onClick={() => handleOpenCustomMerge(conflict)}
                        style={{
                          backgroundColor: "#ffffff",
                          color: "#334155",
                          border: "1px solid #cbd5e1",
                          borderRadius: "6px",
                          padding: "6px 12px",
                          fontSize: "12px",
                          fontWeight: 500,
                          cursor: "pointer",
                        }}
                      >
                        Custom Merge
                      </button>
                    </div>
                  </div>

                  {/* Side-by-Side Diff Table */}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "140px 1fr 1fr",
                      border: "1px solid #e2e8f0",
                      borderRadius: "6px",
                      overflow: "hidden",
                      backgroundColor: "#ffffff",
                      fontSize: "12px",
                    }}
                  >
                    <div style={{ padding: "8px 10px", backgroundColor: "#f1f5f9", fontWeight: 600, color: "#475569", borderBottom: "1px solid #e2e8f0" }}>
                      Field
                    </div>
                    <div style={{ padding: "8px 10px", backgroundColor: "#eff6ff", fontWeight: 600, color: "#1d4ed8", borderBottom: "1px solid #e2e8f0", borderLeft: "1px solid #e2e8f0" }}>
                      Local Data (Clock: {JSON.stringify(conflict.local_clock)})
                    </div>
                    <div style={{ padding: "8px 10px", backgroundColor: "#f0fdfa", fontWeight: 600, color: "#0f766e", borderBottom: "1px solid #e2e8f0", borderLeft: "1px solid #e2e8f0" }}>
                      Remote Data (Clock: {JSON.stringify(conflict.remote_clock)})
                    </div>

                    {diffRows.map((row) => (
                      <React.Fragment key={row.key}>
                        <div
                          style={{
                            padding: "8px 10px",
                            fontWeight: 600,
                            color: "#334155",
                            borderBottom: "1px solid #f1f5f9",
                            backgroundColor: row.isDifferent ? "#fefce8" : "#ffffff",
                          }}
                        >
                          {row.key}
                        </div>
                        <div
                          style={{
                            padding: "8px 10px",
                            fontFamily: "ui-monospace, monospace",
                            wordBreak: "break-all",
                            borderBottom: "1px solid #f1f5f9",
                            borderLeft: "1px solid #e2e8f0",
                            backgroundColor: row.isDifferent ? "#fef9c3" : "#ffffff",
                            color: "#0f172a",
                          }}
                        >
                          {row.localVal}
                        </div>
                        <div
                          style={{
                            padding: "8px 10px",
                            fontFamily: "ui-monospace, monospace",
                            wordBreak: "break-all",
                            borderBottom: "1px solid #f1f5f9",
                            borderLeft: "1px solid #e2e8f0",
                            backgroundColor: row.isDifferent ? "#fef9c3" : "#ffffff",
                            color: "#0f172a",
                          }}
                        >
                          {row.remoteVal}
                        </div>
                      </React.Fragment>
                    ))}
                  </div>

                  {isCustomOpen && (
                    <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
                      <textarea
                        rows={4}
                        value={customJson}
                        onChange={(e) => setCustomJson(e.target.value)}
                        style={{
                          width: "100%",
                          boxSizing: "border-box",
                          fontFamily: "ui-monospace, monospace",
                          fontSize: "12px",
                          padding: "8px",
                          borderRadius: "6px",
                          border: "1px solid #cbd5e1",
                        }}
                      />
                      <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                        <button
                          type="button"
                          onClick={() => setCustomEditConflictId(null)}
                          style={{
                            backgroundColor: "#ffffff",
                            color: "#475569",
                            border: "1px solid #cbd5e1",
                            borderRadius: "6px",
                            padding: "5px 12px",
                            fontSize: "12px",
                            cursor: "pointer",
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => handleSubmitCustomMerge(conflict.conflict_id)}
                          disabled={isBusy}
                          style={{
                            backgroundColor: "#2563eb",
                            color: "#ffffff",
                            border: "none",
                            borderRadius: "6px",
                            padding: "5px 12px",
                            fontSize: "12px",
                            fontWeight: 600,
                            cursor: "pointer",
                          }}
                        >
                          Submit Custom Resolution
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      <QrPairingModal
        client={client}
        isOpen={isQrModalOpen}
        onClose={() => setIsQrModalOpen(false)}
        onPaired={() => {
          loadSyncState();
        }}
        events={events}
      />
    </div>
  );
};
