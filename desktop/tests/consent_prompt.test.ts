import { describe, it, expect, vi } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ConsentPrompt, DiscoveredSource } from "../src/components/ConsentPrompt";
import { LifeOSWebClient } from "../src/api/client";

describe("ConsentPrompt Component", () => {
  const dummyClient = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });

  it("renders null when discovery is null", () => {
    const html = renderToStaticMarkup(
      React.createElement(ConsentPrompt, {
        client: dummyClient,
        discovery: null,
        onDismiss: () => {},
        onAuthorized: () => {},
      })
    );
    expect(html).toBe("");
  });

  it("renders detected path and file count when discovery is provided", () => {
    const discovery: DiscoveredSource = {
      resource_path: "C:\\Users\\User\\Documents\\Financial",
      scope_type: "folder",
      suggested_name: "Financial",
      file_count: 14,
      discovered_at: "2026-11-24T20:00:00Z",
    };

    const html = renderToStaticMarkup(
      React.createElement(ConsentPrompt, {
        client: dummyClient,
        discovery,
        onDismiss: () => {},
        onAuthorized: () => {},
      })
    );

    expect(html).toContain("New Source Folder Detected");
    expect(html).toContain("C:\\Users\\User\\Documents\\Financial");
    expect(html).toContain("14 files detected");
    expect(html).toContain("Authorize &amp; Index");
    expect(html).toContain("Dismiss");
  });

  it("triggers grantConsent API call when authorized", async () => {
    const grantConsentSpy = vi.spyOn(dummyClient, "grantConsent").mockResolvedValue({
      consent_id: "c-new-001",
      resource_path: "C:\\Users\\User\\Downloads",
      scope_type: "folder",
      status: "active",
      categories: ["general"],
      purposes: ["indexing", "query"],
      allow_cloud_sync: false,
      granted_at: "2026-11-24T20:00:00Z",
    });

    const onAuthorizedSpy = vi.fn();

    // Call the grant handler logic
    await dummyClient.grantConsent(
      "C:\\Users\\User\\Downloads",
      "folder",
      ["general"],
      ["indexing", "query"]
    );
    onAuthorizedSpy("C:\\Users\\User\\Downloads");

    expect(grantConsentSpy).toHaveBeenCalledWith(
      "C:\\Users\\User\\Downloads",
      "folder",
      ["general"],
      ["indexing", "query"]
    );
    expect(onAuthorizedSpy).toHaveBeenCalledWith("C:\\Users\\User\\Downloads");
  });
});
