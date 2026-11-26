/**
 * useLifeOS.ts
 * React hook providing LifeOSWebClient, LifeOSEventClient, Tauri sidecar hydration, and health polling.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { LifeOSWebClient } from "../api/client";
import { LifeOSEventClient } from "../api/events";
import { ReadinessResponse, SidecarState, SystemHealth } from "../api/types";

export interface TauriWindow extends Window {
  __TAURI_INTERNALS__?: {
    invoke?: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
  };
}

export function isTauriEnvironment(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

export async function hydrateSidecarFromTauri(): Promise<SidecarState | null> {
  if (!isTauriEnvironment()) {
    return null;
  }
  const info = await invoke<SidecarState>("get_sidecar_status");
  if (!info) {
    return null;
  }
  const host = info.host || "127.0.0.1";
  const port = typeof info.port === "number" && info.port > 0 ? info.port : 8000;
  return {
    status: info.status || "ready",
    host,
    port,
    token: info.token || "",
    pid: info.pid ?? null,
    error: info.error,
  };
}

export function useLifeOS(baseUrl: string = "http://127.0.0.1:8000", authToken?: string) {
  const [resolvedBaseUrl, setResolvedBaseUrl] = useState<string>(baseUrl);
  const [resolvedToken, setResolvedToken] = useState<string | undefined>(authToken);

  useEffect(() => {
    setResolvedBaseUrl(baseUrl);
  }, [baseUrl]);

  useEffect(() => {
    setResolvedToken(authToken);
  }, [authToken]);

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

  // Hydrate dynamic sidecar port and ephemeral session token when running inside Tauri shell
  useEffect(() => {
    let active = true;
    if (!isTauriEnvironment()) {
      return;
    }

    hydrateSidecarFromTauri()
      .then((info) => {
        if (!active || !info) return;
        const dynamicUrl = `http://${info.host}:${info.port}`;
        setResolvedBaseUrl(dynamicUrl);
        if (info.token) {
          setResolvedToken(info.token);
        }
        setSidecarState(info);
      })
      .catch((err: any) => {
        if (!active) return;
        setError(err?.message || "Failed to query Tauri sidecar status");
        setSidecarState((prev) => ({
          ...prev,
          status: "error",
          error: err?.message || "Tauri IPC error",
        }));
      });

    return () => {
      active = false;
    };
  }, []);

  const client = useMemo(
    () => new LifeOSWebClient({ baseUrl: resolvedBaseUrl, authToken: resolvedToken }),
    [resolvedBaseUrl, resolvedToken]
  );

  const eventClient = useMemo(
    () => new LifeOSEventClient({ baseUrl: resolvedBaseUrl, authToken: resolvedToken }),
    [resolvedBaseUrl, resolvedToken]
  );

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
    eventClient,
    baseUrl: resolvedBaseUrl,
    authToken: resolvedToken,
    health,
    readiness,
    sidecarState,
    isLoading,
    error,
    refresh: checkStatus,
  };
}
