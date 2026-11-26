import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LifeOSWebClient } from "../src/api/client";
import { WorkspaceProfile } from "../src/api/types";
import {
  QueryView,
  extractCitations,
  extractConfidence,
  getConfidenceBadge,
  buildFileLink,
  ConversationTurn,
} from "../src/views/QueryView";

describe("QueryView & Query Intelligence", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const sampleWorkspaces: WorkspaceProfile[] = [
    {
      workspace_id: "default",
      name: "Personal",
      profile_type: "personal",
      description: "Default personal workspace",
      created_at: "2026-11-24T10:00:00Z",
      updated_at: "2026-11-24T10:00:00Z",
      is_active: true,
      settings: {},
      document_count: 12,
      context_count: 4,
    },
    {
      workspace_id: "ws-work",
      name: "Acme Corp",
      profile_type: "work",
      description: "Work documents",
      created_at: "2026-11-24T10:00:00Z",
      updated_at: "2026-11-24T10:00:00Z",
      is_active: true,
      settings: { local_only: false },
      document_count: 42,
      context_count: 9,
    },
  ];

  it("renders empty QueryView with session id and workspace scope dropdown", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const html = renderToStaticMarkup(
      React.createElement(QueryView, {
        client,
        workspaces: sampleWorkspaces,
        activeWorkspaceId: "ws-work",
        initialSessionId: "sess-test-001",
      })
    );

    expect(html).toContain("Query &amp; Intelligence");
    expect(html).toContain("Session: sess-test-001");
    expect(html).toContain("Acme Corp (work)");
    expect(html).toContain("Personal (personal)");
    expect(html).toContain("Ask LifeOS");
  });

  it("renders conversation turns, source citations with file links, and confidence badges", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const turns: ConversationTurn[] = [
      {
        id: "t1",
        role: "user",
        content: "Summarize Q4 budget notes",
        timestamp: "2026-11-24T12:00:00Z",
        sessionId: "sess-1",
      },
      {
        id: "t2",
        role: "assistant",
        content: "Q4 infrastructure spend is capped at $45,000.",
        timestamp: "2026-11-24T12:00:02Z",
        sessionId: "sess-1",
        answerType: "reasoned",
        confidencePercent: 88,
        citations: [
          {
            id: "cit-1",
            sourcePath: "C:\\Users\\demo\\Docs\\Q4_Budget.md",
            score: 88,
            snippet: "Infrastructure allocation approved for $45,000.",
          },
        ],
      },
      {
        id: "t3",
        role: "assistant",
        content: "Tentative vendor estimate.",
        timestamp: "2026-11-24T12:01:00Z",
        sessionId: "sess-1",
        confidencePercent: 62,
      },
      {
        id: "t4",
        role: "assistant",
        content: "Weak match from archive.",
        timestamp: "2026-11-24T12:02:00Z",
        sessionId: "sess-1",
        confidencePercent: 31,
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(QueryView, {
        client,
        workspaces: sampleWorkspaces,
        activeWorkspaceId: "default",
        initialSessionId: "sess-1",
        initialTurns: turns,
      })
    );

    expect(html).toContain("Summarize Q4 budget notes");
    expect(html).toContain("Q4 infrastructure spend is capped at $45,000.");
    expect(html).toContain("Source Citations (1)");
    expect(html).toContain('href="file:///C:/Users/demo/Docs/Q4_Budget.md"');
    expect(html).toContain("88% match");
    expect(html).toContain("Confidence: 88% (High)");
    expect(html).toContain("Confidence: 62% (Medium)");
    expect(html).toContain("Confidence: 31% (Low)");
  });

  it("extracts citations and confidence accurately from backend QueryResponse shape", () => {
    const citations = extractCitations({
      query: "Where is roadmap?",
      answer: "Found in roadmap.md",
      answer_type: "rag",
      session_id: "sess-99",
      request_id: "req-1",
      data: [
        {
          source: "D:\\Notes\\roadmap.md",
          score: 0.84,
          preview: "Milestone C3 covers desktop UX.",
        },
      ],
    });

    expect(citations).toHaveLength(1);
    expect(citations[0].sourcePath).toBe("D:\\Notes\\roadmap.md");
    expect(citations[0].score).toBe(84);
    expect(citations[0].snippet).toBe("Milestone C3 covers desktop UX.");

    const conf = extractConfidence({ answer: "ok", data: [] }, citations);
    expect(conf).toBe(84);
    expect(getConfidenceBadge(84).label).toBe("High");
    expect(getConfidenceBadge(55).label).toBe("Medium");
    expect(getConfidenceBadge(20).label).toBe("Low");
    expect(buildFileLink("D:\\Notes\\roadmap.md")).toBe("file:///D:/Notes/roadmap.md");
  });

  it("submits query with session_id and workspace_id via client.query", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        query: "Find tax docs",
        answer: "Found 2025_taxes.pdf",
        answer_type: "rag",
        session_id: "sess-abc",
        request_id: "req-2",
        data: [{ source: "taxes.pdf", score: 0.91, preview: "Tax filing summary" }],
      }),
    });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000", authToken: "tok" });
    const res = await client.query({
      query: "Find tax docs",
      session_id: "sess-abc",
      workspace_id: "ws-work",
    });

    expect(res.session_id).toBe("sess-abc");
    expect(res.answer).toBe("Found 2025_taxes.pdf");
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/query");
    expect(JSON.parse(init.body)).toEqual({
      query: "Find tax docs",
      session_id: "sess-abc",
      workspace_id: "ws-work",
    });
  });
});
