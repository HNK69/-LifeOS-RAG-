import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LifeOSWebClient } from "../src/api/client";
import { WorkspaceProfile } from "../src/api/types";
import { WorkspacesView, WorkspaceSwitcher, isWorkspaceLocalOnly } from "../src/views/WorkspacesView";

describe("WorkspacesView & WorkspaceSwitcher", () => {
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
      description: "Primary personal knowledge base",
      created_at: "2026-11-24T08:00:00Z",
      updated_at: "2026-11-24T08:00:00Z",
      is_active: true,
      settings: {},
      document_count: 15,
      context_count: 6,
    },
    {
      workspace_id: "ws-vault",
      name: "Medical Vault",
      profile_type: "restricted",
      description: "Strictly local medical records",
      created_at: "2026-11-24T09:00:00Z",
      updated_at: "2026-11-24T09:00:00Z",
      is_active: true,
      settings: { local_only: true },
      document_count: 8,
      context_count: 2,
    },
  ];

  it("renders workspace cards, counts, and Local-Only badge", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const html = renderToStaticMarkup(
      React.createElement(WorkspacesView, {
        client,
        workspaces: sampleWorkspaces,
        activeWorkspaceId: "ws-vault",
        onSelectWorkspace: () => {},
      })
    );

    expect(html).toContain("Workspaces &amp; Profile Isolation");
    expect(html).toContain("Personal");
    expect(html).toContain("Medical Vault");
    expect(html).toContain("Local-Only");
    expect(html).toContain("Documents: <strong>8</strong>");
    expect(html).toContain("Active Workspace");
    expect(html).toContain("Switch to Workspace");
  });

  it("renders WorkspaceSwitcher with Local-Only indicator badge when restricted workspace is active", () => {
    const htmlRestricted = renderToStaticMarkup(
      React.createElement(WorkspaceSwitcher, {
        workspaces: sampleWorkspaces,
        activeWorkspaceId: "ws-vault",
        onSelectWorkspace: () => {},
      })
    );
    expect(htmlRestricted).toContain("Medical Vault (restricted)");
    expect(htmlRestricted).toContain("Local-Only");

    const htmlPersonal = renderToStaticMarkup(
      React.createElement(WorkspaceSwitcher, {
        workspaces: sampleWorkspaces,
        activeWorkspaceId: "default",
        onSelectWorkspace: () => {},
      })
    );
    expect(htmlPersonal).toContain("Personal (personal)");
    expect(htmlPersonal).not.toContain("Local-Only");

    expect(isWorkspaceLocalOnly(sampleWorkspaces[0])).toBe(false);
    expect(isWorkspaceLocalOnly(sampleWorkspaces[1])).toBe(true);
  });

  it("renders Create Workspace dialog when open", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const html = renderToStaticMarkup(
      React.createElement(WorkspacesView, {
        client,
        workspaces: sampleWorkspaces,
        activeWorkspaceId: "default",
        onSelectWorkspace: () => {},
        initialCreateDialogOpen: true,
      })
    );

    expect(html).toContain("Create Workspace Profile");
    expect(html).toContain("Workspace Name");
    expect(html).toContain("Profile Type");
    expect(html).toContain("Enforce strict Local-Only mode");
  });

  it("executes listWorkspaces, createWorkspace, and deleteWorkspace via client", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => sampleWorkspaces })
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          workspace_id: "ws-legal",
          name: "Legal",
          profile_type: "restricted",
          description: "Legal contracts",
          created_at: "2026-11-24T16:00:00Z",
          updated_at: "2026-11-24T16:00:00Z",
          is_active: true,
          settings: { local_only: true },
          document_count: 0,
          context_count: 0,
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ deleted: true, workspace_id: "ws-legal" }),
      });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });

    const list = await client.listWorkspaces();
    expect(list).toHaveLength(2);

    const created = await client.createWorkspace({
      name: "Legal",
      profile_type: "restricted",
      description: "Legal contracts",
      settings: { local_only: true },
    });
    expect(created.workspace_id).toBe("ws-legal");
    expect(isWorkspaceLocalOnly(created)).toBe(true);

    const del = await client.deleteWorkspace("ws-legal");
    expect(del.deleted).toBe(true);
    expect(mockFetch.mock.calls[1][0]).toBe("http://127.0.0.1:8000/api/workspaces");
    expect(mockFetch.mock.calls[2][1].method).toBe("DELETE");
  });
});
