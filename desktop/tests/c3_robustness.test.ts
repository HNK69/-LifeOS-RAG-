import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Root } from "react-dom/client";
import { act } from "react";
import { LifeOSWebClient, LifeOSApiError } from "../src/api/client";
import {
  ConsentGrant,
  PeerSyncStatus,
  PrivacyExportResponse,
  SyncConflict,
  SyncExportResponse,
  WorkspaceProfile,
} from "../src/api/types";
import {
  QueryView,
  extractCitations,
  extractConfidence,
  normalizeScorePercent,
  buildFileLink,
} from "../src/views/QueryView";
import { PrivacyView } from "../src/views/PrivacyView";
import { SyncView, computeDataDiff, getTrustBadgeStyle } from "../src/views/SyncView";
import { WorkspacesView, WorkspaceSwitcher } from "../src/views/WorkspacesView";
import { ErrorBoundary } from "../src/App";

// ---------------------------------------------------------------------------
// Minimal DOM shim for interactive react-dom/client tests in Node
// ---------------------------------------------------------------------------

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

  addEventListener(type: string, fn: (ev: any) => void): void {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(fn);
  }

  removeEventListener(type: string, fn: (ev: any) => void): void {
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
    if (!ev.stopPropagation) {
      ev.stopPropagation = () => {};
    }
    const list = this.listeners[ev.type] || [];
    for (const fn of [...list]) {
      fn(ev);
    }
    if (ev.bubbles !== false && this.parentNode) {
      this.parentNode.dispatchEvent(ev);
    }
    return !ev.defaultPrevented;
  }

  get firstChild(): MiniNode | null {
    return this.childNodes[0] || null;
  }

  get nextSibling(): MiniNode | null {
    if (!this.parentNode) return null;
    const idx = this.parentNode.childNodes.indexOf(this);
    return idx >= 0 && idx + 1 < this.parentNode.childNodes.length ? this.parentNode.childNodes[idx + 1] : null;
  }

  appendChild(child: MiniNode): MiniNode {
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  insertBefore(newNode: MiniNode, refNode: MiniNode | null): MiniNode {
    if (!refNode) return this.appendChild(newNode);
    if (newNode.parentNode) {
      newNode.parentNode.removeChild(newNode);
    }
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
  get data(): string {
    return this.nodeValue;
  }
  set data(v: string) {
    this.nodeValue = v;
  }
  get textContent(): string {
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
  get textContent(): string {
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
  selected = false;
  private _value = "";

  constructor(tagName: string, ownerDocument: MiniDocument) {
    super(1, ownerDocument);
    this.tagName = tagName.toUpperCase();
  }

  get options(): MiniElement[] {
    return this.querySelectorAll((el) => el.tagName === "OPTION");
  }

  get value(): string {
    return this._value;
  }
  set value(v: string) {
    this._value = v == null ? "" : String(v);
  }

  get nodeName(): string {
    return this.tagName;
  }

  setAttribute(name: string, val: string): void {
    const str = String(val);
    this.attributes[name] = str;
    if (name === "disabled") this.disabled = str !== "false";
    if (name === "value") this._value = str;
  }

  removeAttribute(name: string): void {
    delete this.attributes[name];
    if (name === "disabled") this.disabled = false;
  }

  getAttribute(name: string): string | null {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }

  click(): void {
    if (this.disabled) return;
    this.dispatchEvent({ type: "click", bubbles: true });
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

  createElement(tagName: string): MiniElement {
    return new MiniElement(tagName, this);
  }

  createElementNS(_ns: string, tagName: string): MiniElement {
    return new MiniElement(tagName, this);
  }

  createTextNode(text: string): MiniTextNode {
    return new MiniTextNode(text, this);
  }

  createComment(data: string): MiniCommentNode {
    return new MiniCommentNode(data, this);
  }
}

async function installDomEnv() {
  const doc = new MiniDocument();
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
    addEventListener: () => {},
    removeEventListener: () => {},
    navigator: { userAgent: "node" },
  };
  doc.defaultView = win;

  const prevWindow = (globalThis as any).window;
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
  const root: Root = createRoot(container as any);

  const cleanup = () => {
    try {
      act(() => {
        root.unmount();
      });
    } catch {
      // Ignore unmount errors if render already aborted
    }
    (globalThis as any).window = prevWindow;
    (globalThis as any).document = prevDocument;
    (globalThis as any).HTMLElement = prevHTMLElement;
    (globalThis as any).HTMLIFrameElement = prevHTMLIFrameElement;
    (globalThis as any).Element = prevElement;
    (globalThis as any).Node = prevNode;
    (globalThis as any).IS_REACT_ACT_ENVIRONMENT = prevActEnv;
  };

  return { doc, container, root, cleanup };
}

function setControlledValue(el: MiniElement, nextValue: string): void {
  const protoDesc = Object.getOwnPropertyDescriptor(MiniElement.prototype, "value");
  if (protoDesc && protoDesc.set) {
    protoDesc.set.call(el, nextValue);
  } else {
    el.value = nextValue;
  }
  el.dispatchEvent({ type: "input", bubbles: true, target: el });
  el.dispatchEvent({ type: "change", bubbles: true, target: el });
}

// ---------------------------------------------------------------------------
// Test Suites
// ---------------------------------------------------------------------------

describe("C3 Iteration 2 Robustness, Edge Cases & Contract Fixes", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  // -------------------------------------------------------------------------
  // Client SDK Robustness (Task 1 & Task 7)
  // -------------------------------------------------------------------------
  describe("LifeOSWebClient robustness", () => {
    it("sends X-Workspace-ID header in query() when workspace_id is specified and omits when absent", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ answer: "ok" }),
      });
      globalThis.fetch = mockFetch;

      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000", authToken: "tok" });
      await client.query({ query: "hello", session_id: "s1", workspace_id: "ws-work" });
      expect(mockFetch.mock.calls[0][1].headers["X-Workspace-ID"]).toBe("ws-work");

      await client.query({ query: "hello", session_id: "s2" });
      expect(mockFetch.mock.calls[1][1].headers["X-Workspace-ID"]).toBeUndefined();
    });

    it("URL-encodes all path parameters (workspaceId, consentId, conflictId)", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({}),
      });
      globalThis.fetch = mockFetch;

      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      const hostile = "id/evil?admin=true#frag";
      const encoded = encodeURIComponent(hostile);

      await client.getWorkspace(hostile);
      await client.updateWorkspace(hostile, { name: "x" });
      await client.deleteWorkspace(hostile);
      await client.listWorkspaceMembers(hostile);
      await client.revokeConsent(hostile);
      await client.resolveSyncConflict(hostile, { a: 1 });

      expect(mockFetch.mock.calls[0][0]).toBe(`http://127.0.0.1:8000/api/workspaces/${encoded}`);
      expect(mockFetch.mock.calls[1][0]).toBe(`http://127.0.0.1:8000/api/workspaces/${encoded}`);
      expect(mockFetch.mock.calls[2][0]).toBe(`http://127.0.0.1:8000/api/workspaces/${encoded}`);
      expect(mockFetch.mock.calls[3][0]).toBe(`http://127.0.0.1:8000/api/workspaces/${encoded}/members`);
      expect(mockFetch.mock.calls[4][0]).toBe(`http://127.0.0.1:8000/api/privacy/consent/${encoded}`);
      expect(mockFetch.mock.calls[5][0]).toBe(`http://127.0.0.1:8000/api/sync/conflicts/${encoded}/resolve`);
    });

    it("preserves HTTP statusCode when response.ok is true but response.json() throws SyntaxError", async () => {
      globalThis.fetch = vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("Unexpected end of JSON input");
        },
      });

      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      try {
        await client.getHealth();
        expect.unreachable("Expected LifeOSApiError");
      } catch (err: any) {
        expect(err).toBeInstanceOf(LifeOSApiError);
        expect(err.statusCode).toBe(200);
      }
    });
  });

  // -------------------------------------------------------------------------
  // QueryView Robustness (Task 2)
  // -------------------------------------------------------------------------
  describe("QueryView robustness", () => {
    it("guards extractCitations, buildFileLink, normalizeScorePercent, and extractConfidence on malformed inputs", () => {
      expect(extractCitations(null as any)).toEqual([]);

      const cits = extractCitations({
        answer: "ok",
        sources: [
          null as any,
          { document_id: "doc-42", source_path: null as any, score: 0.85, snippet: "hello" },
          { document_id: "", source_path: "", score: Infinity, snippet: null as any },
        ],
      });
      expect(cits).toHaveLength(2);
      expect(cits[0].sourcePath).toBe("doc-42");
      expect(cits[0].score).toBe(85);
      expect(cits[1].sourcePath).toBe("Unknown source");
      expect(cits[1].score).toBeNull();

      const dataCits = extractCitations({
        answer: "ok",
        data: [null as any, undefined as any, { document_id: "d9", score: 0.9 }],
      });
      expect(dataCits).toHaveLength(1);
      expect(dataCits[0].sourcePath).toBe("d9");

      expect(buildFileLink(12345 as any)).toBe("file:///12345");
      expect(buildFileLink(null as any)).toBe("#");

      expect(normalizeScorePercent(Infinity)).toBeNull();
      expect(normalizeScorePercent(-Infinity)).toBeNull();
      expect(normalizeScorePercent(NaN)).toBeNull();
      expect(normalizeScorePercent(-5)).toBeNull();
      expect(normalizeScorePercent(101)).toBeNull();

      expect(extractConfidence({ answer: "ok", confidence: NaN }, cits)).toBe(85);
      expect(extractConfidence({ answer: "ok", confidence: Infinity }, cits)).toBe(85);
    });

    it("renders maxLength=4096, char counter, and stringifies non-string turn.content", () => {
      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      const html = renderToStaticMarkup(
        React.createElement(QueryView, {
          client,
          initialQueryText: "hello",
          initialTurns: [
            {
              id: "t-obj",
              role: "assistant",
              content: { nested: "object-answer" } as any,
              timestamp: "2026-11-24T12:00:00Z",
            },
          ],
        })
      );
      expect(html).toContain('maxLength="4096"');
      expect(html).toContain("5 / 4096");
      expect(html).toContain("{&quot;nested&quot;:&quot;object-answer&quot;}");
    });

    it("ignores stale query responses when New Session is clicked while request is in flight", async () => {
      let resolvePending!: (val: any) => void;
      const pendingPromise = new Promise((res) => {
        resolvePending = res;
      });
      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      const querySpy = vi.spyOn(client, "query").mockReturnValue(pendingPromise as any);

      const { container, root, cleanup } = await installDomEnv();
      try {
        await act(async () => {
          root.render(
            React.createElement(QueryView, {
              client,
              initialSessionId: "sess-old",
            })
          );
        });

        const textarea = container.querySelector((el) => el.tagName === "TEXTAREA")!;
        const form = container.querySelector((el) => el.tagName === "FORM")!;
        const newSessionBtn = container.querySelector(
          (el) => el.tagName === "BUTTON" && el.textContent.includes("New Session")
        )!;

        await act(async () => {
          setControlledValue(textarea, "Slow query in old session");
        });

        await act(async () => {
          form.dispatchEvent({ type: "submit", bubbles: true });
        });
        expect(querySpy).toHaveBeenCalledTimes(1);

        // Click New Session while query is still pending
        await act(async () => {
          newSessionBtn.click();
        });

        // Now resolve the old session's query
        await act(async () => {
          resolvePending({
            answer: "STALE_RESPONSE_FROM_OLD_SESSION",
            session_id: "sess-old",
          });
          await Promise.resolve();
        });

        expect(container.textContent).not.toContain("STALE_RESPONSE_FROM_OLD_SESSION");
        expect(container.textContent).not.toContain("sess-old");
      } finally {
        cleanup();
      }
    });
  });

  // -------------------------------------------------------------------------
  // PrivacyView Robustness (Task 3)
  // -------------------------------------------------------------------------
  describe("PrivacyView robustness", () => {
    it("handles null purposes, null export arrays, and unknown initial audit status", () => {
      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });

      const unknownHtml = renderToStaticMarkup(
        React.createElement(PrivacyView, {
          client,
          initialSummary: null,
          initialAuditVerify: null,
          initialConsents: [
            {
              consent_id: "c-null",
              resource_path: "C:\\Docs",
              scope_type: "folder",
              status: "active",
              categories: [],
              purposes: null as any,
              allow_cloud_sync: false,
              granted_at: "2026-11-24T00:00:00Z",
            },
          ],
          initialExportData: {
            exported_at: "2026-11-24T00:00:00Z",
            device_id: "dev-1",
            consents: null as any,
            documents: null as any,
            context_memories: null as any,
            people: null as any,
            audit_log: null as any,
          } as PrivacyExportResponse,
        })
      );

      expect(unknownHtml).toContain("Unknown");
      expect(unknownHtml).not.toContain("Integrity Violation");
      expect(unknownHtml).toContain("—");
      expect(unknownHtml).toContain("Consents: <strong>0</strong>");
    });

    it("keeps independent disabled state for concurrent revocations and renders Forget modal errors inside role=dialog", async () => {
      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      const sampleConsents: ConsentGrant[] = [
        {
          consent_id: "c1",
          resource_path: "C:\\A",
          scope_type: "folder",
          status: "active",
          categories: ["general"],
          purposes: ["indexing"],
          allow_cloud_sync: false,
          granted_at: "2026-11-24T00:00:00Z",
        },
        {
          consent_id: "c2",
          resource_path: "C:\\B",
          scope_type: "folder",
          status: "active",
          categories: ["general"],
          purposes: ["query"],
          allow_cloud_sync: false,
          granted_at: "2026-11-24T00:00:00Z",
        },
      ];

      let resolveC1!: (v: any) => void;
      let resolveC2!: (v: any) => void;
      vi.spyOn(client, "getPrivacySummary").mockResolvedValue({
        device_id: "d1",
        device_name: "Desk",
        local_first_mode: true,
        cloud_sync_enabled: false,
        telemetry_enabled: false,
        active_consented_sources: 2,
        total_consent_grants: 2,
        total_audit_events: 1,
        audit_chain_valid: true,
        total_indexed_documents: 0,
      });
      vi.spyOn(client, "listConsents").mockResolvedValue(sampleConsents);
      vi.spyOn(client, "verifyAuditTrail").mockResolvedValue({ valid: true, verified_records: 1, error: null });
      vi.spyOn(client, "revokeConsent").mockImplementation((id: string) => {
        if (id === "c1") return new Promise((r) => (resolveC1 = r));
        return new Promise((r) => (resolveC2 = r));
      });

      const { container, root, cleanup } = await installDomEnv();
      try {
        await act(async () => {
          root.render(
            React.createElement(PrivacyView, {
              client,
              initialConsents: sampleConsents,
              initialForgetModalOpen: true,
            })
          );
          await Promise.resolve();
        });

        const getRevokeBtns = () =>
          container.querySelectorAll(
            (el) =>
              el.tagName === "BUTTON" &&
              (el.textContent === "Revoke" || el.textContent === "Revoking...")
          );

        await act(async () => {
          getRevokeBtns()[0].click();
        });
        await act(async () => {
          getRevokeBtns()[1].click();
        });

        // Both buttons must remain disabled independently while both requests are pending
        expect(getRevokeBtns()[0].disabled).toBe(true);
        expect(getRevokeBtns()[1].disabled).toBe(true);

        // Resolve only c1; c2 must stay disabled
        await act(async () => {
          resolveC1({ revoked: true, consent_id: "c1" });
          await Promise.resolve();
        });
        const remainingRevokeBtns = getRevokeBtns();
        expect(remainingRevokeBtns).toHaveLength(1);
        expect(remainingRevokeBtns[0].disabled).toBe(true);

        await act(async () => {
          resolveC2({ revoked: true, consent_id: "c2" });
          await Promise.resolve();
        });

        // Submit Forget modal without path value (after checking confirm) -> error must appear inside dialog
        const dialog = container.querySelector((el) => el.getAttribute("role") === "dialog")!;
        const forgetForm = dialog.querySelector((el) => el.tagName === "FORM")!;
        await act(async () => {
          forgetForm.dispatchEvent({ type: "submit", bubbles: true });
        });
        expect(dialog.textContent).toContain("Please confirm permanent purge before continuing.");
      } finally {
        cleanup();
      }
    });
  });

  // -------------------------------------------------------------------------
  // SyncView Robustness (Task 4)
  // -------------------------------------------------------------------------
  describe("SyncView robustness", () => {
    it("guards missing trust_status and null envelope", () => {
      expect(getTrustBadgeStyle(undefined).label).toBe("unknown");

      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      const html = renderToStaticMarkup(
        React.createElement(SyncView, {
          client,
          initialPeers: [
            {
              peer_device_id: "p1",
              device_name: "Phone",
              device_type: "mobile",
              trust_status: undefined as any,
              last_synced_at: null,
              unresolved_conflicts: 0,
            },
          ],
          initialExportResult: {
            batch_id: "b1",
            items_count: 1,
            manifest: {},
            envelope: null as any,
          } as SyncExportResponse,
        })
      );

      expect(html).toContain("unknown");
      expect(html).toContain("Algorithm: —");
    });

    it("detects type mismatches, missing vs dash, ignores reordered nested keys, and guards non-objects in computeDataDiff", () => {
      const typeDiff = computeDataDiff(
        { numStr: "123", boolStr: "true", nullStr: "null", dash: "—" },
        { numStr: 123, boolStr: true, nullStr: null }
      );
      const byKey = Object.fromEntries(typeDiff.map((r) => [r.key, r]));
      expect(byKey.numStr.isDifferent).toBe(true);
      expect(byKey.boolStr.isDifferent).toBe(true);
      expect(byKey.nullStr.isDifferent).toBe(true);
      expect(byKey.dash.isDifferent).toBe(true);

      const reordered = computeDataDiff(
        { nested: { a: 1, b: { c: 2, d: 3 } } },
        { nested: { b: { d: 3, c: 2 }, a: 1 } }
      );
      expect(reordered[0].isDifferent).toBe(false);

      const nonObj = computeDataDiff("hostile" as any, null as any);
      expect(nonObj.map((r) => r.key)).not.toContain("0");
      expect(nonObj).toHaveLength(1);
    });

    it("does not re-fetch peers/conflicts on peer selection and validates Custom Merge plain object", async () => {
      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      const peers: PeerSyncStatus[] = [
        {
          peer_device_id: "p1",
          device_name: "Phone 1",
          device_type: "mobile",
          trust_status: "paired",
          last_synced_at: null,
          unresolved_conflicts: 0,
        },
        {
          peer_device_id: "p2",
          device_name: "Phone 2",
          device_type: "mobile",
          trust_status: "paired",
          last_synced_at: null,
          unresolved_conflicts: 0,
        },
      ];
      const conflicts: SyncConflict[] = [
        {
          conflict_id: "conf-1",
          entity_type: "context",
          entity_id: "k1",
          workspace_id: "default",
          peer_device_id: "p1",
          local_data: { a: 1 },
          remote_data: { a: 2 },
          local_clock: { d: 1 },
          remote_clock: { p: 1 },
          status: "unresolved",
          resolution_strategy: "MANUAL",
          resolved_value: null,
          created_at: "2026-11-24T00:00:00Z",
          resolved_at: null,
        },
      ];

      const listPeersSpy = vi.spyOn(client, "listSyncPeers").mockResolvedValue(peers);
      const listConflictsSpy = vi.spyOn(client, "listSyncConflicts").mockResolvedValue(conflicts);
      const resolveSpy = vi.spyOn(client, "resolveSyncConflict").mockResolvedValue({
        conflict_id: "conf-1",
        resolved: true,
        status: "resolved",
      });

      const { container, root, cleanup } = await installDomEnv();
      try {
        await act(async () => {
          root.render(React.createElement(SyncView, { client }));
          await Promise.resolve();
        });

        expect(listPeersSpy).toHaveBeenCalledTimes(1);
        expect(listConflictsSpy).toHaveBeenCalledTimes(1);

        // Select second peer via dropdown -> must NOT trigger redundant fetches
        const selectEl = container.querySelector(
          (el) => el.tagName === "SELECT" && el.getAttribute("aria-label") === "Target Peer Device"
        )!;
        await act(async () => {
          setControlledValue(selectEl, "p2");
        });
        expect(listPeersSpy).toHaveBeenCalledTimes(1);
        expect(listConflictsSpy).toHaveBeenCalledTimes(1);

        // Open Custom Merge and submit non-object payloads ('null', '[1,2]', '123')
        const customBtn = container.querySelector(
          (el) => el.tagName === "BUTTON" && el.textContent === "Custom Merge"
        )!;
        await act(async () => {
          customBtn.click();
        });

        const textareas = container.querySelectorAll((el) => el.tagName === "TEXTAREA");
        const customTextarea = textareas[textareas.length - 1];
        const submitBtn = container.querySelector(
          (el) => el.tagName === "BUTTON" && el.textContent === "Submit Custom Resolution"
        )!;

        for (const badJson of ["null", "[1, 2]", "123", '"hello"']) {
          await act(async () => {
            setControlledValue(customTextarea, badJson);
          });
          await act(async () => {
            submitBtn.click();
          });
          expect(resolveSpy).not.toHaveBeenCalled();
          expect(container.textContent).toContain("plain object");
        }
      } finally {
        cleanup();
      }
    });
  });

  // -------------------------------------------------------------------------
  // WorkspacesView & ErrorBoundary Robustness (Task 5 & Task 6)
  // -------------------------------------------------------------------------
  describe("WorkspacesView & ErrorBoundary robustness", () => {
    it("does not fall back to workspaces[0] in WorkspaceSwitcher when activeWorkspaceId is unknown", () => {
      const workspaces: WorkspaceProfile[] = [
        {
          workspace_id: "ws-restricted",
          name: "Secret Vault",
          profile_type: "restricted",
          description: null,
          created_at: "2026-11-24T00:00:00Z",
          updated_at: "2026-11-24T00:00:00Z",
          is_active: true,
          settings: { local_only: true },
          document_count: 0,
          context_count: 0,
        },
      ];

      const html = renderToStaticMarkup(
        React.createElement(WorkspaceSwitcher, {
          workspaces,
          activeWorkspaceId: "non-existent-ws",
          onSelectWorkspace: () => {},
        })
      );

      expect(html).toContain("non-existent-ws");
      expect(html).not.toContain("Local-Only");
    });

    it("renders Create Workspace modal errors inside role=dialog", async () => {
      const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
      vi.spyOn(client, "listWorkspaces").mockResolvedValue([]);

      const { container, root, cleanup } = await installDomEnv();
      try {
        await act(async () => {
          root.render(
            React.createElement(WorkspacesView, {
              client,
              activeWorkspaceId: "default",
              onSelectWorkspace: () => {},
              initialCreateDialogOpen: true,
            })
          );
          await Promise.resolve();
        });

        const dialog = container.querySelector((el) => el.getAttribute("role") === "dialog")!;
        const form = dialog.querySelector((el) => el.tagName === "FORM")!;
        await act(async () => {
          form.dispatchEvent({ type: "submit", bubbles: true });
        });

        expect(dialog.textContent).toContain("Workspace name is required.");
      } finally {
        cleanup();
      }
    });

    it("catches render crashes in ErrorBoundary and recovers on Reset View", async () => {
      let shouldThrow = true;
      const CrashableView: React.FC = () => {
        if (shouldThrow) {
          throw new Error("Simulated render crash");
        }
        return React.createElement("div", null, "Recovered cleanly");
      };

      const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      const { container, root, cleanup } = await installDomEnv();
      try {
        await act(async () => {
          root.render(
            React.createElement(
              ErrorBoundary,
              { viewName: "Test View" },
              React.createElement(CrashableView)
            )
          );
        });

        expect(container.textContent).toContain("Test View encountered an error");
        expect(container.textContent).toContain("Simulated render crash");
        const resetBtn = container.querySelector(
          (el) => el.tagName === "BUTTON" && el.textContent === "Reset View"
        )!;
        expect(resetBtn).toBeTruthy();

        shouldThrow = false;
        await act(async () => {
          resetBtn.click();
        });

        expect(container.textContent).toContain("Recovered cleanly");
      } finally {
        errSpy.mockRestore();
        cleanup();
      }
    });
  });
});
