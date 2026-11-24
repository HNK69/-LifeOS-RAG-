import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { LifeOSWebClient, LifeOSApiError } from "../src/api/client";

describe("LifeOSWebClient", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("constructs client and formats baseUrl cleanly", () => {
    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000///" });
    expect(client.getBaseUrl()).toBe("http://127.0.0.1:8000");
  });

  it("attaches Authorization header when authToken is provided", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ status: "ok", service: "lifeos" }),
    });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({
      baseUrl: "http://127.0.0.1:8000",
      authToken: "secret-session-token-123",
    });

    const res = await client.getHealth();
    expect(res.status).toBe("ok");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, options] = mockFetch.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/health");
    expect(options.headers["Authorization"]).toBe("Bearer secret-session-token-123");
    expect(options.headers["Content-Type"]).toBe("application/json");
  });

  it("throws typed LifeOSApiError on HTTP error status", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      text: async () => "Invalid or expired token",
    });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({
      baseUrl: "http://127.0.0.1:8000",
    });

    await expect(client.getReady()).rejects.toThrowError(LifeOSApiError);

    try {
      await client.getReady();
    } catch (err: any) {
      expect(err.statusCode).toBe(401);
      expect(err.responseBody).toBe("Invalid or expired token");
    }
  });

  it("handles query params serialization in listConsents", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [{ consent_id: "c1", status: "active" }],
    });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const consents = await client.listConsents("active");
    expect(consents).toHaveLength(1);

    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/api/privacy/consent?status=active");
  });

  it("submits structured query request with payload", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ answer: "Test answer", confidence: 0.95 }),
    });
    globalThis.fetch = mockFetch;

    const client = new LifeOSWebClient({ baseUrl: "http://127.0.0.1:8000" });
    const resp = await client.query({ query: "What is LifeOS?", workspace_id: "ws-1" });

    expect(resp.answer).toBe("Test answer");
    const [, options] = mockFetch.mock.calls[0];
    expect(JSON.parse(options.body)).toEqual({
      query: "What is LifeOS?",
      workspace_id: "ws-1",
    });
  });
});
