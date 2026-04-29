"""
HTTP reverse proxy for Amazon OpenSearch Service (VPC endpoint).
Accepts OpenSearch-style API requests, signs them with SigV4, and forwards to the VPC endpoint.
Optional basic auth on the proxy for public exposure (e.g. Elastic Cloud reindex).
Optional request/response capture for cutover replay (see ``Proxy/capture.py``).

Environment variables:
  OPENSEARCH_ENDPOINT          Required. VPC endpoint URL.
  AWS_REGION                   AWS region (default: us-east-1).
  PROXY_USER                   Optional basic-auth username for inbound requests.
  PROXY_PASSWORD               Optional basic-auth password for inbound requests.
  PROXY_MAX_BODY_MB            Max request body size in MB (default: 100).
  PROXY_VERIFY_TLS             Set to "false" to disable TLS verification (default: true).
  PROXY_CA_BUNDLE              Path to a CA bundle file for TLS verification.
  PROXY_DEBUG                  Set to "1" to log method/path/status/latency (no bodies).
  PROXY_LISTEN                 host:port to bind (default: 0.0.0.0:9200).

Capture (optional; off unless PROXY_CAPTURE_MODE is set):
  PROXY_CAPTURE_MODE           "off" (default), "local", or "s3".
  PROXY_CAPTURE_DIR            Local directory when mode=local.
  PROXY_CAPTURE_S3_URI         s3://bucket/prefix when mode=s3.
  PROXY_CAPTURE_INCLUDE_BODIES "true" (default) or "false".
  PROXY_CAPTURE_MAX_BODY_BYTES Inline cap for bodies (default: 1048576).
  PROXY_CAPTURE_PATH_INCLUDE   Comma-separated regex (default: ".*").
  PROXY_CAPTURE_PATH_EXCLUDE   Comma-separated regex (e.g. "_cluster/.*,_nodes/.*").
  PROXY_CAPTURE_METHODS        Comma-separated methods (default: "GET,POST,HEAD").
"""

import logging
import os
import sys
import time
from pathlib import Path

_root = Path(__file__).resolve().parents[1]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
try:
    import bootstrap_env

    bootstrap_env.load()
except ImportError:
    pass

import boto3
import requests
from flask import Flask, Request, Response, request, stream_with_context
from requests_aws4auth import AWS4Auth

app = Flask(__name__)

# Limit request body size (bulk requests); override via PROXY_MAX_BODY_MB (default 100).
_max_body_mb = int(os.environ.get("PROXY_MAX_BODY_MB", "100"))
app.config["MAX_CONTENT_LENGTH"] = max(1, _max_body_mb) * 1024 * 1024

OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "").rstrip("/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")

# TLS verification: PROXY_VERIFY_TLS=false disables; PROXY_CA_BUNDLE sets a custom CA bundle.
_verify_tls_env = os.environ.get("PROXY_VERIFY_TLS", "true").lower()
if _verify_tls_env in ("0", "false", "no"):
    PROXY_TLS_VERIFY: object = False
elif os.environ.get("PROXY_CA_BUNDLE"):
    PROXY_TLS_VERIFY = os.environ["PROXY_CA_BUNDLE"]
else:
    PROXY_TLS_VERIFY = True

PROXY_DEBUG = os.environ.get("PROXY_DEBUG", "0") in ("1", "true", "yes")

# Optional capture. Sibling module — make sure Proxy/ is on sys.path so this
# file can be run either as `python Proxy/app.py` or `python -m Proxy.app`.
_proxy_dir = str(Path(__file__).resolve().parent)
if _proxy_dir not in sys.path:
    sys.path.insert(0, _proxy_dir)
from capture import CaptureConfig, Capturer, make_record  # noqa: E402

_capture_cfg = CaptureConfig.from_env()
_capturer: "Capturer | None" = None
if _capture_cfg.mode != "off":
    _capturer = Capturer(_capture_cfg)
    _capturer.start()

# Headers we forward from client to OpenSearch (lowercase keys)
FORWARD_REQUEST_HEADERS = {"content-type", "accept", "accept-encoding"}
# Headers we forward from OpenSearch response to client
FORWARD_RESPONSE_HEADERS = {"content-type", "content-length", "accept-ranges"}

if PROXY_DEBUG:
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
    _log = logging.getLogger("proxy")
