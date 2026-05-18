"""File-backed brain store — fallback when Supabase is unavailable.

Provides the same interface as the db_repo brain functions so callers can
switch transparently. Stored at output/brain_store.json.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

_STORE_PATH = Path(__file__).parent.parent.parent / "output" / "brain_store.json"
_LOCK = threading.Lock()


def _load() -> dict:
    if _STORE_PATH.exists():
        try:
            with open(_STORE_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return {"patterns": [], "candidates": [], "_next_id": 1}


def _save(data: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STORE_PATH, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def enqueue_candidate(
    field: str,
    pattern_type: str,
    candidate_pattern: str,
    language: str = "any",
    llm_cost_eur: float = 0.0,
    rationale: Optional[str] = None,
    **_,
) -> dict:
    with _LOCK:
        data = _load()
        row: dict = {
            "id": data.get("_next_id", 1),
            "field": field,
            "pattern_type": pattern_type,
            "candidate_pattern": candidate_pattern,
            "language": language,
            "status": "queued",
            "llm_cost_eur": llm_cost_eur,
            "rationale": rationale,
        }
        data["candidates"].append(row)
        data["_next_id"] = row["id"] + 1
        _save(data)
        return row


def list_candidates(status: Optional[str] = "queued") -> list:
    data = _load()
    candidates = data.get("candidates", [])
    if status:
        return [c for c in candidates if c.get("status") == status]
    return candidates


def promote_candidate(candidate_id: int, baseline_confidence: float = 0.5) -> dict:
    with _LOCK:
        data = _load()
        cand = next((c for c in data.get("candidates", []) if c["id"] == candidate_id), None)
        if not cand:
            raise LookupError(f"local brain: candidate {candidate_id} not found")
        pat: dict = {
            "id": data.get("_next_id", 1),
            "field": cand["field"],
            "pattern_type": cand["pattern_type"],
            "pattern": cand["candidate_pattern"],
            "language": cand.get("language", "any"),
            "confidence_score": baseline_confidence,
            "status": "trial",
            "success_count": 0,
            "failure_count": 0,
            "rationale": cand.get("rationale"),
        }
        data["patterns"].append(pat)
        for c in data["candidates"]:
            if c["id"] == candidate_id:
                c["status"] = "promoted"
        data["_next_id"] = pat["id"] + 1
        _save(data)
        return pat


def list_active_patterns(field: str, language: Optional[str] = None) -> list:
    data = _load()
    active = [
        p for p in data.get("patterns", [])
        if p.get("status") in ("trial", "active") and p.get("field") == field
    ]
    if language:
        active = [p for p in active if p.get("language") in (language, "any")]
    return sorted(active, key=lambda p: float(p.get("confidence_score", 0.5)), reverse=True)


def bump_pattern_success(pattern_id: int, delta: float = 0.01) -> None:
    with _LOCK:
        data = _load()
        for p in data.get("patterns", []):
            if p["id"] == pattern_id:
                p["confidence_score"] = min(1.0, float(p.get("confidence_score", 0.5)) + delta)
                p["success_count"] = int(p.get("success_count", 0)) + 1
                break
        _save(data)


def bump_pattern_failure(pattern_id: int, delta: float = 0.1) -> Optional[float]:
    with _LOCK:
        data = _load()
        new_conf = None
        for p in data.get("patterns", []):
            if p["id"] == pattern_id:
                new_conf = max(0.0, float(p.get("confidence_score", 0.5)) - delta)
                p["confidence_score"] = new_conf
                p["failure_count"] = int(p.get("failure_count", 0)) + 1
                break
        _save(data)
        return new_conf


def set_pattern_status(pattern_id: int, status: str) -> None:
    with _LOCK:
        data = _load()
        for p in data.get("patterns", []):
            if p["id"] == pattern_id:
                p["status"] = status
                break
        _save(data)
