import React, { useEffect, useRef, useState } from "react";
import { LifeOSWebClient } from "../api/client";
import { QueryCitation, QueryResponse, WorkspaceProfile } from "../api/types";

export interface NormalizedCitation {
  id: string;
  sourcePath: string;
  score: number | null;
  snippet: string;
}

export interface ConversationTurn {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  workspaceId?: string;
  sessionId?: string;
  answerType?: string;
  citations?: NormalizedCitation[];
  confidencePercent?: number | null;
  error?: boolean;
}

export interface ConfidenceBadgeStyle {
  label: "High" | "Medium" | "Low";
  bg: string;
  color: string;
  border: string;
}

export function normalizeScorePercent(rawScore: number | undefined | null): number | null {
  if (typeof rawScore !== "number" || !Number.isFinite(rawScore)) {
    return null;
  }
  if (rawScore < 0 || rawScore > 100) {
    return null;
  }
  const pct = rawScore <= 1 ? rawScore * 100 : rawScore;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

export function getConfidenceBadge(confidencePercent: number): ConfidenceBadgeStyle {
  if (confidencePercent >= 80) {
    return { label: "High", bg: "#dcfce7", color: "#15803d", border: "#86efac" };
  }
  if (confidencePercent >= 50) {
    return { label: "Medium", bg: "#fef3c7", color: "#b45309", border: "#fcd34d" };
  }
  return { label: "Low", bg: "#fee2e2", color: "#b91c1c", border: "#fca5a5" };
}

export function extractCitations(res: QueryResponse): NormalizedCitation[] {
  if (!res || typeof res !== "object") {
    return [];
  }
  const results: NormalizedCitation[] = [];

  const rawSources = Array.isArray(res.sources)
    ? res.sources
    : Array.isArray(res.data)
    ? res.data
    : [];

  if (Array.isArray(res.sources) && res.sources.length > 0) {
    res.sources.forEach((src, idx) => {
      if (!src || typeof src !== "object") {
        return;
      }
      const sourcePath = src.source_path
        ? String(src.source_path).trim()
        : src.document_id
        ? String(src.document_id).trim()
        : "Unknown source";
      results.push({
        id: src.document_id ? String(src.document_id) : `src-${idx}`,
        sourcePath: sourcePath || "Unknown source",
        score: normalizeScorePercent(src.score),
        snippet: typeof src.snippet === "string" ? src.snippet : src.snippet != null ? String(src.snippet) : "",
      });
    });
    return results;
  }

  rawSources.forEach((item, idx) => {
    if (item === null || item === undefined) {
      return;
    }
    if (typeof item === "string") {
      const trimmed = item.trim();
      results.push({
        id: `data-${idx}`,
        sourcePath: trimmed || `Source #${idx + 1}`,
        score: null,
        snippet: "",
      });
    } else if (typeof item === "object") {
      const cit = item as QueryCitation;
      const rawPath = cit.source || cit.source_path || cit.filename || cit.document_id;
      const sourcePath = rawPath ? String(rawPath).trim() : `Source #${idx + 1}`;
      results.push({
        id: cit.document_id ? String(cit.document_id) : `data-${idx}`,
        sourcePath: sourcePath || `Source #${idx + 1}`,
        score: normalizeScorePercent(cit.score),
        snippet: cit.preview || cit.snippet || "",
      });
    }
  });

  return results;
}

export function extractConfidence(res: QueryResponse, citations: NormalizedCitation[]): number | null {
  if (res && typeof res === "object") {
    const topConfidence = normalizeScorePercent(res.confidence);
    if (topConfidence !== null) {
      return topConfidence;
    }
    if (res.data && !Array.isArray(res.data) && typeof res.data === "object") {
      const dataConfidence = normalizeScorePercent(res.data.confidence);
      if (dataConfidence !== null) {
        return dataConfidence;
      }
    }
  }
  const scores = citations.map((c) => c.score).filter((s): s is number => s !== null);
  if (scores.length > 0) {
    return Math.max(...scores);
  }
  return null;
}

export function buildFileLink(sourcePath: string): string {
  const trimmed = typeof sourcePath === "string" ? sourcePath.trim() : String(sourcePath || "").trim();
  if (!trimmed) {
    return "#";
  }
  if (/^(https?:\/\/|file:\/\/)/i.test(trimmed)) {
    return trimmed;
  }
  const normalized = trimmed.replace(/\\/g, "/");
  return normalized.startsWith("/") ? `file://${normalized}` : `file:///${normalized}`;
}

export function generateSessionId(): string {
  return `sess-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export interface QueryViewProps {
  client: LifeOSWebClient;
  workspaces?: WorkspaceProfile[];
  activeWorkspaceId?: string;
  onWorkspaceChange?: (workspaceId: string) => void;
  initialSessionId?: string;
  initialTurns?: ConversationTurn[];
  initialQueryText?: string;
}

export const QueryView: React.FC<QueryViewProps> = ({
  client,
  workspaces = [],
  activeWorkspaceId = "default",
  onWorkspaceChange,
  initialSessionId,
  initialTurns = [],
  initialQueryText = "",
}) => {
  const [queryText, setQueryText] = useState(initialQueryText);
  const [sessionId, setSessionId] = useState(() => initialSessionId || generateSessionId());
  const currentSessionIdRef = useRef<string>(sessionId);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string>(activeWorkspaceId || "default");
  const [turns, setTurns] = useState<ConversationTurn[]>(initialTurns);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    currentSessionIdRef.current = sessionId;
  }, [sessionId]);

  useEffect(() => {
    if (activeWorkspaceId) {
      setSelectedWorkspaceId(activeWorkspaceId);
    }
  }, [activeWorkspaceId]);

  const handleWorkspaceSelect = (wsId: string) => {
    setSelectedWorkspaceId(wsId);
    if (onWorkspaceChange) {
      onWorkspaceChange(wsId);
    }
  };

  const handleNewSession = () => {
    const nextSessionId = generateSessionId();
    currentSessionIdRef.current = nextSessionId;
    setSessionId(nextSessionId);
    setTurns([]);
    setError(null);
    setIsLoading(false);
  };

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) {
      e.preventDefault();
    }
    const trimmed = queryText.trim();
    if (!trimmed || isLoading) {
      return;
    }
    if (trimmed.length > 4096) {
      setError("Query exceeds maximum length of 4096 characters.");
      return;
    }

    const initiatingSessionId = currentSessionIdRef.current;
    setError(null);
    const now = new Date().toISOString();
    const userTurn: ConversationTurn = {
      id: `u-${Date.now()}`,
      role: "user",
      content: trimmed,
      timestamp: now,
      workspaceId: selectedWorkspaceId,
      sessionId: initiatingSessionId,
    };

    setTurns((prev) => [...prev, userTurn]);
    setQueryText("");
    setIsLoading(true);

    try {
      const response = await client.query({
        query: trimmed,
        session_id: initiatingSessionId,
        workspace_id: selectedWorkspaceId || undefined,
      });

      if (currentSessionIdRef.current !== initiatingSessionId) {
        return;
      }

      const resolvedSessionId = response.session_id || initiatingSessionId;
      if (response.session_id && response.session_id !== initiatingSessionId) {
        currentSessionIdRef.current = response.session_id;
        setSessionId(response.session_id);
      }

      const citations = extractCitations(response);
      const confidencePercent = extractConfidence(response, citations);
      const safeContent =
        typeof response.answer === "string"
          ? response.answer
          : response.answer == null
          ? ""
          : JSON.stringify(response.answer);

      const assistantTurn: ConversationTurn = {
        id: response.request_id || `a-${Date.now()}`,
        role: "assistant",
        content: safeContent,
        timestamp: new Date().toISOString(),
        workspaceId: selectedWorkspaceId,
        sessionId: resolvedSessionId,
        answerType: response.answer_type,
        citations,
        confidencePercent,
      };
      setTurns((prev) => [...prev, assistantTurn]);
    } catch (err: any) {
      if (currentSessionIdRef.current !== initiatingSessionId) {
        return;
      }
      const msg = err?.message || "Failed to execute query";
      setError(msg);
      setTurns((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: "assistant",
          content: `Query failed: ${msg}`,
          timestamp: new Date().toISOString(),
          sessionId: initiatingSessionId,
          error: true,
        },
      ]);
    } finally {
      if (currentSessionIdRef.current === initiatingSessionId) {
        setIsLoading(false);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  };

  const workspaceOptions =
    workspaces.length > 0
      ? workspaces
      : [
          {
            workspace_id: selectedWorkspaceId || "default",
            name: selectedWorkspaceId === "default" || !selectedWorkspaceId ? "Default Workspace" : selectedWorkspaceId,
            profile_type: "personal",
            description: null,
            created_at: "",
            updated_at: "",
            is_active: true,
            settings: {},
            document_count: 0,
            context_count: 0,
          },
        ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: "16px", overflow: "hidden" }}>
      {/* Top Toolbar: Session & Workspace Filtering */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          backgroundColor: "#ffffff",
          padding: "12px 16px",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          flexWrap: "wrap",
          gap: "12px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <h2 style={{ margin: 0, fontSize: "16px", fontWeight: 600, color: "#1e293b" }}>
            Query &amp; Intelligence
          </h2>
          <span
            style={{
              fontSize: "11px",
              color: "#475569",
              backgroundColor: "#f1f5f9",
              padding: "3px 8px",
              borderRadius: "4px",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            }}
          >
            Session: {sessionId}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          <label htmlFor="query-workspace-select" style={{ fontSize: "12px", color: "#475569", fontWeight: 500 }}>
            Workspace Scope:
          </label>
          <select
            id="query-workspace-select"
            aria-label="Query Workspace Scope"
            value={selectedWorkspaceId}
            onChange={(e) => handleWorkspaceSelect(e.target.value)}
            style={{
              padding: "6px 10px",
              fontSize: "12px",
              borderRadius: "6px",
              border: "1px solid #cbd5e1",
              backgroundColor: "#f8fafc",
              color: "#1e293b",
              fontWeight: 500,
            }}
          >
            {workspaceOptions.map((ws) => (
              <option key={ws.workspace_id} value={ws.workspace_id}>
                {ws.name} ({ws.profile_type})
              </option>
            ))}
          </select>

          <button
            type="button"
            onClick={handleNewSession}
            style={{
              backgroundColor: "#f1f5f9",
              color: "#334155",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              padding: "6px 12px",
              fontSize: "12px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            New Session
          </button>
        </div>
      </div>

      {/* Conversation Memory Stream */}
      <div
        style={{
          flex: 1,
          backgroundColor: "#ffffff",
          borderRadius: "8px",
          border: "1px solid #e2e8f0",
          padding: "16px",
          overflowY: "auto",
          display: "flex",
          flexDirection: "column",
          gap: "16px",
        }}
      >
        {turns.length === 0 ? (
          <div style={{ textAlign: "center", color: "#64748b", marginTop: "48px" }}>
            <div style={{ fontSize: "15px", fontWeight: 600, color: "#334155", marginBottom: "6px" }}>
              Ask LifeOS anything across your local knowledge base
            </div>
            <div style={{ fontSize: "13px" }}>
              Queries execute locally against indexed documents, people, and memory inside workspace{" "}
              <strong style={{ color: "#1e293b" }}>{selectedWorkspaceId}</strong>.
            </div>
          </div>
        ) : (
          turns.map((turn) => {
            const isUser = turn.role === "user";
            const badge =
              turn.confidencePercent !== null && turn.confidencePercent !== undefined
                ? getConfidenceBadge(turn.confidencePercent)
                : null;

            return (
              <div
                key={turn.id}
                style={{
                  alignSelf: isUser ? "flex-end" : "stretch",
                  maxWidth: isUser ? "80%" : "100%",
                  backgroundColor: isUser ? "#eff6ff" : turn.error ? "#fef2f2" : "#f8fafc",
                  border: `1px solid ${isUser ? "#bfdbfe" : turn.error ? "#fecaca" : "#e2e8f0"}`,
                  borderRadius: "8px",
                  padding: "12px 16px",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "8px",
                    gap: "8px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        textTransform: "uppercase",
                        color: isUser ? "#1d4ed8" : turn.error ? "#b91c1c" : "#334155",
                      }}
                    >
                      {isUser ? "You" : "LifeOS"}
                    </span>
                    {turn.answerType && (
                      <span
                        style={{
                          fontSize: "11px",
                          backgroundColor: "#e2e8f0",
                          color: "#475569",
                          padding: "1px 6px",
                          borderRadius: "4px",
                        }}
                      >
                        {turn.answerType}
                      </span>
                    )}
                    {badge && turn.confidencePercent !== null && turn.confidencePercent !== undefined && (
                      <span
                        data-testid="confidence-badge"
                        style={{
                          fontSize: "11px",
                          fontWeight: 600,
                          backgroundColor: badge.bg,
                          color: badge.color,
                          border: `1px solid ${badge.border}`,
                          padding: "1px 8px",
                          borderRadius: "9999px",
                        }}
                      >
                        Confidence: {turn.confidencePercent}% ({badge.label})
                      </span>
                    )}
                  </div>
                  <span style={{ fontSize: "11px", color: "#94a3b8" }}>{turn.timestamp}</span>
                </div>

                <div
                  style={{
                    fontSize: "14px",
                    lineHeight: 1.55,
                    color: turn.error ? "#991b1b" : "#0f172a",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {typeof turn.content === "string"
                    ? turn.content
                    : turn.content == null
                    ? ""
                    : JSON.stringify(turn.content)}
                </div>

                {/* Source Citations */}
                {turn.citations && turn.citations.length > 0 && (
                  <div style={{ marginTop: "12px", borderTop: "1px solid #e2e8f0", paddingTop: "10px" }}>
                    <div style={{ fontSize: "12px", fontWeight: 600, color: "#475569", marginBottom: "8px" }}>
                      Source Citations ({turn.citations.length})
                    </div>
                    <div
                      style={{
                        display: "grid",
                        gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                        gap: "8px",
                      }}
                    >
                      {turn.citations.map((cit) => (
                        <div
                          key={cit.id}
                          style={{
                            backgroundColor: "#ffffff",
                            border: "1px solid #cbd5e1",
                            borderRadius: "6px",
                            padding: "10px",
                            fontSize: "12px",
                          }}
                        >
                          <div
                            style={{
                              display: "flex",
                              justifyContent: "space-between",
                              alignItems: "center",
                              gap: "6px",
                              marginBottom: "4px",
                            }}
                          >
                            <a
                              href={buildFileLink(cit.sourcePath)}
                              title={cit.sourcePath}
                              style={{
                                color: "#2563eb",
                                fontWeight: 600,
                                textDecoration: "none",
                                wordBreak: "break-all",
                              }}
                            >
                              {cit.sourcePath}
                            </a>
                            {cit.score !== null && (
                              <span
                                style={{
                                  flexShrink: 0,
                                  fontSize: "11px",
                                  backgroundColor: "#f1f5f9",
                                  color: "#334155",
                                  padding: "1px 6px",
                                  borderRadius: "4px",
                                  fontWeight: 600,
                                }}
                              >
                                {cit.score}% match
                              </span>
                            )}
                          </div>
                          {cit.snippet && (
                            <div
                              style={{
                                color: "#475569",
                                fontSize: "12px",
                                lineHeight: 1.4,
                                backgroundColor: "#f8fafc",
                                padding: "6px 8px",
                                borderRadius: "4px",
                                border: "1px solid #f1f5f9",
                              }}
                            >
                              {cit.snippet}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Error Alert */}
      {error && (
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
          {error}
        </div>
      )}

      {/* Natural Language Query Bar */}
      <form
        onSubmit={handleSubmit}
        style={{
          backgroundColor: "#ffffff",
          border: "1px solid #cbd5e1",
          borderRadius: "8px",
          padding: "12px",
          display: "flex",
          gap: "12px",
          alignItems: "flex-end",
        }}
      >
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "4px" }}>
            <label htmlFor="nl-query-input" style={{ display: "block", fontSize: "11px", color: "#64748b" }}>
              Natural Language Query (Ctrl+Enter to send)
            </label>
            <span
              data-testid="query-char-count"
              style={{
                fontSize: "11px",
                color: queryText.length >= 4096 ? "#b91c1c" : "#64748b",
              }}
            >
              {queryText.length} / 4096
            </span>
          </div>
          <textarea
            id="nl-query-input"
            value={queryText}
            onChange={(e) => setQueryText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about your documents, meetings, contacts, or context..."
            rows={2}
            maxLength={4096}
            disabled={isLoading}
            style={{
              width: "100%",
              boxSizing: "border-box",
              border: "1px solid #cbd5e1",
              borderRadius: "6px",
              padding: "8px 10px",
              fontSize: "13px",
              fontFamily: "inherit",
              resize: "vertical",
              color: "#0f172a",
            }}
          />
        </div>
        <button
          type="submit"
          disabled={isLoading || !queryText.trim()}
          style={{
            backgroundColor: isLoading || !queryText.trim() ? "#93c5fd" : "#2563eb",
            color: "#ffffff",
            border: "none",
            borderRadius: "6px",
            padding: "10px 18px",
            fontSize: "13px",
            fontWeight: 600,
            cursor: isLoading || !queryText.trim() ? "not-allowed" : "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {isLoading ? "Searching..." : "Ask LifeOS"}
        </button>
      </form>
    </div>
  );
};
