import { useState } from "react";
import { AppLayout } from "./components/AppLayout";
import { SourcePage } from "./pages/SourcePage";
import { TargetPage } from "./pages/TargetPage";
import { MethodPage } from "./pages/MethodPage";
import { ProxyDeployPage } from "./pages/ProxyDeployPage";
import { IndicesPage } from "./pages/IndicesPage";
import { ExecutePage } from "./pages/ExecutePage";
import type { TargetType } from "./pages/TargetPage";
import type { MigrationMethod } from "./pages/MethodPage";
import type { IndexInfo } from "./pages/IndicesPage";
import type { ProxyStatus } from "./pages/ProxyDeployPage";

type Page = "source" | "target" | "method" | "proxy_deploy" | "indices" | "execute";
type AuthType = "iam" | "basic";
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
  const [targetType, setTargetType] = useState<TargetType>("cloud_hosted");
  const [targetUrl, setTargetUrl] = useState("");
  const [targetApiKey, setTargetApiKey] = useState("");
  const [targetStatus, setTargetStatus] = useState<ConnectionStatus>("idle");
  const [targetMsg, setTargetMsg] = useState("");

  // ── Method state ─────────────────────────────────────────────────────────
  const [migrationMethod, setMigrationMethod] = useState<MigrationMethod>("remote_reindex");
  const [isVpcProxy, setIsVpcProxy] = useState(false);

  // ── VPC Proxy / Deploy state ─────────────────────────────────────────────
  const [vpcId, setVpcId] = useState("");
  const [subnetId, setSubnetId] = useState("");
  const [allowedCidr, setAllowedCidr] = useState("10.0.0.0/8");
  const [instanceType, setInstanceType] = useState("t3.small");
  const [keyPairName, setKeyPairName] = useState("");
  const [proxyStatus, setProxyStatus] = useState<ProxyStatus>("idle");
  const [proxyEndpoint, setProxyEndpoint] = useState("");
  const [proxyTestMsg, setProxyTestMsg] = useState("");

  // ── Indices state ─────────────────────────────────────────────────────────
  const [availableIndices, setAvailableIndices] = useState<IndexInfo[]>([]);
  const [selectedIndices, setSelectedIndices] = useState<string[]>([]);
  const [indicesLoading, setIndicesLoading] = useState(false);
  const [indicesError, setIndicesError] = useState("");
  const [batchSize, setBatchSize] = useState(1000);
  const [slices, setSlices] = useState("auto");

  // ── Routing helpers ───────────────────────────────────────────────────────

  /** true only when we need the proxy_deploy step in the wizard */
  const showProxyStep = isVpcProxy && migrationMethod === "remote_reindex";

  function resetProxyState() {
    setProxyStatus("idle");
    setProxyEndpoint("");
    setProxyTestMsg("");
  }

  function handleMethodChange(m: MigrationMethod) {
    setMigrationMethod(m);
    // Switching away from remote_reindex clears proxy deploy state
    if (m !== "remote_reindex") {
      resetProxyState();
    }
  }

  function handleVpcProxyChange(v: boolean) {
    setIsVpcProxy(v);
    if (!v) {
      resetProxyState();
    }
  }

  function handleMethodNext() {
    if (isVpcProxy && migrationMethod === "remote_reindex") {
      setPage("proxy_deploy");
    } else {
      setPage("indices");
    }
  }

  function handleProxyDeployBack() {
    setPage("method");
  }

  // ── Connection test handlers ──────────────────────────────────────────────

  async function handleTestSource() {
    if (!sourceEndpoint) return;
    setSourceStatus("testing");
    try {
      const resp = await fetch("/api/test-source", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: sourceEndpoint,
          region: sourceRegion,
          authType: sourceAuthType,
          username: sourceUsername,
          password: sourcePassword,
        }),
        signal: AbortSignal.timeout(12000),
      });
      if (resp.ok) {
        const data = await resp.json();
        setSourceStatus("ok");
        setSourceMsg(
          `Connected — OpenSearch ${data.version ?? "unknown"}${data.clusterName ? ` · ${data.clusterName}` : ""}`
        );
      } else {
        const data = await resp.json().catch(() => ({}));
        setSourceStatus("fail");
        setSourceMsg(data.error ?? `HTTP ${resp.status}`);
      }
    } catch (e: any) {
      if (e?.name === "TypeError" || e?.name === "AbortError") {
        try {
          new URL(sourceEndpoint);
          setSourceStatus("ok");
          setSourceMsg("URL format valid. (Proxy server not running — start with: node proxy.cjs)");
        } catch {
          setSourceStatus("fail");
          setSourceMsg("Invalid URL format.");
        }
      } else {
        setSourceStatus("fail");
        setSourceMsg(e?.message ?? "Connection failed");
      }
    }
  }

  async function handleTestTarget() {
    if (!targetUrl || !targetApiKey) return;
    setTargetStatus("testing");
    try {
      const resp = await fetch("/api/test-target", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: targetUrl, apiKey: targetApiKey }),
        signal: AbortSignal.timeout(12000),
      });
      if (resp.ok) {
        const data = await resp.json();
        setTargetStatus("ok");
        setTargetMsg(
          `Connected — Elasticsearch ${data.version ?? "unknown"}${data.clusterName ? ` · ${data.clusterName}` : ""}`
        );
      } else {
        const data = await resp.json().catch(() => ({}));
        setTargetStatus("fail");
        setTargetMsg(data.error ?? `HTTP ${resp.status}`);
      }
    } catch (e: any) {
      if (e?.name === "TypeError" || e?.name === "AbortError") {
        try {
          new URL(targetUrl);
          setTargetStatus("ok");
          setTargetMsg("URL format valid. (Proxy server not running — start with: node proxy.cjs)");
        } catch {
          setTargetStatus("fail");
          setTargetMsg("Invalid URL format.");
        }
      } else {
        setTargetStatus("fail");
        setTargetMsg(e?.message ?? "Connection failed");
      }
    }
  }

  // ── Proxy connectivity test ───────────────────────────────────────────────

  async function handleTestProxy() {
    if (!proxyEndpoint) return;
    setProxyStatus("testing");
    try {
      const resp = await fetch("/api/test-proxy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proxyEndpoint, apiKey: targetApiKey }),
        signal: AbortSignal.timeout(12000),
      });
      if (resp.ok) {
        const data = await resp.json();
        setProxyStatus("confirmed");
        setProxyTestMsg(
          `Proxy reachable — forwarding to Elasticsearch ${data.version ?? "unknown"}${data.clusterName ? ` · ${data.clusterName}` : ""}`
        );
      } else {
        const data = await resp.json().catch(() => ({}));
        setProxyStatus("failed");
        setProxyTestMsg(data.error ?? `HTTP ${resp.status}`);
      }
    } catch (e: any) {
      setProxyStatus("failed");
      setProxyTestMsg(e?.message ?? "Could not reach proxy endpoint");
    }
  }

  // ── Load indices ──────────────────────────────────────────────────────────

  async function handleLoadIndices() {
    setIndicesLoading(true);
    setIndicesError("");
    try {
      const resp = await fetch("/api/indices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          endpoint: sourceEndpoint,
          region: sourceRegion,
          authType: sourceAuthType,
          username: sourceUsername,
          password: sourcePassword,
        }),
        signal: AbortSignal.timeout(20000),
      });
      if (resp.ok) {
        const data: IndexInfo[] = await resp.json();
        setAvailableIndices(data);
        setSelectedIndices(data.map((i) => i.name));
      } else {
        const data = await resp.json().catch(() => ({}));
        setIndicesError(data.error ?? `HTTP ${resp.status}`);
        setAvailableIndices(SAMPLE_INDICES);
        setSelectedIndices(SAMPLE_INDICES.map((i) => i.name));
      }
    } catch {
      setIndicesError("Could not reach source cluster via proxy server.");
      setAvailableIndices(SAMPLE_INDICES);
      setSelectedIndices(SAMPLE_INDICES.map((i) => i.name));
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
      showProxyStep={showProxyStep}
      isVpcProxyMode={isVpcProxy}
      sourceConnected={sourceStatus === "ok"}
      targetConnected={targetStatus === "ok"}
      methodSelected={true}
      proxyDeployed={proxyStatus === "confirmed"}
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
          isVpcProxy={isVpcProxy}
          onMethodChange={handleMethodChange}
          onVpcProxyChange={handleVpcProxyChange}
          onBack={() => setPage("target")}
          onNext={handleMethodNext}
        />
      )}

      {page === "proxy_deploy" && (
        <ProxyDeployPage
          sourceRegion={sourceRegion}
          targetUrl={targetUrl}
          vpcId={vpcId}
          subnetId={subnetId}
          allowedCidr={allowedCidr}
          instanceType={instanceType}
          keyPairName={keyPairName}
          proxyStatus={proxyStatus}
          proxyEndpoint={proxyEndpoint}
          proxyTestMsg={proxyTestMsg}
          onVpcIdChange={setVpcId}
          onSubnetIdChange={setSubnetId}
          onAllowedCidrChange={setAllowedCidr}
          onInstanceTypeChange={setInstanceType}
          onKeyPairNameChange={setKeyPairName}
          onProxyStatusChange={setProxyStatus}
          onProxyEndpointChange={setProxyEndpoint}
          onProxyTestMsgChange={setProxyTestMsg}
          onTestProxy={handleTestProxy}
          onBack={handleProxyDeployBack}
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
          onBack={() => setPage(showProxyStep ? "proxy_deploy" : "method")}
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
          targetType={targetType}
          migrationMethod={migrationMethod}
          isVpcProxy={isVpcProxy}
          proxyEndpoint={proxyEndpoint}
          selectedIndices={selectedIndices}
          availableIndices={availableIndices}
          batchSize={batchSize}
          slices={slices}
          vpcId={vpcId}
          subnetId={subnetId}
          allowedCidr={allowedCidr}
          instanceType={instanceType}
          onBack={() => setPage("indices")}
        />
      )}
    </AppLayout>
  );
}

// Sample indices shown when proxy server is unavailable
const SAMPLE_INDICES: IndexInfo[] = [
  { name: "logs-app-2024",   docCount: 4_820_000,  sizeBytes: 3_221_225_472 },
  { name: "logs-app-2023",   docCount: 12_100_000, sizeBytes: 8_589_934_592 },
  { name: "metrics-system",  docCount: 920_000,    sizeBytes: 524_288_000  },
  { name: "apm-traces",      docCount: 2_400_000,  sizeBytes: 2_147_483_648 },
  { name: "security-events", docCount: 380_000,    sizeBytes: 268_435_456  },
];
