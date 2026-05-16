"""
Record-level content hash for change detection.

Hashes the canonical business payload of a Phase 3 record:
    {name, address, phone, opening_hours}

`website_url` is the identity key, not part of the hashed payload.

Pipeline:
    record  -> canonical_payload (pick + normalize)
            -> canonical_json    (sort_keys, no whitespace, utf-8)
            -> sha256 hex
"""

import hashlib
import json
import re
from typing import Any

TRACKED_FIELDS = ("name", "address", "phone", "opening_hours")

_WS_RE = re.compile(r"\s+")


def _norm_string(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return _norm_string(value)
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def canonical_payload(record: dict) -> dict:
    """Pick TRACKED_FIELDS from record; missing -> None; normalize strings."""
    return {f: _normalize(record.get(f)) for f in TRACKED_FIELDS}


def canonical_json(payload: dict) -> str:
    """Deterministic JSON: sorted keys, no whitespace, unicode preserved."""
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def compute_hash(record: dict) -> str:
    """SHA-256 hex of the canonical-JSON of the canonical payload."""
    blob = canonical_json(canonical_payload(record)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
