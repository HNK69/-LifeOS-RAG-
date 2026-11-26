import React, { useEffect, useState } from "react";
import { Header } from "./components/Header";
import { ConsentPrompt, DiscoveredSource } from "./components/ConsentPrompt";
import { useLifeOS } from "./hooks/useLifeOS";
import { useEventStream } from "./hooks/useEventStream";
import { WorkspaceProfile } from "./api/types";
import { QueryView } from "./views/QueryView";
import { PrivacyView } from "./views/PrivacyView";
import { SyncView } from "./views/SyncView";
import { WorkspacesView } from "./views/WorkspacesView";
import { DiagnosticsView } from "./views/DiagnosticsView";

export type AppTab = "query" | "privacy" | "sync" | "workspaces" | "diagnostics";

export interface AppProps {
  initialTab?: AppTab;
  initialWorkspaces?: WorkspaceProfile[];
  initialActiveWorkspaceId?: string;
  initialDiscovery?: DiscoveredSource | null;
}

export interface ErrorBoundaryProps {
  viewName?: string;
  onReset?: () => void;
  children?: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  errorMessage: string;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, errorMessage: "" };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      errorMessage: error?.message || "An unexpected rendering error occurred.",
    };
  }

  componentDidCatch(_error: Error, _errorInfo: React.ErrorInfo): void {
    // Captured by boundary state for safe fallback rendering
  }

  handleReset = () => {
    this.setState({ hasError: false, errorMessage: "" });
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          data-testid="view-error-boundary"
          style={{
            backgroundColor: "#fef2f2",
            border: "1px solid #fecaca",
            borderRadius: "8px",
            padding: "20px",
            color: "#991b1b",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
            alignItems: "flex-start",
          }}
        >
          <div style={{ fontSize: "15px", fontWeight: 700 }}>
            {this.props.viewName ? `${this.props.viewName} encountered an error` : "View encountered an error"}
          </div>
          <div style={{ fontSize: "13px", color: "#7f1d1d" }}>{this.state.errorMessage}</div>
          <button
            type="button"
            onClick={this.handleReset}
            style={{
              backgroundColor: "#dc2626",
              color: "#ffffff",
              border: "none",
              borderRadius: "6px",
              padding: "7px 14px",
              fontSize: "12px",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Reset View
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

const NAV_TABS: Array<{ id: AppTab; label: string }> = [
  { id: "query", label: "Query" },
  { id: "privacy", label: "Privacy" },
  { id: "sync", label: "Sync" },
  { id: "workspaces", label: "Workspaces" },
  { id: "diagnostics", label: "Diagnostics" },
];

export const App: React.FC<AppProps> = ({
  initialTab = "query",
  initialWorkspaces = [],
  initialActiveWorkspaceId = "default",
  initialDiscovery = null,
}) => {
  const [baseUrl] = useState("http://127.0.0.1:8000");
  const {
    client,
    sidecarState,
    health,
    readiness,
    error,
    refresh,
    baseUrl: activeBaseUrl,
    authToken,
  } = useLifeOS(baseUrl);
  const { connectionState: sseState, events, clearEvents } = useEventStream(activeBaseUrl, authToken);
  const [activeDiscovery, setActiveDiscovery] = useState<DiscoveredSource | null>(initialDiscovery);

  const [activeTab, setActiveTab] = useState<AppTab>(initialTab);
  const [workspaces, setWorkspaces] = useState<WorkspaceProfile[]>(initialWorkspaces);
  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string>(initialActiveWorkspaceId);

  // Load workspaces for Header Workspace Switcher & views
  useEffect(() => {
    let mounted = true;
    client
      .listWorkspaces()
      .then((list) => {
        if (mounted && Array.isArray(list)) {
          setWorkspaces(list);
        }
      })
      .catch(() => {
        // Backend may still be starting; views can refresh later
      });
    return () => {
      mounted = false;
    };
  }, [client]);

  // Detect incoming discovery events from real-time event stream
  useEffect(() => {
    const latestDiscovery = events.find((e) => e.event_type === "privacy.discovery_detected");
    if (latestDiscovery && latestDiscovery.payload) {
      setActiveDiscovery({
        resource_path: latestDiscovery.payload.resource_path || "",
        scope_type: latestDiscovery.payload.scope_type || "folder",
        suggested_name: latestDiscovery.payload.suggested_name || "Discovered Folder",
        file_count: latestDiscovery.payload.file_count || 0,
        discovered_at: latestDiscovery.payload.discovered_at || latestDiscovery.timestamp,
      });
    }
  }, [events]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100vh",
        backgroundColor: "#f8fafc",
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
      }}
    >
      <Header
        sidecarState={sidecarState}
        sseState={sseState}
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onWorkspaceChange={setActiveWorkspaceId}
      />

      {/* Tab Navigation Bar */}
      <nav
        aria-label="Primary Navigation"
        style={{
          display: "flex",
          alignItems: "center",
          gap: "6px",
          padding: "8px 20px",
          backgroundColor: "#ffffff",
          borderBottom: "1px solid #e2e8f0",
        }}
      >
        {NAV_TABS.map((tab) => {
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setActiveTab(tab.id)}
              style={{
                backgroundColor: isActive ? "#2563eb" : "transparent",
                color: isActive ? "#ffffff" : "#475569",
                border: isActive ? "1px solid #2563eb" : "1px solid transparent",
                borderRadius: "6px",
                padding: "6px 14px",
                fontSize: "13px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </nav>

      <main style={{ flex: 1, padding: "20px", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* Consent Prompt Banner (Top - preserved across all tabs) */}
        <ConsentPrompt
          client={client}
          discovery={activeDiscovery}
          onDismiss={() => setActiveDiscovery(null)}
          onAuthorized={() => {
            setActiveDiscovery(null);
            refresh();
          }}
        />

        {activeTab === "query" && (
          <ErrorBoundary key="query" viewName="Query & Intelligence">
            <QueryView
              client={client}
              workspaces={workspaces}
              activeWorkspaceId={activeWorkspaceId}
              onWorkspaceChange={setActiveWorkspaceId}
            />
          </ErrorBoundary>
        )}

        {activeTab === "privacy" && (
          <ErrorBoundary key="privacy" viewName="Privacy Dashboard">
            <PrivacyView client={client} />
          </ErrorBoundary>
        )}

        {activeTab === "sync" && (
          <ErrorBoundary key="sync" viewName="Cross-Device Sync">
            <SyncView client={client} events={events} activeWorkspaceId={activeWorkspaceId} />
          </ErrorBoundary>
        )}

        {activeTab === "workspaces" && (
          <ErrorBoundary key="workspaces" viewName="Workspaces">
            <WorkspacesView
              client={client}
              workspaces={workspaces}
              activeWorkspaceId={activeWorkspaceId}
              onSelectWorkspace={setActiveWorkspaceId}
              onWorkspacesUpdated={setWorkspaces}
            />
          </ErrorBoundary>
        )}

        {activeTab === "diagnostics" && (
          <ErrorBoundary key="diagnostics" viewName="Diagnostics">
            <DiagnosticsView
              client={client}
              sidecarState={sidecarState}
              health={health}
              readiness={readiness}
              connectionError={error}
              onRefreshSidecar={refresh}
              events={events}
              onClearEvents={clearEvents}
            />
          </ErrorBoundary>
        )}
      </main>
    </div>
  );
};
