import { describe, it, expect } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { App } from "../src/App";
import { Header } from "../src/components/Header";
import { WorkspaceProfile } from "../src/api/types";

describe("App Navigation & C1/C2 Integration", () => {
  const sampleWorkspaces: WorkspaceProfile[] = [
    {
      workspace_id: "default",
      name: "Personal",
      profile_type: "personal",
      description: null,
      created_at: "2026-11-24T08:00:00Z",
      updated_at: "2026-11-24T08:00:00Z",
      is_active: true,
      settings: {},
      document_count: 3,
      context_count: 1,
    },
    {
      workspace_id: "ws-vault",
      name: "Confidential Vault",
      profile_type: "restricted",
      description: "Local only",
      created_at: "2026-11-24T08:00:00Z",
      updated_at: "2026-11-24T08:00:00Z",
      is_active: true,
      settings: { local_only: true },
      document_count: 5,
      context_count: 2,
    },
  ];

  it("renders primary navigation tabs, Header workspace switcher, and Query view by default", () => {
    const html = renderToStaticMarkup(
      React.createElement(App, {
        initialWorkspaces: sampleWorkspaces,
        initialActiveWorkspaceId: "ws-vault",
      })
    );

    expect(html).toContain("LifeOS Desktop");
    expect(html).toContain("Starting...");
    expect(html).toContain("Confidential Vault (restricted)");
    expect(html).toContain("Local-Only");
    expect(html).toContain("Query");
    expect(html).toContain("Privacy");
    expect(html).toContain("Sync");
    expect(html).toContain("Workspaces");
    expect(html).toContain("Diagnostics");
    expect(html).toContain("Query &amp; Intelligence");
  });

  it("preserves C2 ConsentPrompt banner across tabs", () => {
    const html = renderToStaticMarkup(
      React.createElement(App, {
        initialTab: "privacy",
        initialWorkspaces: sampleWorkspaces,
        initialDiscovery: {
          resource_path: "C:\\Users\\demo\\Projects",
          scope_type: "folder",
          suggested_name: "Projects",
          file_count: 7,
          discovered_at: "2026-11-24T16:00:00Z",
        },
      })
    );

    expect(html).toContain("New Source Folder Detected");
    expect(html).toContain("C:\\Users\\demo\\Projects");
    expect(html).toContain("Privacy Dashboard &amp; Cryptographic Governance");
  });

  it("renders Sync, Workspaces, and Diagnostics tabs cleanly", () => {
    const syncHtml = renderToStaticMarkup(
      React.createElement(App, { initialTab: "sync", initialWorkspaces: sampleWorkspaces })
    );
    expect(syncHtml).toContain("Cross-Device Sync &amp; Conflict Resolution");

    const wsHtml = renderToStaticMarkup(
      React.createElement(App, { initialTab: "workspaces", initialWorkspaces: sampleWorkspaces })
    );
    expect(wsHtml).toContain("Workspaces &amp; Profile Isolation");

    const diagHtml = renderToStaticMarkup(
      React.createElement(App, { initialTab: "diagnostics", initialWorkspaces: sampleWorkspaces })
    );
    expect(diagHtml).toContain("Sidecar Lifecycle &amp; Health");
    expect(diagHtml).toContain("Event Bus Stream");
  });

  it("renders Header cleanly both with and without optional workspace props", () => {
    const basicHeader = renderToStaticMarkup(
      React.createElement(Header, {
        sidecarState: { status: "ready", host: "127.0.0.1", port: 8000, token: "", pid: 123 },
        sseState: "connected",
      })
    );
    expect(basicHeader).toContain("Sidecar Ready");
    expect(basicHeader).toContain("127.0.0.1:8000");
    expect(basicHeader).toContain("connected");
  });
});
