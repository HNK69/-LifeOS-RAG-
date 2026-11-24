/**
 * useEventStream.ts
 * React hook for subscribing to real-time LifeOS events.
 */

import { useEffect, useRef, useState } from "react";
import { ConnectionState, LifeOSEventClient } from "../api/events";
import { LifeOSEventPayload } from "../api/types";

export function useEventStream(
  baseUrl: string,
  authToken?: string,
  topics: string[] = ["*"]
) {
  const [connectionState, setConnectionState] = useState<ConnectionState>("disconnected");
  const [events, setEvents] = useState<LifeOSEventPayload[]>([]);
  const clientRef = useRef<LifeOSEventClient | null>(null);

  useEffect(() => {
    const client = new LifeOSEventClient({
      baseUrl,
      authToken,
      topics,
    });
    clientRef.current = client;

    const unsubState = client.onStateChange(setConnectionState);
    const unsubEvents = client.subscribe("*", (evt) => {
      setEvents((prev) => [evt, ...prev.slice(0, 99)]);
    });

    client.connect();

    return () => {
      unsubState();
      unsubEvents();
      client.disconnect();
      clientRef.current = null;
    };
  }, [baseUrl, authToken, topics.join(",")]);

  const clearEvents = () => setEvents([]);

  return {
    connectionState,
    events,
    clearEvents,
    client: clientRef.current,
  };
}
