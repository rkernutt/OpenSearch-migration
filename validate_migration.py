#!/usr/bin/env python3
"""
Compare document counts between a source OpenSearch index and a destination Elastic
index after migration. Uses SigV4 for Amazon OpenSearch Service; API key or basic
auth for Elastic Cloud.

Batch mode: --indices or --indices-file with optional --dest-prefix / --dest-suffix.
Optional: --sample-size N to verify a sample of document IDs exist on the destination.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime
import io
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from requests_aws4auth import AWS4Auth
from urllib3.util.retry import Retry

try:
    import boto3
except ImportError:
    boto3 = None

# ---------------------------------------------------------------------------
# Configurable timeouts (override via environment variables)
# ---------------------------------------------------------------------------
_TIMEOUT_SHORT = int(os.environ.get("VALIDATION_TIMEOUT_SHORT", "30"))  # HEAD, GET _count
_TIMEOUT_SEARCH = int(os.environ.get("VALIDATION_TIMEOUT_SEARCH", "120"))  # POST _search, _mget


def _make_session() -> requests.Session:
    """
    Return a requests Session with exponential-backoff retry on transient errors.
    Retries on 429, 500, 502, 503, 504 for idempotent methods (GET, HEAD, POST).
    """
    retry = Retry(
        total=3,
        backoff_factor=1,  # 0s, 1s, 2s between attempts
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods={"GET", "HEAD", "POST"},
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_SESSION = _make_session()

# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------
_LOG_FORMAT_JSON = False  # set to True when --log-format=json is parsed


def _cli_log(level: str, message: str, **extra) -> None:
    """
    Emit a structured log line to stderr.
    Plain text by default; JSON when _LOG_FORMAT_JSON is True (set via --log-format=json).
    """
    if _LOG_FORMAT_JSON:
        record = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "level": level,
            "message": message,
        }
        record.update(extra)
        print(json.dumps(record), file=sys.stderr)
    else:
        suffix = "".join(f" {k}={v}" for k, v in extra.items())
        print(f"{level.upper()}: {message}{suffix}", file=sys.stderr)


@dataclass
class DestAuth:
    api_key: Optional[str] = None
    api_key_encoded: bool = False  # True = key is already Base64-encoded
    user: Optional[str] = None
    password: Optional[str] = None

    def apply(self, headers: Optional[dict] = None) -> Tuple[dict, Optional[tuple]]:
        """Return (headers, auth tuple) for requests."""
        h = dict(headers or {})
        a = None
        if self.api_key:
            key = self.api_key
            if not self.api_key_encoded:
                # Raw Elastic API keys are in id:secret format; Base64-encode them.
                # Base64 alphabet (A-Za-z0-9+/=) never contains a colon, so the
                # presence of ":" reliably identifies an unencoded key.
                if ":" in key:
                    key = base64.b64encode(key.encode()).decode()
            h["Authorization"] = f"ApiKey {key}"
        elif self.user and self.password:
            a = (self.user, self.password)
        return h, a


def build_sample_search_body(size: int, mode: str, random_seed: Optional[int] = None) -> dict:
    """
    Build _search body for ID sampling. mode: 'head' (sort _doc) or 'random' (function_score random_score).
    For stratified multi-request sampling, use build_stratified_slice_search_body.
    Exported for unit tests.
    """
    if mode == "random":
        rs: Dict[str, Any] = {}
        if random_seed is not None:
            rs["seed"] = random_seed
        else:
            rs["seed"] = 42
        return {
            "size": size,
            "query": {
                "function_score": {
                    "query": {"match_all": {}},
                    "random_score": rs,
                }
            },
            "_source": False,
        }
    if mode != "head":
        raise ValueError(f"unknown sample mode: {mode}")
    return {
        "size": size,
        "query": {"match_all": {}},
        "_source": False,
        "sort": ["_doc"],
    }


def build_stratified_slice_search_body(
    size: int, slice_id: int, max_slices: int, random_seed: int
) -> dict:
    """
    One slice of stratified sampling: random_score within a search slice (partitions the index for parallel search).
    """
    body = build_sample_search_body(size, "random", random_seed)
    body["slice"] = {"id": slice_id, "max": max_slices}
    return body


def effective_stratified_slice_count(requested: Optional[int], sample_total: int) -> int:
    """Cap slice count to sample size; default up to 8 slices."""
    if sample_total <= 0:
        return 1
    default = min(8, max(1, sample_total))
    k = requested if requested is not None else default
    return max(1, min(k, sample_total))


def distribute_sample_sizes(total: int, num_slices: int) -> List[int]:
    """Split total across num_slices (each gets floor/ceil so sums to total)."""
    if num_slices < 1:
        raise ValueError("num_slices must be >= 1")
    if total < 0:
        raise ValueError("total must be >= 0")
    base = total // num_slices
    rem = total % num_slices
    return [base + (1 if i < rem else 0) for i in range(num_slices)]


def iter_time_bucket_ranges(min_v: float, max_v: float, buckets: int) -> List[Dict[str, Any]]:
    """
    Build non-overlapping range clauses for a numeric or date field (epoch millis / numeric).
    Last bucket uses lte=max_v; others use lt=hi to avoid duplicates.
    """
    if buckets < 1:
        raise ValueError("buckets must be >= 1")
    if min_v > max_v:
        raise ValueError("min_v > max_v")
    if min_v == max_v:
        return [{"gte": min_v, "lte": max_v}]
    span = max_v - min_v
    out: List[Dict[str, Any]] = []
    for i in range(buckets):
        gte = min_v + (span * i) / buckets
        hi = min_v + (span * (i + 1)) / buckets
        if i == buckets - 1:
            out.append({"gte": gte, "lte": max_v})
        else:
            out.append({"gte": gte, "lt": hi})
    return out


def build_time_bucket_search_body(
    size: int, field: str, range_clause: Dict[str, Any], random_seed: int
) -> dict:
    """Random doc IDs within one time/value bucket (stats-based stratification)."""
    return {
        "size": size,
        "_source": False,
        "query": {
            "function_score": {
                "query": {
                    "bool": {
                        "filter": [{"range": {field: range_clause}}],
                    }
                },
                "random_score": {"seed": random_seed},
            }
        },
    }


def _fetch_stats_min_max(
    url_search: str, auth: Any, field: str
) -> Tuple[Optional[float], Optional[float]]:
    """min/max from stats aggregation (date and numeric fields)."""
    body = {"size": 0, "aggs": {"_bounds": {"stats": {"field": field}}}}
    r = _SESSION.post(url_search, auth=auth, json=body, timeout=_TIMEOUT_SEARCH)
    r.raise_for_status()
    data = r.json()
    st = data.get("aggregations", {}).get("_bounds", {})
    mn_raw = st.get("min")
    mx_raw = st.get("max")
    try:
        mn = float(mn_raw) if mn_raw is not None else None
        mx = float(mx_raw) if mx_raw is not None else None
    except (TypeError, ValueError):
        mn = mx = None
    return mn, mx


def _sample_doc_ids_time_stratified(
    url: str,
    auth: Any,
    n: int,
    field: str,
    time_buckets: int,
    random_seed: Optional[int],
) -> Tuple[List[str], str]:
    """
    Sample IDs by splitting [min,max] of field into buckets (from stats agg), random per bucket.
    Returns (ids, note_detail).
    """
    mn, mx = _fetch_stats_min_max(url, auth, field)
    if mn is None or mx is None:
        return (
            [],
            "time_field stats returned no min/max (field missing, wrong type, or empty index?)",
        )
    k = max(1, min(time_buckets, n))
    ranges = iter_time_bucket_ranges(mn, mx, k)
    sizes = distribute_sample_sizes(n, k)
    base_seed = random_seed if random_seed is not None else 42
    out: List[str] = []
    empty_buckets = 0
    for i, (rng, sz) in enumerate(zip(ranges, sizes)):
        if sz <= 0:
            continue
        body = build_time_bucket_search_body(sz, field, rng, base_seed + i)
        r = _SESSION.post(url, auth=auth, json=body, timeout=_TIMEOUT_SEARCH)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        if not hits:
            empty_buckets += 1
        out.extend(h["_id"] for h in hits if "_id" in h)
    note = f"time_field={field}, buckets={k}"
    if empty_buckets:
        note += f" ({empty_buckets} empty)"
    return list(dict.fromkeys(out)), note


def opensearch_auth_sigv4(region: str) -> AWS4Auth:
    if not boto3:
        raise RuntimeError("boto3 is required for SigV4; pip install boto3")
    credentials = boto3.Session().get_credentials()
    if not credentials:
        raise RuntimeError("No AWS credentials found for SigV4")
    return AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "es",
        session_token=credentials.token,
    )


def head_index_opensearch_sigv4(host: str, index: str, region: str) -> bool:
    auth = opensearch_auth_sigv4(region)
    url = host.rstrip("/") + "/" + index
    r = _SESSION.head(url, auth=auth, timeout=_TIMEOUT_SHORT)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    return True


def head_index_opensearch_basic(host: str, index: str, user: str, password: str) -> bool:
    url = host.rstrip("/") + "/" + index
    r = _SESSION.head(url, auth=(user, password), timeout=_TIMEOUT_SHORT)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    return True


def head_index_elastic(host: str, index: str, dest: DestAuth) -> bool:
    url = host.rstrip("/") + "/" + index
    headers, auth = dest.apply()
    r = _SESSION.head(url, headers=headers or None, auth=auth, timeout=_TIMEOUT_SHORT)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    return True


def get_count_opensearch_sigv4(host: str, index: str, region: str) -> int:
    auth = opensearch_auth_sigv4(region)
    url = host.rstrip("/") + "/" + index + "/_count"
    r = _SESSION.get(url, auth=auth, timeout=_TIMEOUT_SHORT)
    r.raise_for_status()
    return r.json()["count"]


def get_count_opensearch_basic(host: str, index: str, user: str, password: str) -> int:
    url = host.rstrip("/") + "/" + index + "/_count"
    r = _SESSION.get(url, auth=(user, password), timeout=_TIMEOUT_SHORT)
    r.raise_for_status()
    return r.json()["count"]


def get_count_elastic(host: str, index: str, dest: DestAuth) -> int:
    url = host.rstrip("/") + "/" + index + "/_count"
    headers, auth = dest.apply()
    r = _SESSION.get(url, headers=headers or None, auth=auth, timeout=_TIMEOUT_SHORT)
    r.raise_for_status()
    return r.json()["count"]


def _sample_doc_ids_stratified(
    url: str,
    auth: Any,
    n: int,
    random_seed: Optional[int],
    sample_slices: Optional[int],
) -> List[str]:
    """Fetch n IDs using sliced random searches (auth: AWS4Auth or (user, pass) tuple)."""
    k = effective_stratified_slice_count(sample_slices, n)
    sizes = distribute_sample_sizes(n, k)
    base_seed = random_seed if random_seed is not None else 42
    out: List[str] = []
    for i in range(k):
        if sizes[i] <= 0:
            continue
        body = build_stratified_slice_search_body(sizes[i], i, k, base_seed + i)
        r = _SESSION.post(url, auth=auth, json=body, timeout=_TIMEOUT_SEARCH)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        out.extend(h["_id"] for h in hits if "_id" in h)
    # Dedupe while preserving order (should be rare across slices)
    return list(dict.fromkeys(out))


def sample_doc_ids_opensearch_sigv4(
    host: str,
    index: str,
    region: str,
    size: int,
    sample_mode: str,
    random_seed: Optional[int],
    sample_slices: Optional[int] = None,
    time_field: Optional[str] = None,
    time_buckets: int = 8,
    time_meta: Optional[List[str]] = None,
) -> List[str]:
    auth = opensearch_auth_sigv4(region)
    url = host.rstrip("/") + "/" + index + "/_search"
    if sample_mode == "stratified":
        return _sample_doc_ids_stratified(url, auth, size, random_seed, sample_slices)
    if sample_mode == "time_stratified":
        if not time_field:
            raise ValueError("time_stratified requires time_field")
        ids, note = _sample_doc_ids_time_stratified(
            url, auth, size, time_field, time_buckets, random_seed
        )
        if time_meta is not None:
            time_meta.append(note)
        return ids
    body = build_sample_search_body(size, sample_mode, random_seed)
    r = _SESSION.post(url, auth=auth, json=body, timeout=_TIMEOUT_SEARCH)
    r.raise_for_status()
    data = r.json()
    hits = data.get("hits", {}).get("hits", [])
    return [h["_id"] for h in hits if "_id" in h]


def sample_doc_ids_opensearch_basic(
    host: str,
    index: str,
    user: str,
    password: str,
    size: int,
    sample_mode: str,
    random_seed: Optional[int],
    sample_slices: Optional[int] = None,
    time_field: Optional[str] = None,
    time_buckets: int = 8,
    time_meta: Optional[List[str]] = None,
) -> List[str]:
    url = host.rstrip("/") + "/" + index + "/_search"
    if sample_mode == "stratified":
        return _sample_doc_ids_stratified(url, (user, password), size, random_seed, sample_slices)
    if sample_mode == "time_stratified":
        if not time_field:
            raise ValueError("time_stratified requires time_field")
        ids, note = _sample_doc_ids_time_stratified(
            url, (user, password), size, time_field, time_buckets, random_seed
        )
        if time_meta is not None:
            time_meta.append(note)
        return ids
    body = build_sample_search_body(size, sample_mode, random_seed)
    r = _SESSION.post(url, auth=(user, password), json=body, timeout=_TIMEOUT_SEARCH)
    r.raise_for_status()
    data = r.json()
    hits = data.get("hits", {}).get("hits", [])
    return [h["_id"] for h in hits if "_id" in h]


def verify_ids_mget_elastic(
    host: str, index: str, dest: DestAuth, ids: List[str]
) -> Tuple[int, List[str]]:
    """
    Return (found_count, missing_ids) using _mget.
    """
    if not ids:
        return 0, []
    url = host.rstrip("/") + "/" + index + "/_mget"
    body = {"ids": ids}
    headers, auth = dest.apply({"Content-Type": "application/json"})
    r = _SESSION.post(url, headers=headers, auth=auth, json=body, timeout=_TIMEOUT_SEARCH)
    r.raise_for_status()
    data = r.json()
    docs = data.get("docs", [])
    missing = []
    found = 0
    for d in docs:
        if d.get("found"):
            found += 1
        else:
            mid = d.get("_id")
            if mid:
                missing.append(mid)
    return found, missing


def validate_index_name(name: str) -> Optional[str]:
    """
    Return an error string if *name* is not a valid OpenSearch/Elasticsearch index name,
    or None if it is valid.
    Rules: no uppercase; must not start with '-', '_', or '+'; no spaces or
    special characters (, # * ? " < > | / \\ :).
    """
    import re

    if not name:
        return "index name must not be empty"
    if name != name.lower():
        return f"index name must be lowercase: {name!r}"
    if name[0] in ("-", "_", "+"):
        return f"index name must not start with '-', '_', or '+': {name!r}"
    invalid = set(name) & set(' ,#*?"<>|\\/: \t\n')
    if invalid:
        chars = ", ".join(repr(c) for c in sorted(invalid))
        return f"index name contains invalid characters ({chars}): {name!r}"
    if re.search(r"[\x00-\x1f]", name):
        return f"index name contains control characters: {name!r}"
    return None


def _redact_response_text(text: str) -> str:
    """
    Strip potential credential patterns from error response bodies before logging.
    Masks Base64-like tokens and 'ApiKey ...' / 'Bearer ...' patterns.
    """
    import re

    # Mask Authorization header values
    text = re.sub(r"(ApiKey|Bearer)\s+[A-Za-z0-9+/=_\-]{8,}", r"\1 ***", text)
    # Mask long Base64-like strings that look like encoded secrets
    text = re.sub(r"[A-Za-z0-9+/]{40,}={0,2}", "***", text)
    return text


def parse_indices_arg(indices: str) -> List[str]:
    return [s.strip() for s in indices.split(",") if s.strip()]


def read_indices_file(path: str) -> List[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def validate_pair(
    source_host: str,
    dest_host: str,
    source_index: str,
    dest_index: str,
    use_sigv4: bool,
    source_region: str,
    source_user: Optional[str],
    source_password: Optional[str],
    dest_auth: DestAuth,
    check_existence: bool,
    sample_size: int,
    sample_mode: str = "head",
    random_seed: Optional[int] = None,
    sample_slices: Optional[int] = None,
    time_field: Optional[str] = None,
    time_buckets: int = 8,
) -> Tuple[bool, str, str]:
    """
    Return (ok, detail_message, category) where category is \"ok\", \"validation\", or \"transport\".
    """
    try:
        for idx_name, label in ((source_index, "source"), (dest_index, "destination")):
            err = validate_index_name(idx_name)
            if err:
                return False, f"Invalid {label} index name — {err}", "validation"

        if not use_sigv4:
            if source_user is None or source_password is None:
                return (
                    False,
                    "OpenSearch user and password are required when not using SigV4",
                    "validation",
                )
        if check_existence:
            if use_sigv4:
                if not head_index_opensearch_sigv4(source_host, source_index, source_region):
                    return False, f"Source index does not exist: {source_index}", "validation"
            else:
                if source_user is None or source_password is None:
                    return (
                        False,
                        "OpenSearch user and password are required when not using SigV4",
                        "validation",
                    )
                if not head_index_opensearch_basic(
                    source_host, source_index, source_user, source_password
                ):
                    return False, f"Source index does not exist: {source_index}", "validation"
            if not head_index_elastic(dest_host, dest_index, dest_auth):
                return False, f"Destination index does not exist: {dest_index}", "validation"

        if use_sigv4:
            source_count = get_count_opensearch_sigv4(source_host, source_index, source_region)
        else:
            if source_user is None or source_password is None:
                return (
                    False,
                    "OpenSearch user and password are required when not using SigV4",
                    "validation",
                )
            source_count = get_count_opensearch_basic(
                source_host, source_index, source_user, source_password
            )
        dest_count = get_count_elastic(dest_host, dest_index, dest_auth)

        if source_count != dest_count:
            return (
                False,
                f"count mismatch: source={source_count} dest={dest_count}",
                "validation",
            )

        detail = f"counts match ({source_count})"

        if sample_size > 0 and source_count > 0:
            n = min(sample_size, source_count)
            time_meta: List[str] = []
            if use_sigv4:
                ids = sample_doc_ids_opensearch_sigv4(
                    source_host,
                    source_index,
                    source_region,
                    n,
                    sample_mode,
                    random_seed,
                    sample_slices,
                    time_field=time_field,
                    time_buckets=time_buckets,
                    time_meta=time_meta if sample_mode == "time_stratified" else None,
                )
            else:
                if source_user is None or source_password is None:
                    return (
                        False,
                        "OpenSearch user and password are required when not using SigV4",
                        "validation",
                    )
                ids = sample_doc_ids_opensearch_basic(
                    source_host,
                    source_index,
                    source_user,
                    source_password,
                    n,
                    sample_mode,
                    random_seed,
                    sample_slices,
                    time_field=time_field,
                    time_buckets=time_buckets,
                    time_meta=time_meta if sample_mode == "time_stratified" else None,
                )
            if sample_mode == "time_stratified" and not ids and time_meta:
                return False, f"{detail}; {time_meta[0]}", "validation"
            if len(ids) < n:
                detail += f"; sampled {len(ids)} ids (requested {n})"
            found, missing = verify_ids_mget_elastic(dest_host, dest_index, dest_auth, ids)
            if missing:
                return (
                    False,
                    f"{detail}; ID sample failed: {len(missing)} missing on dest (e.g. {missing[:5]})",
                    "validation",
                )
            strat = ""
            if sample_mode == "stratified":
                k = effective_stratified_slice_count(sample_slices, n)
                strat = f", slices={k}"
            elif sample_mode == "time_stratified" and time_meta:
                strat = f", {time_meta[0]}"
            detail += f"; ID sample OK ({found} docs, mode={sample_mode}{strat})"

        return True, detail, "ok"
    except ValueError as e:
        return False, str(e), "validation"
    except requests.RequestException as e:
        msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            msg += f" — {_redact_response_text(e.response.text[:500])}"
        return False, msg, "transport"


def main():
    import bootstrap_env

    bootstrap_env.load()

    parser = argparse.ArgumentParser(
        description="Validate migration: compare OpenSearch vs Elastic doc counts; optional batch and ID sampling."
    )
    parser.add_argument("--source-index", help="Source index name (single-index mode)")
    parser.add_argument("--dest-index", help="Destination index name (single-index mode)")
    parser.add_argument(
        "--indices",
        help="Comma-separated source index names (batch). Destination = --dest-prefix + name + --dest-suffix unless same as source.",
    )
    parser.add_argument(
        "--indices-file",
        help="File with one source index name per line (# comments OK). Batch mode.",
    )
    parser.add_argument(
        "--dest-prefix",
        default="",
        help="Prepended to each source index name for destination (batch).",
    )
    parser.add_argument(
        "--dest-suffix",
        default="",
        help="Appended to each source index name for destination (batch).",
    )
    parser.add_argument(
        "--source-host",
        default=os.environ.get("SOURCE_OPENSEARCH_HOST"),
        help="OpenSearch base URL",
    )
    parser.add_argument(
        "--dest-host", default=os.environ.get("DEST_ELASTIC_HOST"), help="Elastic base URL"
    )
    parser.add_argument(
        "--source-region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
        help="AWS region for SigV4 (source)",
    )
    parser.add_argument(
        "--source-user",
        default=os.environ.get("SOURCE_OPENSEARCH_USER"),
        help="OpenSearch user (basic auth)",
    )
    parser.add_argument(
        "--source-password",
        default=os.environ.get("SOURCE_OPENSEARCH_PASSWORD"),
        help="OpenSearch password",
    )
    parser.add_argument(
        "--dest-api-key", default=os.environ.get("DEST_ELASTIC_API_KEY"), help="Elastic API key"
    )
    parser.add_argument(
        "--dest-api-key-encoded",
        action="store_true",
        default=os.environ.get("DEST_ELASTIC_API_KEY_ENCODED", "").lower() in ("1", "true", "yes"),
        help="Indicate that --dest-api-key is already Base64-encoded (skip automatic encoding).",
    )
    parser.add_argument(
        "--dest-user", default=os.environ.get("DEST_ELASTIC_USER"), help="Elastic user"
    )
    parser.add_argument(
        "--dest-password", default=os.environ.get("DEST_ELASTIC_PASSWORD"), help="Elastic password"
    )
    parser.add_argument(
        "--check-existence",
        action="store_true",
        help="HEAD source/dest index before _count (clearer errors if index is missing).",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=0,
        metavar="N",
        help="After counts match, fetch N document IDs from source and verify they exist on dest via _mget (0 = skip).",
    )
    parser.add_argument(
        "--sample-mode",
        choices=("head", "random", "stratified", "time_stratified"),
        default="head",
        help="How to sample IDs: head, random, stratified (slice), or time_stratified (stats min/max buckets on --time-field).",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=None,
        help="Seed for random_score / stratified slices (default 42 if omitted).",
    )
    parser.add_argument(
        "--sample-slices",
        type=int,
        default=None,
        metavar="K",
        help="With --sample-mode stratified, number of search slices (default min(8, sample size); capped to sample size).",
    )
    parser.add_argument(
        "--time-field",
        default=None,
        metavar="FIELD",
        help="Required for time_stratified: field for stats agg + range filters (e.g. @timestamp, numeric). Use epoch millis or numeric types.",
    )
    parser.add_argument(
        "--time-buckets",
        type=int,
        default=8,
        metavar="B",
        help="With time_stratified, number of min/max buckets (default 8; capped to sample size).",
    )
    parser.add_argument(
        "--output-format",
        choices=("text", "json", "csv"),
        default="text",
        help="text: human lines; json: one JSON object on stdout; csv: header + rows (good for CI artifacts).",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="Stderr log format: text (default) or json (one JSON object per line, useful for CI).",
    )
    parser.add_argument(
        "--strict-exit-codes",
        action="store_true",
        help=(
            "Distinct exit codes for automation: 0=ok, 1=data/validation failure, "
            "2=misconfiguration, 3=network/HTTP failure. Default: any failure exits 1 "
            "(argparse errors still exit 2)."
        ),
    )
    args = parser.parse_args()

    batch_indices: List[str] = []
    if args.indices_file:
        batch_indices.extend(read_indices_file(args.indices_file))
    if args.indices:
        batch_indices.extend(parse_indices_arg(args.indices))
    if batch_indices:
        seen = set()
        batch_indices = [x for x in batch_indices if not (x in seen or seen.add(x))]
    elif args.source_index and args.dest_index:
        batch_indices = None
    else:
        parser.error(
            "Provide --source-index and --dest-index, or --indices / --indices-file for batch mode."
        )

    if not args.source_host or not args.dest_host:
        parser.error(
            "--source-host and --dest-host (or SOURCE_OPENSEARCH_HOST and DEST_ELASTIC_HOST) are required"
        )

    use_sigv4 = not (args.source_user and args.source_password)
    global _LOG_FORMAT_JSON
    _LOG_FORMAT_JSON = args.log_format == "json"

    if use_sigv4 and not boto3:
        _cli_log("error", "For SigV4 source auth, boto3 is required.")
        sys.exit(2 if args.strict_exit_codes else 1)
    if not args.dest_api_key and not (args.dest_user and args.dest_password):
        _cli_log("error", "Set --dest-api-key or (--dest-user and --dest-password) for Elastic.")
        sys.exit(2 if args.strict_exit_codes else 1)
    if args.sample_mode == "time_stratified" and not args.time_field:
        parser.error("--sample-mode time_stratified requires --time-field")

    dest_auth = DestAuth(
        api_key=args.dest_api_key,
        api_key_encoded=args.dest_api_key_encoded,
        user=args.dest_user,
        password=args.dest_password,
    )

    if batch_indices is None:
        pairs = [(args.source_index, args.dest_index)]
    else:
        if not batch_indices:
            print("Error: no indices in batch.", file=sys.stderr)
            sys.exit(2 if args.strict_exit_codes else 1)
        pairs = [(src, args.dest_prefix + src + args.dest_suffix) for src in batch_indices]

    results: List[dict] = []
    failures = 0
    transport_failures = 0
    for src_idx, dst_idx in pairs:
        ok, detail, category = validate_pair(
            args.source_host,
            args.dest_host,
            src_idx,
            dst_idx,
            use_sigv4,
            args.source_region,
            args.source_user,
            args.source_password,
            dest_auth,
            check_existence=args.check_existence,
            sample_size=args.sample_size,
            sample_mode=args.sample_mode,
            random_seed=args.random_seed,
            sample_slices=args.sample_slices,
            time_field=args.time_field,
            time_buckets=args.time_buckets,
        )
        results.append(
            {
                "source_index": src_idx,
                "dest_index": dst_idx,
                "ok": ok,
                "status": "PASS" if ok else "FAIL",
                "detail": detail,
                "category": category,
            }
        )
        if not ok:
            failures += 1
            if category == "transport":
                transport_failures += 1

    def _exit_failure() -> None:
        if args.strict_exit_codes and transport_failures:
            sys.exit(3)
        if failures:
            sys.exit(1)

    if args.output_format == "text":
        for row in results:
            print(
                f"[{row['status']}] {row['source_index']} -> {row['dest_index']}: {row['detail']}"
            )
        if failures:
            _cli_log("error", f"{len(pairs)} pair(s) checked; {failures} failed.", failed=failures)
            _exit_failure()
        print("All checks passed.")
    elif args.output_format == "json":
        out = {
            "summary": {
                "checked": len(pairs),
                "failed": failures,
                "transport_failed": transport_failures,
            },
            "results": results,
        }
        print(json.dumps(out, indent=2))
        if failures:
            _exit_failure()
    else:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["source_index", "dest_index", "status", "ok", "category", "detail"])
        for row in results:
            w.writerow(
                [
                    row["source_index"],
                    row["dest_index"],
                    row["status"],
                    row["ok"],
                    row["category"],
                    row["detail"],
                ]
            )
        print(buf.getvalue().rstrip("\n"))
        if failures:
            _exit_failure()


if __name__ == "__main__":
    main()
