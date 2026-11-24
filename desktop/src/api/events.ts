/**
 * events.ts
 * Real-time SSE and WebSocket event stream client with automatic reconnection
 * and Last-Event-ID tracking.
 */

import { LifeOSEventPayload } from "./types";

export type EventCallback = (event: LifeOSEventPayload) => void;
export type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected";

export interface EventClientConfig {
  baseUrl: string;
  authToken?: string;
  topics?: string[];
  reconnectIntervalMs?: number;
  maxReconnectIntervalMs?: number;
}

export class LifeOSEventClient {
  private baseUrl: string;
  private authToken?: string;
  private topics: string[];
  private subscribers: Map<string, Set<EventCallback>> = new Map();
  private allSubscribers: Set<EventCallback> = new Set();
  private stateListeners: Set<(state: ConnectionState) => void> = new Set();

  private currentState: ConnectionState = "disconnected";
  private eventSource: EventSource | null = null;
  private lastEventId: string | null = null;
  private reconnectTimer: any = null;
  private reconnectInterval: number;
  private maxReconnectInterval: number;
  private isIntentionallyClosed = false;

  constructor(config: EventClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/+$/, "");
    this.authToken = config.authToken;
    this.topics = config.topics || ["*"];
    this.reconnectInterval = config.reconnectIntervalMs ?? 1000;
    this.maxReconnectInterval = config.maxReconnectIntervalMs ?? 15000;
  }

  public getState(): ConnectionState {
    return this.currentState;
  }

  private setState(newState: ConnectionState): void {
    if (this.currentState !== newState) {
      this.currentState = newState;
      for (const listener of this.stateListeners) {
        try {
          listener(newState);
        } catch (err) {
          console.error("Error in state listener:", err);
        }
      }
    }
  }

  public onStateChange(listener: (state: ConnectionState) => void): () => void {
    this.stateListeners.add(listener);
    listener(this.currentState);
    return () => this.stateListeners.delete(listener);
  }

  public subscribe(eventType: string, callback: EventCallback): () => void {
    if (eventType === "*") {
      this.allSubscribers.add(callback);
      return () => this.allSubscribers.delete(callback);
    }

    if (!this.subscribers.has(eventType)) {
      this.subscribers.set(eventType, new Set());
    }
    const set = this.subscribers.get(eventType)!;
    set.add(callback);
    return () => set.delete(callback);
  }

  private dispatchEvent(event: LifeOSEventPayload): void {
    if (event.event_id) {
      this.lastEventId = event.event_id;
    }

    for (const sub of this.allSubscribers) {
      try {
        sub(event);
      } catch (err) {
        console.error("Error in generic subscriber:", err);
      }
    }

    const specific = this.subscribers.get(event.event_type);
    if (specific) {
      for (const sub of specific) {
        try {
          sub(event);
        } catch (err) {
          console.error(`Error in subscriber for ${event.event_type}:`, err);
        }
      }
    }
  }

  public connect(): void {
    if (this.eventSource || this.currentState === "connected" || this.currentState === "connecting") {
      return;
    }

    this.isIntentionallyClosed = false;
    this.setState("connecting");

    const params = new URLSearchParams();
    if (this.topics.length > 0 && !this.topics.includes("*")) {
      params.append("topics", this.topics.join(","));
    }
    if (this.authToken) {
      params.append("token", this.authToken);
    }
    if (this.lastEventId) {
      params.append("last_event_id", this.lastEventId);
    }

    const qs = params.toString();
    const sseUrl = `${this.baseUrl}/api/events/sse${qs ? `?${qs}` : ""}`;

    try {
      this.eventSource = new EventSource(sseUrl);

      this.eventSource.onopen = () => {
        this.reconnectInterval = 1000; // Reset backoff on successful connect
        this.setState("connected");
      };

      this.eventSource.onmessage = (msg: MessageEvent) => {
        if (!msg.data) return;
        try {
          const payload = JSON.parse(msg.data) as LifeOSEventPayload;
          this.dispatchEvent(payload);
        } catch (err) {
          console.warn("Failed to parse SSE JSON payload:", msg.data);
        }
      };

      this.eventSource.onerror = () => {
        this.eventSource?.close();
        this.eventSource = null;

        if (!this.isIntentionallyClosed) {
          this.setState("reconnecting");
          this.scheduleReconnect();
        } else {
          this.setState("disconnected");
        }
      };
    } catch (err) {
      this.setState("reconnecting");
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.reconnectInterval = Math.min(this.reconnectInterval * 1.5, this.maxReconnectInterval);
      this.connect();
    }, this.reconnectInterval);
  }

  public disconnect(): void {
    this.isIntentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
    this.setState("disconnected");
  }
}
