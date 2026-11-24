/**
 * useLifeOS.ts
 * React hook providing LifeOSWebClient instance and health polling.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { LifeOSWebClient } from "../api/client";
import { ReadinessResponse, SidecarState, SystemHealth } from "../api/types";

export function useLifeOS(baseUrl: string = "http://127.0.0.1:8000", authToken?: string) {
  const client = useMemo(() => new LifeOSWebClient({ baseUrl, authToken }), [baseUrl, authToken]);

  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [sidecarState, setSidecarState] = useState<SidecarState>({
    status: "starting",
    host: "127.0.0.1",
    port: 8000,
    token: authToken || "",
    pid: null,
  });
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkStatus = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [h, r] = await Promise.all([client.getHealth(), client.getReady()]);
      setHealth(h);
      setReadiness(r);
      setSidecarState((prev) => ({
        ...prev,
        status: r.status === "ready" ? "ready" : "starting",
      }));
    } catch (err: any) {
      setError(err?.message || "Failed to connect to LifeOS backend");
      setSidecarState((prev) => ({
        ...prev,
        status: "error",
        error: err?.message,
      }));
    } finally {
      setIsLoading(false);
    }
  }, [client]);

  useEffect(() => {
    checkStatus();
    const interval = setInterval(checkStatus, 10000);
    return () => clearInterval(interval);
  }, [checkStatus]);

  return {
    client,
    health,
    readiness,
    sidecarState,
    isLoading,
    error,
    refresh: checkStatus,
  };
}
