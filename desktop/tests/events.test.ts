import { describe, it, expect, beforeEach } from "vitest";
import { LifeOSEventClient, ConnectionState } from "../src/api/events";

class MockEventSource {
  public url: string;
  public onopen: (() => void) | null = null;
  public onmessage: ((msg: any) => void) | null = null;
  public onerror: (() => void) | null = null;
  public closed = false;

  constructor(url: string) {
    this.url = url;
    setTimeout(() => {
      if (this.onopen && !this.closed) {
        this.onopen();
      }
    }, 10);
  }

  public close() {
    this.closed = true;
  }

  public emitMessage(data: any) {
    if (this.onmessage && !this.closed) {
      this.onmessage({ data: JSON.stringify(data) });
    }
  }

  public emitError() {
    if (this.onerror && !this.closed) {
      this.onerror();
    }
  }
}

describe("LifeOSEventClient", () => {
  beforeEach(() => {
    (globalThis as any).EventSource = MockEventSource;
  });

  it("initializes in disconnected state and transitions to connecting/connected", async () => {
    const client = new LifeOSEventClient({
      baseUrl: "http://127.0.0.1:8000",
      authToken: "test-token",
    });

    const states: ConnectionState[] = [];
    client.onStateChange((s) => states.push(s));

    expect(client.getState()).toBe("disconnected");

    client.connect();
    expect(client.getState()).toBe("connecting");

    await new Promise((r) => setTimeout(r, 20));
    expect(client.getState()).toBe("connected");

    client.disconnect();
    expect(client.getState()).toBe("disconnected");
  });

  it("subscribes and receives typed event payloads", async () => {
    const client = new LifeOSEventClient({
      baseUrl: "http://127.0.0.1:8000",
      topics: ["ingestion.*"],
    });

    const received: any[] = [];
    const unsubscribe = client.subscribe("ingestion.completed", (evt) => {
      received.push(evt);
    });

    client.connect();
    await new Promise((r) => setTimeout(r, 20));

    const activeSource = (client as any).eventSource as MockEventSource;
    expect(activeSource).toBeDefined();

    activeSource.emitMessage({
      event_id: "evt-001",
      event_type: "ingestion.completed",
      timestamp: "2026-11-24T19:00:00Z",
      producer: "ingestion.worker",
      payload: { job_id: "job-123" },
    });

    expect(received).toHaveLength(1);
    expect(received[0].event_id).toBe("evt-001");
    expect(received[0].payload.job_id).toBe("job-123");

    // Test unsubscribe
    unsubscribe();
    activeSource.emitMessage({
      event_id: "evt-002",
      event_type: "ingestion.completed",
      timestamp: "2026-11-24T19:00:01Z",
      producer: "ingestion.worker",
      payload: { job_id: "job-456" },
    });
    expect(received).toHaveLength(1);

    client.disconnect();
  });
});
