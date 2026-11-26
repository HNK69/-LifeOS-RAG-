import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React, { act } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Root } from "react-dom/client";
import { LifeOSWebClient } from "../src/api/client";
import {
  DiagnosticReport,
  GovernorDiagnostics,
  SidecarState,
  UpdateInfo,
} from "../src/api/types";
import { DiagnosticsView } from "../src/views/DiagnosticsView";
import { hydrateSidecarFromTauri, isTauriEnvironment } from "../src/hooks/useLifeOS";

describe("DiagnosticsView & Tauri Sidecar Hydration (Milestone C4)", () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = (globalThis as any).window;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    if (originalWindow === undefined) {
      delete (globalThis as any).window;
    } else {
      (globalThis as any).window = originalWindow;
    }
  });

  const sampleSidecar: SidecarState = {
    status: "ready",
    host: "127.0.0.1",
    port: 54321,
    token: "ephemeral-session-token",
    pid: 4242,
  };

  const sampleHealthyReport: DiagnosticReport = {
    overall_status: "HEALTHY",
    total_checks: 3,
    passed: 3,
    warnings: 0,
    failed: 0,
    timestamp: "2026-11-24T17:00:00Z",
    checks: [
      {
        name: "document_registry.db",
        category: "Database",
        status: "OK",
        message: "Integrity check passed (WAL mode active)",
        fix_hint: null,
      },
      {
        name: "device_identity",
        category: "Security",
        status: "OK",
        message: "Device 'Workstation' registered",
        fix_hint: null,
      },
      {
        name: "port_54321",
        category: "Network",
        status: "OK",
        message: "API port actively serving traffic",
        fix_hint: null,
      },
    ],
  };

  const sampleGovernor: GovernorDiagnostics = {
    cpu_percent: 24.5,
    ram_percent: 58.2,
    on_battery: false,
    battery_percent: 100,
    should_pause_ingestion: false,
    pause_reason: null,
  };

  it("renders sidecar connection info, grouped doctor checks, governor card, and updater card", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:54321", authToken: "ephemeral-session-token" });
    const html = renderToStaticMarkup(
      React.createElement(DiagnosticsView, {
        client,
        sidecarState: sampleSidecar,
        health: { status: "healthy", service: "lifeos" },
        readiness: { status: "ready", databases: {}, storage: {}, identity: true },
        connectionError: null,
        onRefreshSidecar: () => {},
        events: [],
        onClearEvents: () => {},
        initialReport: sampleHealthyReport,
        initialGovernor: sampleGovernor,
      })
    );

    expect(html).toContain("Sidecar Lifecycle &amp; Health");
    expect(html).toContain("127.0.0.1:54321");
    expect(html).toContain("Configured (Bearer)");
    expect(html).toContain("PID 4242");
    expect(html).toContain("HEALTHY");
    expect(html).toContain("Run Pre-Flight Diagnostics");
    expect(html).toContain("Strict Mode");
    expect(html).toContain("document_registry.db");
    expect(html).toContain("API port actively serving traffic");
    expect(html).toContain("Resource Governor Telemetry");
    expect(html).toContain("24.5%");
    expect(html).toContain("58.2%");
    expect(html).toContain("AC Power");
    expect(html).toContain("Active");
    expect(html).toContain("Auto-Update &amp; Packaging");
    expect(html).toContain("v1.0.0");
    expect(html).toContain("Not Checked");
    expect(html).not.toContain("Verified (Ed25519)");
    expect(html).toContain("Event Bus Stream");
  });

  it("renders FAILED diagnostic checks with remediation fix hints and throttled battery state", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const failedReport: DiagnosticReport = {
      overall_status: "FAILED",
      total_checks: 2,
      passed: 1,
      warnings: 0,
      failed: 1,
      timestamp: "2026-11-24T17:05:00Z",
      checks: [
        {
          name: "sync.db",
          category: "Database",
          status: "FAIL",
          message: "Corrupt database: file is not a database",
          fix_hint: "Restore from recent backup using 'lifeos backup restore'.",
        },
        {
          name: "configuration_hygiene",
          category: "Config",
          status: "OK",
          message: "Configuration passes all fail-fast validation rules",
          fix_hint: null,
        },
      ],
    };

    const batteryGovernor: GovernorDiagnostics = {
      cpu_percent: 12.0,
      ram_percent: 45.0,
      on_battery: true,
      battery_percent: 62,
      should_pause_ingestion: true,
      pause_reason: "on_battery",
    };

    const updateAvailable: UpdateInfo = {
      current_version: "1.0.0",
      latest_version: "1.1.0",
      update_available: true,
      status: "update_available",
      signature_verified: true,
    };

    const html = renderToStaticMarkup(
      React.createElement(DiagnosticsView, {
        client,
        sidecarState: sampleSidecar,
        health: { status: "healthy", service: "lifeos" },
        readiness: { status: "ready", databases: {}, storage: {}, identity: true },
        connectionError: "Sidecar connection degraded",
        onRefreshSidecar: () => {},
        events: [],
        onClearEvents: () => {},
        initialReport: failedReport,
        initialGovernor: batteryGovernor,
        initialUpdateInfo: updateAvailable,
        initialStrictMode: true,
      })
    );

    expect(html).toContain("FAILED");
    expect(html).toContain("Fix Hint: Restore from recent backup");
    expect(html).toContain("Battery (62%)");
    expect(html).toContain("Paused (on_battery)");
    expect(html).toContain("Update Available (v1.1.0)");
    expect(html).toContain("Verified (Ed25519)");
    expect(html).toContain("Sidecar connection degraded");
    expect(html).toContain('checked=""');
  });

  it("handles null check entries and null/undefined governor numeric fields gracefully", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const reportWithNullCheck: DiagnosticReport = {
      overall_status: "HEALTHY",
      total_checks: 1,
      passed: 1,
      warnings: 0,
      failed: 0,
      timestamp: "2026-11-24T17:10:00Z",
      checks: [
        null as any,
        {
          name: "document_registry.db",
          category: "Database",
          status: "OK",
          message: "Integrity check passed (WAL mode active)",
          fix_hint: null,
        },
      ],
    };

    const partialGovernor: GovernorDiagnostics = {
      cpu_percent: null as any,
      ram_percent: undefined as any,
      on_battery: true,
      battery_percent: undefined as any,
      should_pause_ingestion: true,
      pause_reason: "on_battery",
    };

    const html = renderToStaticMarkup(
      React.createElement(DiagnosticsView, {
        client,
        sidecarState: sampleSidecar,
        health: { status: "healthy", service: "lifeos" },
        readiness: { status: "ready", databases: {}, storage: {}, identity: true },
        connectionError: null,
        onRefreshSidecar: () => {},
        events: [],
        onClearEvents: () => {},
        initialReport: reportWithNullCheck,
        initialGovernor: partialGovernor,
      })
    );

    expect(html).toContain("document_registry.db");
    expect(html).toContain("N/A");
    expect(html).toContain("Battery");
    expect(html).not.toContain("undefined%");
  });

  it("invokes getDiagnostics and getGovernorDiagnostics via LifeOSWebClient", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => sampleHealthyReport,
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => sampleGovernor,
      });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({
      baseUrl: "http://127.0.0.1:54321",
      authToken: "secret-c4-tok",
    });

    const report = await client.getDiagnostics(true);
    expect(report.overall_status).toBe("HEALTHY");
    expect(report.passed).toBe(3);

    const gov = await client.getGovernorDiagnostics();
    expect(gov.cpu_percent).toBe(24.5);
    expect(gov.should_pause_ingestion).toBe(false);

    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch.mock.calls[0][0]).toBe("http://127.0.0.1:54321/api/diagnostics?strict=true");
    expect(mockFetch.mock.calls[0][1].headers.Authorization).toBe("Bearer secret-c4-tok");
    expect(mockFetch.mock.calls[1][0]).toBe("http://127.0.0.1:54321/api/diagnostics/governor");
  });

  it("hydrates dynamic port and token from Tauri internals when present and returns null otherwise", async () => {
    // Non-Tauri browser environment
    delete (globalThis as any).window;
    expect(isTauriEnvironment()).toBe(false);
    expect(await hydrateSidecarFromTauri()).toBeNull();

    // Tauri desktop environment with __TAURI_INTERNALS__
    const invokeMock = vi.fn().mockResolvedValue({
      status: "ready",
      host: "127.0.0.1",
      port: 61234,
      token: "tauri-ephemeral-token",
      pid: 9876,
    });
    (globalThis as any).window = {
      __TAURI_INTERNALS__: {
        invoke: invokeMock,
      },
    };

    expect(isTauriEnvironment()).toBe(true);
    const hydrated = await hydrateSidecarFromTauri();
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(invokeMock.mock.calls[0][0]).toBe("get_sidecar_status");
    expect(hydrated).toEqual({
      status: "ready",
      host: "127.0.0.1",
      port: 61234,
      token: "tauri-ephemeral-token",
      pid: 9876,
      error: undefined,
    });
  });

  it("toggles strict mode with single fetch, clears error on governor recovery, and parses Tauri v2 update metadata", async () => {
    class MiniNode {
      nodeType: number;
      parentNode: MiniNode | null = null;
      childNodes: MiniNode[] = [];
      ownerDocument: MiniDocument;
      listeners: Record<string, Array<(ev: any) => void>> = {};
      constructor(nodeType: number, ownerDocument: MiniDocument) {
        this.nodeType = nodeType;
        this.ownerDocument = ownerDocument;
      }
      addEventListener(type: string, fn: (ev: any) => void) {
        if (!this.listeners[type]) this.listeners[type] = [];
        this.listeners[type].push(fn);
      }
      removeEventListener(type: string, fn: (ev: any) => void) {
        if (!this.listeners[type]) return;
        this.listeners[type] = this.listeners[type].filter((f) => f !== fn);
      }
      dispatchEvent(ev: any): boolean {
        if (!ev.target) ev.target = this;
        ev.currentTarget = this;
        if (!ev.preventDefault) {
          ev.defaultPrevented = false;
          ev.preventDefault = () => {
            ev.defaultPrevented = true;
          };
        }
        if (!ev.stopPropagation) ev.stopPropagation = () => {};
        for (const fn of [...(this.listeners[ev.type] || [])]) fn(ev);
        if (ev.bubbles !== false && this.parentNode) this.parentNode.dispatchEvent(ev);
        return !ev.defaultPrevented;
      }
      get firstChild(): MiniNode | null {
        return this.childNodes[0] || null;
      }
      get nextSibling(): MiniNode | null {
        if (!this.parentNode) return null;
        const idx = this.parentNode.childNodes.indexOf(this);
        return idx >= 0 && idx + 1 < this.parentNode.childNodes.length
          ? this.parentNode.childNodes[idx + 1]
          : null;
      }
      appendChild(child: MiniNode): MiniNode {
        if (child.parentNode) child.parentNode.removeChild(child);
        child.parentNode = this;
        this.childNodes.push(child);
        return child;
      }
      insertBefore(newNode: MiniNode, refNode: MiniNode | null): MiniNode {
        if (!refNode) return this.appendChild(newNode);
        if (newNode.parentNode) newNode.parentNode.removeChild(newNode);
        const idx = this.childNodes.indexOf(refNode);
        if (idx < 0) return this.appendChild(newNode);
        newNode.parentNode = this;
        this.childNodes.splice(idx, 0, newNode);
        return newNode;
      }
      removeChild(child: MiniNode): MiniNode {
        const idx = this.childNodes.indexOf(child);
        if (idx >= 0) {
          this.childNodes.splice(idx, 1);
          child.parentNode = null;
        }
        return child;
      }
      get textContent(): string {
        return this.childNodes.map((c) => c.textContent).join("");
      }
      set textContent(val: string) {
        this.childNodes.forEach((c) => (c.parentNode = null));
        this.childNodes = [];
        if (val !== "" && val !== null && val !== undefined) {
          this.appendChild(this.ownerDocument.createTextNode(String(val)));
        }
      }
    }
    class MiniTextNode extends MiniNode {
      nodeValue: string;
      constructor(text: string, ownerDocument: MiniDocument) {
        super(3, ownerDocument);
        this.nodeValue = text;
      }
      get data() {
        return this.nodeValue;
      }
      set data(v: string) {
        this.nodeValue = v;
      }
      get textContent() {
        return this.nodeValue;
      }
      set textContent(v: string) {
        this.nodeValue = v;
      }
    }
    class MiniCommentNode extends MiniNode {
      data: string;
      constructor(data: string, ownerDocument: MiniDocument) {
        super(8, ownerDocument);
        this.data = data;
      }
      get textContent() {
        return "";
      }
    }
    class MiniElement extends MiniNode {
      tagName: string;
      namespaceURI = "http://www.w3.org/1999/xhtml";
      attributes: Record<string, string> = {};
      style: Record<string, any> = {};
      disabled = false;
      checked = false;
      private _value = "";
      constructor(tagName: string, ownerDocument: MiniDocument) {
        super(1, ownerDocument);
        this.tagName = tagName.toUpperCase();
      }
      get value() {
        return this._value;
      }
      set value(v: string) {
        this._value = v == null ? "" : String(v);
      }
      get type(): string {
        return this.attributes["type"] || "";
      }
      set type(v: string) {
        this.setAttribute("type", v);
      }
      get nodeName() {
        return this.tagName;
      }
      setAttribute(name: string, val: string) {
        const str = String(val);
        this.attributes[name] = str;
        if (name === "disabled") this.disabled = str !== "false";
        if (name === "checked") this.checked = str !== "false";
        if (name === "value") this._value = str;
      }
      removeAttribute(name: string) {
        delete this.attributes[name];
        if (name === "disabled") this.disabled = false;
        if (name === "checked") this.checked = false;
      }
      getAttribute(name: string) {
        return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
      }
      click() {
        if (!this.disabled) {
          if (this.tagName === "INPUT" && this.attributes["type"] === "checkbox") {
            this.checked = !this.checked;
            this.dispatchEvent({ type: "click", bubbles: true });
            this.dispatchEvent({ type: "change", bubbles: true });
            return;
          }
          this.dispatchEvent({ type: "click", bubbles: true });
        }
      }
      querySelectorAll(predicate: (el: MiniElement) => boolean): MiniElement[] {
        const out: MiniElement[] = [];
        for (const child of this.childNodes) {
          if (child instanceof MiniElement) {
            if (predicate(child)) out.push(child);
            out.push(...child.querySelectorAll(predicate));
          }
        }
        return out;
      }
      querySelector(predicate: (el: MiniElement) => boolean): MiniElement | null {
        return this.querySelectorAll(predicate)[0] || null;
      }
    }
    class MiniDocument extends MiniNode {
      body: MiniElement;
      documentElement: MiniElement;
      defaultView: any = null;
      oninput: any = null;
      onchange: any = null;
      constructor() {
        super(9, null as any);
        this.ownerDocument = this;
        this.documentElement = new MiniElement("html", this);
        this.body = new MiniElement("body", this);
        this.documentElement.appendChild(this.body);
      }
      createElement(tagName: string) {
        return new MiniElement(tagName, this);
      }
      createElementNS(_ns: string, tagName: string) {
        return new MiniElement(tagName, this);
      }
      createTextNode(text: string) {
        return new MiniTextNode(text, this);
      }
      createComment(data: string) {
        return new MiniCommentNode(data, this);
      }
    }

    const doc = new MiniDocument();
    const invokeMock = vi.fn().mockResolvedValue({
      rid: 1,
      currentVersion: "1.0.0",
      version: "1.2.0",
      body: "Security patch release",
    });
    const win: any = {
      document: doc,
      HTMLElement: MiniElement,
      HTMLInputElement: MiniElement,
      HTMLTextAreaElement: MiniElement,
      HTMLSelectElement: MiniElement,
      HTMLOptionElement: MiniElement,
      HTMLButtonElement: MiniElement,
      HTMLFormElement: MiniElement,
      HTMLIFrameElement: class extends MiniElement {},
      SVGElement: MiniElement,
      Element: MiniElement,
      Node: MiniNode,
      Text: MiniTextNode,
      Document: MiniDocument,
      Event: class {
        type: string;
        bubbles: boolean;
        constructor(type: string, init?: { bubbles?: boolean }) {
          this.type = type;
          this.bubbles = init?.bubbles ?? true;
        }
      },
      MouseEvent: class {
        type: string;
        bubbles: boolean;
        constructor(type: string, init?: { bubbles?: boolean }) {
          this.type = type;
          this.bubbles = init?.bubbles ?? true;
        }
      },
      InputEvent: class {
        type: string;
        bubbles: boolean;
        constructor(type: string, init?: { bubbles?: boolean }) {
          this.type = type;
          this.bubbles = init?.bubbles ?? true;
        }
      },
      __TAURI_INTERNALS__: { invoke: invokeMock },
      addEventListener: () => {},
      removeEventListener: () => {},
      navigator: { userAgent: "node" },
    };
    doc.defaultView = win;

    const prevDocument = (globalThis as any).document;
    const prevHTMLElement = (globalThis as any).HTMLElement;
    const prevHTMLIFrameElement = (globalThis as any).HTMLIFrameElement;
    const prevElement = (globalThis as any).Element;
    const prevNode = (globalThis as any).Node;
    const prevActEnv = (globalThis as any).IS_REACT_ACT_ENVIRONMENT;

    (globalThis as any).window = win;
    (globalThis as any).document = doc;
    (globalThis as any).HTMLElement = MiniElement;
    (globalThis as any).HTMLIFrameElement = win.HTMLIFrameElement;
    (globalThis as any).Element = MiniElement;
    (globalThis as any).Node = MiniNode;
    (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

    const container = doc.createElement("div");
    doc.body.appendChild(container);
    const { createRoot } = await import("react-dom/client");
    const root: Root = createRoot(container as unknown as HTMLElement);

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:54321", authToken: "tok" });
    const diagCalls: boolean[] = [];
    vi.spyOn(client, "getDiagnostics").mockImplementation(async (strict = false) => {
      diagCalls.push(strict);
      return sampleHealthyReport;
    });
    let govAttempt = 0;
    vi.spyOn(client, "getGovernorDiagnostics").mockImplementation(async () => {
      govAttempt += 1;
      if (govAttempt === 1) {
        throw new Error("Governor sampling failed");
      }
      return sampleGovernor;
    });

    try {
      await act(async () => {
        root.render(
          React.createElement(DiagnosticsView, {
            client,
            sidecarState: sampleSidecar,
            health: { status: "healthy", service: "lifeos" },
            readiness: { status: "ready", databases: {}, storage: {}, identity: true },
            connectionError: null,
            onRefreshSidecar: () => {},
            events: [],
            onClearEvents: () => {},
          })
        );
      });

      // Initial mount: 1 diagnostics fetch (false), 1 failed governor fetch -> error banner shown
      expect(diagCalls).toEqual([false]);
      expect(container.querySelector((el) => el.getAttribute("data-testid") === "diagnostics-error-banner")?.textContent).toContain(
        "Governor sampling failed"
      );

      // Toggle Strict Mode checkbox -> must fire only 1 additional diagnostics call (true)
      const checkbox = container.querySelector((el) => el.tagName === "INPUT" && el.getAttribute("type") === "checkbox");
      expect(checkbox).not.toBeNull();
      await act(async () => {
        checkbox!.click();
        await Promise.resolve();
      });
      expect(diagCalls).toEqual([false, true]);

      // Sample governor again -> succeeds and clears stale error banner
      const sampleBtn = container.querySelector(
        (el) => el.tagName === "BUTTON" && el.textContent.trim() === "Sample"
      );
      expect(sampleBtn).not.toBeNull();
      await act(async () => {
        sampleBtn!.click();
        await Promise.resolve();
      });
      expect(container.querySelector((el) => el.getAttribute("data-testid") === "diagnostics-error-banner")).toBeNull();

      // Check updates -> parses Tauri v2 metadata `{ version: "1.2.0" }` without `.available`
      const checkUpdatesBtn = container.querySelector(
        (el) => el.tagName === "BUTTON" && el.textContent.trim() === "Check Updates"
      );
      expect(checkUpdatesBtn).not.toBeNull();
      await act(async () => {
        checkUpdatesBtn!.click();
        await Promise.resolve();
      });
      expect(invokeMock).toHaveBeenCalledTimes(1);
      expect(invokeMock.mock.calls[0][0]).toBe("plugin:updater|check");
      expect(container.textContent).toContain("Update Available (v1.2.0)");
      expect(container.textContent).toContain("Verified (Ed25519)");
    } finally {
      try {
        act(() => {
          root.unmount();
        });
      } catch {
        // ignore if already unmounted
      }
      (globalThis as any).document = prevDocument;
      (globalThis as any).HTMLElement = prevHTMLElement;
      (globalThis as any).HTMLIFrameElement = prevHTMLIFrameElement;
      (globalThis as any).Element = prevElement;
      (globalThis as any).Node = prevNode;
      (globalThis as any).IS_REACT_ACT_ENVIRONMENT = prevActEnv;
    }
  });
});
