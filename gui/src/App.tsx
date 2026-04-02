import { useState } from "react";
import { AppLayout } from "./components/AppLayout";
import { SourcePage } from "./pages/SourcePage";
import { TargetPage } from "./pages/TargetPage";
import { MethodPage } from "./pages/MethodPage";
import { IndicesPage } from "./pages/IndicesPage";
import { ExecutePage } from "./pages/ExecutePage";
import type { MigrationMethod } from "./pages/MethodPage";
import type { IndexInfo } from "./pages/IndicesPage";

type Page = "source" | "target" | "method" | "indices" | "execute";
type AuthType = "iam" | "basic";
type TargetType = "cloud" | "self_managed";
type ConnectionStatus = "idle" | "testing" | "ok" | "fail";

export default function App() {
  const [page, setPage] = useState<Page>("source");

  // ── Source state ─────────────────────────────────────────────────────────
  const [sourceEndpoint, setSourceEndpoint] = useState("");
  const [sourceRegion, setSourceRegion] = useState("us-east-1");
  const [sourceAuthType, setSourceAuthType] = useState<AuthType>("iam");
  const [sourceUsername, setSourceUsername] = useState("");
  const [sourcePassword, setSourcePassword] = useState("");
  const [sourceStatus, setSourceStatus] = useState<ConnectionStatus>("idle");
  const [sourceMsg, setSourceMsg] = useState("");

  // ── Target state ─────────────────────────────────────────────────────────
  const [targetType, setTargetType] = useState<TargetType>("cloud");
  const [targetUrl, setTargetUrl] = useState("");
  const [targetApiKey, setTargetApiKey] = useState("");
  const [targetStatus, setTargetStatus] = useState<ConnectionStatus>("idle");
  const [targetMsg, setTargetMsg] = useState("");

  // ── Method state ─────────────────────────────────────────────────────────
  const [migrationMethod, setMigrationMethod] = useState<MigrationMethod>("remote_reindex");

  // ── Indices state ─────────────────────────────────────────────────────────
  const [availableIndices, setAvailableIndices] = useState<IndexInfo[]>([]);
  const [selectedIndices, setSelectedIndices] = useState<string[]>([]);
  const [indicesLoading, setIndicesLoading] = useState(false);
  const [indicesError, setIndicesError] = useState("");
  const [batchSize, setBatchSize] = useState(1000);
  const [slices, setSlices] = useState("auto");

  // ── Connection test handlers ──────────────────────────────────────────────

  async function handleTestSource() {
    if (!sourceEndpoint) return;
    setSourceStatus("testing");
    try {
      // Attempt a lightweight call through /api proxy; fall back to format validation
      const url = new URL(sourceEndpoint);
      const resp = await fetch(`/api/test-source`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: url.href,
          region: sourceRegion,
          authType: sourceAuthType,
          username: sourceUsername,
          password: sourcePassword,
        }),
        signal: AbortSignal.timeout(8000),
      });
      if (resp.ok) {
        const data = await resp.json();
        setSourceStatus("ok");
        setSourceMsg(
          `Connected — OpenSearch ${data.version ?? "unknown"} · ${data.clusterName ?? ""}`
        );
      } else {
        const text = await resp.text();
        setSourceStatus("fail");
        setSourceMsg(text || `HTTP ${resp.status}`);
      }
    } catch (e: any) {
      if (e?.name === "TypeError" && e.message?.includes("fetch")) {
        // Backend proxy not running — validate URL format only
        try {
          new URL(sourceEndpoint);
          setSourceStatus("ok");
          setSourceMsg(
            "URL format valid. (Backend proxy not running — connection not verified.)"
          );
        } catch {
          setSourceStatus("fail");
          setSourceMsg("Invalid URL format.");
        }
      } else {
        setSourceStatus("fail");
        setSourceMsg(e?.message ?? "Connection timed out");
      }
    }
  }

  async function handleTestTarget() {
    if (!targetUrl || !targetApiKey) return;
    setTargetStatus("testing");
    try {
      const resp = await fetch(`/api/test-target`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: targetUrl, apiKey: targetApiKey }),
        signal: AbortSignal.timeout(8000),
      });
      if (resp.ok) {
        const data = await resp.json();
        setTargetStatus("ok");
        setTargetMsg(
          `Connected — Elasticsearch ${data.version ?? "unknown"} · ${data.clusterName ?? ""}`
        );
      } else {
        const text = await resp.text();
        setTargetStatus("fail");
        setTargetMsg(text || `HTTP ${resp.status}`);
      }
    } catch (e: any) {
      if (e?.name === "TypeError" && e.message?.includes("fetch")) {
        try {
          new URL(targetUrl);
          setTargetStatus("ok");
          setTargetMsg(
            "URL format valid. (Backend proxy not running — connection not verified.)"
          );
        } catch {
          setTargetStatus("fail");
          setTargetMsg("Invalid URL format.");
        }
      } else {
        setTargetStatus("fail");
        setTargetMsg(e?.message ?? "Connection timed out");
      }
    }
  }

  // ── Load indices ──────────────────────────────────────────────────────────

  async function handleLoadIndices() {
    setIndicesLoading(true);
    setIndicesError("");
    try {
      const resp = await fetch(`/api/indices`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: sourceEndpoint,
          region: sourceRegion,
          authType: sourceAuthType,
          username: sourceUsername,
          password: sourcePassword,
        }),
        signal: AbortSignal.timeout(15000),
      });
      if (resp.ok) {
        const data: IndexInfo[] = await resp.json();
        setAvailableIndices(data);
        setSelectedIndices(data.map((i) => i.name));
      } else {
        const text = await resp.text();
        setIndicesError(text || `HTTP ${resp.status}`);
        // Provide sample data so the UI is still usable
        setAvailableIndices(SAMPLE_INDICES);
      }
    } catch {
      setIndicesError("Could not reach source cluster via backend proxy.");
      setAvailableIndices(SAMPLE_INDICES);
    } finally {
      setIndicesLoading(false);
    }
  }

  function handleToggleIndex(name: string) {
    setSelectedIndices((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
    );
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <AppLayout
      activePage={page}
      onNavigate={(p) => setPage(p as Page)}
      sourceConnected={sourceStatus === "ok"}
      targetConnected={targetStatus === "ok"}
      methodSelected={true}
      indicesSelected={selectedIndices.length > 0}
    >
      {page === "source" && (
        <SourcePage
          sourceEndpoint={sourceEndpoint}
          sourceRegion={sourceRegion}
          sourceAuthType={sourceAuthType}
          sourceUsername={sourceUsername}
          sourcePassword={sourcePassword}
          connectionStatus={sourceStatus}
          connectionMsg={sourceMsg}
          onEndpointChange={setSourceEndpoint}
          onRegionChange={setSourceRegion}
          onAuthTypeChange={setSourceAuthType}
          onUsernameChange={setSourceUsername}
          onPasswordChange={setSourcePassword}
          onTestConnection={handleTestSource}
          onNext={() => setPage("target")}
        />
      )}

      {page === "target" && (
        <TargetPage
          targetType={targetType}
          targetUrl={targetUrl}
          targetApiKey={targetApiKey}
          connectionStatus={targetStatus}
          connectionMsg={targetMsg}
          onTargetTypeChange={setTargetType}
          onUrlChange={setTargetUrl}
          onApiKeyChange={setTargetApiKey}
          onTestConnection={handleTestTarget}
          onBack={() => setPage("source")}
          onNext={() => setPage("method")}
        />
      )}

      {page === "method" && (
        <MethodPage
          method={migrationMethod}
          onMethodChange={setMigrationMethod}
          onBack={() => setPage("target")}
          onNext={() => setPage("indices")}
        />
      )}

      {page === "indices" && (
        <IndicesPage
          availableIndices={availableIndices}
          selectedIndices={selectedIndices}
          indicesLoading={indicesLoading}
          indicesError={indicesError}
          batchSize={batchSize}
          slices={slices}
          onLoadIndices={handleLoadIndices}
          onToggleIndex={handleToggleIndex}
          onSelectAll={() => setSelectedIndices(availableIndices.map((i) => i.name))}
          onClearAll={() => setSelectedIndices([])}
          onBatchSizeChange={setBatchSize}
          onSlicesChange={setSlices}
          onBack={() => setPage("method")}
          onNext={() => setPage("execute")}
        />
      )}

      {page === "execute" && (
        <ExecutePage
          sourceEndpoint={sourceEndpoint}
          sourceRegion={sourceRegion}
          sourceAuthType={sourceAuthType}
          sourceUsername={sourceUsername}
          sourcePassword={sourcePassword}
          targetUrl={targetUrl}
          targetApiKey={targetApiKey}
          migrationMethod={migrationMethod}
          selectedIndices={selectedIndices}
          availableIndices={availableIndices}
          batchSize={batchSize}
          slices={slices}
          onBack={() => setPage("indices")}
        />
      )}
    </AppLayout>
  );
}

// Sample indices shown when backend proxy is unavailable
const SAMPLE_INDICES: IndexInfo[] = [
  { name: "logs-app-2024", docCount: 4_820_000, sizeBytes: 3_221_225_472 },
  { name: "logs-app-2023", docCount: 12_100_000, sizeBytes: 8_589_934_592 },
  { name: "metrics-system", docCount: 920_000, sizeBytes: 524_288_000 },
  { name: "apm-traces", docCount: 2_400_000, sizeBytes: 2_147_483_648 },
  { name: "security-events", docCount: 380_000, sizeBytes: 268_435_456 },
];
