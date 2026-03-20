"""
HTTP reverse proxy for Amazon OpenSearch Service (VPC endpoint).
Accepts OpenSearch-style API requests, signs them with SigV4, and forwards to the VPC endpoint.
Optional basic auth on the proxy for public exposure (e.g. Elastic Cloud reindex).
"""
from pathlib import Path
import os
import sys

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
from requests_aws4auth import AWS4Auth
from flask import Flask, Request, request, Response, stream_with_context

app = Flask(__name__)

OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "").rstrip("/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
PROXY_USER = os.environ.get("PROXY_USER", "")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")

# Headers we forward from client to OpenSearch (lowercase keys)
FORWARD_REQUEST_HEADERS = {"content-type", "accept", "accept-encoding"}
# Headers we forward from OpenSearch response to client
FORWARD_RESPONSE_HEADERS = {"content-type", "content-length", "accept-ranges"}


def _get_sigv4_auth():
    credentials = boto3.Session().get_credentials()
    return AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        AWS_REGION,
        "es",
        session_token=credentials.token,
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
        )
    except requests.RequestException as e:
        return str(e), 502

    response_headers = _forward_headers_from_response(resp)

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