else:
    _log = logging.getLogger("proxy")


def _get_sigv4_auth() -> AWS4Auth:
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise ValueError(
            "AWS credentials not found. Set AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, "
            "assign an IAM role, or configure an AWS profile."
        )
    resolved = credentials.resolve()
    return AWS4Auth(
        resolved.access_key,
        resolved.secret_key,
        AWS_REGION,
        "es",
        session_token=resolved.token,
    )


def _check_proxy_auth(req: Request) -> bool:
    if not PROXY_USER and not PROXY_PASSWORD:
        return True
    auth = req.authorization
    if not auth or auth.username != PROXY_USER or auth.password != PROXY_PASSWORD:
        return False
    return True


def _build_target_url(path: str, query_string: bytes) -> str:
    path = path or ""
    if path.startswith("/"):
        path = path[1:]
    url = f"{OPENSEARCH_ENDPOINT}/{path}" if path else OPENSEARCH_ENDPOINT + "/"
    if query_string:
        url += "?" + query_string.decode("utf-8", errors="replace")
    return url


def _forward_headers_from_request(req: Request) -> dict:
    out = {}
    for k, v in req.headers:
        if k.lower() in FORWARD_REQUEST_HEADERS and v:
            out[k] = v
    return out


def _forward_headers_from_response(resp: requests.Response) -> list:
    out = []
    for k, v in resp.headers.items():
        if k.lower() in FORWARD_RESPONSE_HEADERS and v is not None:
            out.append((k, v))
    return out


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH"])
def proxy(path: str):
    if not OPENSEARCH_ENDPOINT:
        return "OPENSEARCH_ENDPOINT not configured", 500

    if not _check_proxy_auth(request):
        return Response("Unauthorized", 401, {"WWW-Authenticate": "Basic realm=proxy"})

    target_url = _build_target_url(path, request.query_string)
    headers = _forward_headers_from_request(request)
    body = request.get_data()

    auth = _get_sigv4_auth()
    stream = request.method in ("GET", "HEAD")

    t0 = time.monotonic()
    try:
        resp = requests.request(
            request.method,
            target_url,
            auth=auth,
            headers=headers,
            data=body if body else None,
            stream=stream,
            timeout=60,
            allow_redirects=False,
            verify=PROXY_TLS_VERIFY,
        )
    except requests.RequestException as e:
        if PROXY_DEBUG:
            _log.debug("PROXY %s %s ERROR %s", request.method, path, e)
        return str(e), 502

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if PROXY_DEBUG:
        _log.debug("PROXY %s %s -> %s (%dms)", request.method, path, resp.status_code, elapsed_ms)

    response_headers = _forward_headers_from_response(resp)

    # Capture (best-effort, never blocks the proxy).
    if _capturer is not None and not stream:
        # We only capture non-streamed responses so we have the full body
        # bytes available; streamed GETs (typically large _search hits)
        # would otherwise need to buffer the whole response, defeating the
        # streaming optimisation.
        try:
            rec = make_record(
                method=request.method,
                path="/" + (path or ""),
                query_string=request.query_string.decode("utf-8", errors="replace"),
                request_headers={k: v for k, v in request.headers},
                request_body=body or b"",
                response_status=resp.status_code,
                response_headers=dict(resp.headers),
                response_body=resp.content,
                latency_ms=elapsed_ms,
                target_host=OPENSEARCH_ENDPOINT,
                include_bodies=_capture_cfg.include_bodies,
                max_body_bytes=_capture_cfg.max_body_bytes,
            )
            _capturer.record(rec)
        except Exception:  # noqa: BLE001
            # Capture must never break the proxy.
            if PROXY_DEBUG:
                _log.exception("capture record failed")

    if stream and resp.iter_content:

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(
            stream_with_context(generate()),
            status=resp.status_code,
            headers=response_headers,
        )
    return Response(resp.content, status=resp.status_code, headers=response_headers)


@app.route("/health", methods=["GET"])
def health():
    """Lightweight health check for ALB/load-balancer probes. No SigV4 required."""
    return {"status": "ok"}, 200


def main():
    listen = os.environ.get("PROXY_LISTEN", "0.0.0.0:9200")
    if ":" in listen:
        host, port = listen.rsplit(":", 1)
        port = int(port)
    else:
        host = listen
        port = 9200
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
