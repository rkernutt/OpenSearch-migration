/**
 * Migration GUI — backend proxy server
 *
 * Runs on port 5175 (proxied by Vite / nginx at /api/*).
 * Provides three endpoints used by the wizard:
 *
 *   POST /api/test-source   — probe an OpenSearch cluster
 *   POST /api/test-target   — probe an Elasticsearch cluster
 *   POST /api/indices       — list indices from an OpenSearch cluster
 *   GET  /health            — health check
 */

"use strict";

const http = require("http");
const https = require("https");

const PORT = Number(process.env.PROXY_PORT) || 5175;
const HOST = process.env.PROXY_HOST || "127.0.0.1";
const TIMEOUT_MS = 10_000;

// ── Helpers ───────────────────────────────────────────────────────────────────

function sendJson(res, statusCode, body) {
  const payload = JSON.stringify(body);
  res.writeHead(statusCode, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
  });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString()));
      } catch {
        resolve({});
      }
    });
    req.on("error", reject);
  });
}

/**
 * Make an HTTPS/HTTP GET request to an Elasticsearch-compatible endpoint.
 * Returns { ok, status, body } where body is a parsed JSON object.
 */
function esGet(rawUrl, path, headers) {
  return new Promise((resolve) => {
    let parsed;
    try {
      parsed = new URL(rawUrl);
    } catch {
      return resolve({ ok: false, status: 0, body: { error: "Invalid URL" } });
    }

    const transport = parsed.protocol === "https:" ? https : http;
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
      path: path,
      method: "GET",
      headers: {
        Accept: "application/json",
        ...headers,
      },
      timeout: TIMEOUT_MS,
    };

    const req = transport.request(options, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try {
          const body = JSON.parse(Buffer.concat(chunks).toString());
          resolve({
            ok: res.statusCode >= 200 && res.statusCode < 300,
            status: res.statusCode,
            body,
          });
        } catch {
          resolve({ ok: false, status: res.statusCode, body: { error: "Non-JSON response" } });
        }
      });
    });

    req.on("timeout", () => {
      req.destroy();
      resolve({
        ok: false,
        status: 0,
        body: { error: "Connection timed out after " + TIMEOUT_MS / 1000 + "s" },
      });
    });

    req.on("error", (err) => {
      resolve({ ok: false, status: 0, body: { error: err.message } });
    });

    req.end();
  });
}

function buildBasicAuth(username, password) {
  return "Basic " + Buffer.from(`${username}:${password}`).toString("base64");
}

// ── Route handlers ────────────────────────────────────────────────────────────

/**
 * POST /api/test-source
 * body: { endpoint, region, authType, username, password }
 */
async function handleTestSource(req, res) {
  const body = await readBody(req);
  const { endpoint, authType, username, password } = body;

  if (!endpoint) {
    return sendJson(res, 400, { error: "endpoint is required" });
  }

  const headers = {};
  if (authType === "basic" && username) {
    headers["Authorization"] = buildBasicAuth(username, password || "");
  }
  // IAM/SigV4 signing is not performed here (requires AWS credentials in the
  // runtime environment). The UI falls back to format validation for IAM mode.

  const { ok, status, body: respBody } = await esGet(endpoint, "/", headers);

  if (ok) {
    const version = respBody.version?.number ?? respBody.version ?? "unknown";
    const clusterName = respBody.cluster_name ?? "";
    return sendJson(res, 200, { version, clusterName });
  }

  const errorMsg =
    respBody?.error?.reason ?? respBody?.error ?? respBody?.message ?? `HTTP ${status}`;
  sendJson(res, status || 502, { error: String(errorMsg) });
}

/**
 * POST /api/test-target
 * body: { url, apiKey }
 */
async function handleTestTarget(req, res) {
  const body = await readBody(req);
  const { url, apiKey } = body;

  if (!url || !apiKey) {
    return sendJson(res, 400, { error: "url and apiKey are required" });
  }

  const headers = { Authorization: `ApiKey ${apiKey}` };
  const { ok, status, body: respBody } = await esGet(url, "/", headers);

  if (ok) {
    const version = respBody.version?.number ?? "unknown";
    const clusterName = respBody.cluster_name ?? "";
    return sendJson(res, 200, { version, clusterName });
  }

  const errorMsg = respBody?.error?.reason ?? respBody?.error ?? `HTTP ${status}`;
  sendJson(res, status || 502, { error: String(errorMsg) });
}

/**
 * POST /api/indices
 * body: { endpoint, region, authType, username, password }
 * Returns: Array<{ name: string; docCount: number; sizeBytes: number }>
 */
async function handleIndices(req, res) {
  const body = await readBody(req);
  const { endpoint, authType, username, password } = body;

  if (!endpoint) {
    return sendJson(res, 400, { error: "endpoint is required" });
  }

  const headers = {};
  if (authType === "basic" && username) {
    headers["Authorization"] = buildBasicAuth(username, password || "");
  }

  // _cat/indices gives us name + doc count + store size
  const {
    ok,
    status,
    body: respBody,
  } = await esGet(
    endpoint,
    "/_cat/indices?format=json&bytes=b&h=index,docs.count,store.size",
    headers,
  );

  if (!ok) {
    const errorMsg = respBody?.error?.reason ?? respBody?.error ?? `HTTP ${status}`;
    return sendJson(res, status || 502, { error: String(errorMsg) });
  }

  // Filter out system indices (starting with .)
  const indices = (Array.isArray(respBody) ? respBody : [])
    .filter((row) => !row.index?.startsWith("."))
    .map((row) => ({
      name: row.index ?? "",
      docCount: parseInt(row["docs.count"] ?? "0", 10) || 0,
      sizeBytes: parseInt(row["store.size"] ?? "0", 10) || 0,
    }))
    .sort((a, b) => a.name.localeCompare(b.name));

  sendJson(res, 200, indices);
}

/**
 * POST /api/test-proxy
 * body: { proxyEndpoint, apiKey }
 * Tests that the VPC proxy can reach the target Elasticsearch cluster.
 */
async function handleTestProxy(req, res) {
  const body = await readBody(req);
  const { proxyEndpoint, apiKey } = body;

  if (!proxyEndpoint) {
    return sendJson(res, 400, { error: "proxyEndpoint is required" });
  }

  const headers = apiKey ? { Authorization: `ApiKey ${apiKey}` } : {};
  const { ok, status, body: respBody } = await esGet(proxyEndpoint, "/", headers);

  if (ok) {
    const version = respBody.version?.number ?? "unknown";
    const clusterName = respBody.cluster_name ?? "";
    return sendJson(res, 200, { version, clusterName });
  }

  const errorMsg = respBody?.error?.reason ?? respBody?.error ?? `HTTP ${status}`;
  sendJson(res, status || 502, { error: String(errorMsg) });
}

// ── Server ────────────────────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  // CORS preflight
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
    });
    return res.end();
  }

  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200);
    return res.end("ok");
  }

  if (req.method === "POST") {
    try {
      if (req.url === "/api/test-source") return await handleTestSource(req, res);
      if (req.url === "/api/test-target") return await handleTestTarget(req, res);
      if (req.url === "/api/indices") return await handleIndices(req, res);
      if (req.url === "/api/test-proxy") return await handleTestProxy(req, res);
    } catch (err) {
      return sendJson(res, 500, { error: err.message || "Internal server error" });
    }
  }

  sendJson(res, 404, { error: "Not found" });
});

server.listen(PORT, HOST, () => {
  console.log(`[migration-proxy] listening on ${HOST}:${PORT} (timeout ${TIMEOUT_MS / 1000}s)`);
});
