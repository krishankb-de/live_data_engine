"""Database repository — all Supabase read/write ops.

Every write is wrapped in tenacity.retry (3 attempts, exp backoff).
All write functions return plain dicts from Supabase response.data.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper.supabase_client import get_client

logger = logging.getLogger(__name__)

_RETRY_KW = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_str(opening_hours: Any) -> Optional[str]:
    if opening_hours is None:
        return None
    if isinstance(opening_hours, dict):
        return json.dumps(opening_hours, ensure_ascii=False, sort_keys=True)
    return str(opening_hours)


def _compact(row: dict, always: set[str] = frozenset()) -> dict:
    """Drop keys with None values unless they're in always. Prevents 'None' string bugs in supabase-py."""
    return {k: v for k, v in row.items() if v is not None or k in always}


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def upsert_listing(record: dict) -> dict:
    """Upsert keyed on gs_listing_id. record must include gs_listing_id + name.

    Manual select + insert/update because the live schema may not have a
    UNIQUE index on gs_listing_id (Postgres ON CONFLICT would otherwise handle this).
    """
    gs_id = record.get("gs_listing_id") or record.get("gs_uuid", "")
    _required = {"gs_listing_id", "name", "is_paid", "is_verifiable", "updated_at"}
    row = _compact(
        {
            "gs_listing_id": gs_id,
            "name": record.get("name", ""),
            "category": record.get("category"),
            "address": record.get("address"),
            "phone": record.get("phone"),
            "opening_hours": _hours_str(record.get("opening_hours")),
            "website_url": record.get("website_url"),
            "is_paid": bool(record.get("is_paid", False)),
            "is_verifiable": bool(record.get("is_verifiable", True)),
            "unverifiable_reason": record.get("unverifiable_reason"),
            "updated_at": _now(),
        },
        always=_required,
    )
    existing = _get_listing_by_gs_id_raw(gs_id)
    if existing:
        get_client().table("listings").update(row).eq("id", existing["id"]).execute()
        return {**existing, **row}
    resp = get_client().table("listings").insert(row).execute()
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def _get_listing_by_gs_id_raw(gs_listing_id: str) -> Optional[dict]:
    resp = (
        get_client()
        .table("listings")
        .select("*")
        .eq("gs_listing_id", gs_listing_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry(**_RETRY_KW)
def get_listing(listing_id: int) -> Optional[dict]:
    resp = (
        get_client()
        .table("listings")
        .select("*")
        .eq("id", listing_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def get_listing_by_gs_id(gs_listing_id: str) -> Optional[dict]:
    return _get_listing_by_gs_id_raw(gs_listing_id)


@retry(**_RETRY_KW)
def list_listings(
    q: str = "",
    city: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Paginated. Returns (rows, total_count)."""
    qb = get_client().table("listings").select("*", count="exact")
    if q:
        qb = qb.or_(f"name.ilike.%{q}%,address.ilike.%{q}%")
    if city:
        qb = qb.ilike("address", f"%{city}%")
    resp = qb.range(offset, offset + limit - 1).order("id", desc=True).execute()
    return resp.data or [], resp.count or 0


@retry(**_RETRY_KW)
def update_listing_field(listing_id: int, field: str, value: Any) -> None:
    get_client().table("listings").update(
        {field: value, "updated_at": _now()}
    ).eq("id", listing_id).execute()


@retry(**_RETRY_KW)
def touch_listing_hash(listing_id: int, last_checked: str) -> None:
    get_client().table("listings").update(
        {"last_checked": last_checked, "updated_at": _now()}
    ).eq("id", listing_id).execute()


# ---------------------------------------------------------------------------
# Recheck scheduler — used by pipeline_ug
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def pick_due_listings(limit: int = 50, verifiable_only: bool = True) -> list[dict]:
    """Listings whose next_check is due (NULL or <= now), paid first then by due-date.

    `next_check IS NULL` rows (never-checked) sort first when we order by
    next_check ASC NULLS FIRST — supabase-py exposes this via nullsfirst=True.
    """
    q = (
        get_client()
        .table("listings")
        .select(
            "id, gs_listing_id, name, website_url, is_paid, is_verifiable, "
            "check_interval_days, consecutive_unchanged, last_checked, next_check"
        )
        .or_(f"next_check.is.null,next_check.lte.{_now()}")
    )
    if verifiable_only:
        q = q.eq("is_verifiable", True)
    resp = (
        q.order("is_paid", desc=True)
         .order("next_check", desc=False, nullsfirst=True)
         .order("id", desc=False)
         .limit(limit)
         .execute()
    )
    return resp.data or []


@retry(**_RETRY_KW)
def update_listing_schedule(
    listing_id: int,
    *,
    interval_days: float,
    consecutive_unchanged: int,
    next_check_iso: str,
) -> None:
    """Persist scheduler state after a recheck run."""
    get_client().table("listings").update(
        {
            "check_interval_days": interval_days,
            "consecutive_unchanged": consecutive_unchanged,
            "last_checked": _now(),
            "next_check": next_check_iso,
            "updated_at": _now(),
        }
    ).eq("id", listing_id).execute()


@retry(**_RETRY_KW)
def get_recipe_pages(domain: str) -> Optional[dict]:
    """Return the `pages` JSONB blob for a domain, or None if no recipe exists.

    The blob shape is intentionally open: pipeline_ug stores per-page doorman
    state as {page_name: {"url_path": "/contact", "last_etag": "...",
    "last_modified": "...", "last_content_hash": "..."}, ...}
    """
    resp = (
        get_client()
        .table("recipes")
        .select("id, domain, pages")
        .eq("domain", domain)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry(**_RETRY_KW)
def save_recipe_pages(domain: str, pages: dict) -> None:
    """Persist the `pages` blob back to the recipe row. Inserts if missing."""
    client = get_client()
    existing = (
        client.table("recipes").select("id").eq("domain", domain).limit(1).execute()
    )
    if existing.data:
        client.table("recipes").update(
            {"pages": pages, "last_used_at": _now()}
        ).eq("domain", domain).execute()
    else:
        client.table("recipes").insert(
            {"domain": domain, "pages": pages, "status": "active"}
        ).execute()


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def create_batch() -> dict:
    resp = (
        get_client()
        .table("batches")
        .insert({"status": "queued"})
        .execute()
    )
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def get_batch(batch_id: int) -> Optional[dict]:
    resp = (
        get_client()
        .table("batches")
        .select("*")
        .eq("id", batch_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry(**_RETRY_KW)
def list_batches(limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    resp = (
        get_client()
        .table("batches")
        .select("*", count="exact")
        .range(offset, offset + limit - 1)
        .order("id", desc=True)
        .execute()
    )
    return resp.data or [], resp.count or 0


@retry(**_RETRY_KW)
def update_batch(batch_id: int, **fields: Any) -> None:
    get_client().table("batches").update(fields).eq("id", batch_id).execute()


@retry(**_RETRY_KW)
def finalize_batch(batch_id: int, counts: dict, status: str = "done") -> None:
    get_client().table("batches").update(
        {"status": status, "finished_at": _now(), **counts}
    ).eq("id", batch_id).execute()


# ---------------------------------------------------------------------------
# Field observations
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def insert_observation(
    listing_id: int,
    field: str,
    value: Optional[str],
    source: str,
    source_url: Optional[str] = None,
    source_page: Optional[str] = None,
    confidence: Optional[float] = None,
    pattern_id: Optional[int] = None,
) -> dict:
    _required = {"listing_id", "field", "is_present", "source"}
    resp = (
        get_client()
        .table("field_observations")
        .insert(
            _compact(
                {
                    "listing_id": listing_id,
                    "field": field,
                    "value": value,
                    "is_present": value is not None,
                    "source": source,
                    "source_url": source_url,
                    "source_page": source_page,
                    "extraction_confidence": confidence,
                    "pattern_id": pattern_id,
                },
                always=_required,
            )
        )
        .execute()
    )
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def latest_observations(listing_id: int) -> list[dict]:
    """One (most-recent) observation per field."""
    resp = (
        get_client()
        .table("field_observations")
        .select("*")
        .eq("listing_id", listing_id)
        .order("observed_at", desc=True)
        .execute()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for row in resp.data or []:
        if row["field"] not in seen:
            seen.add(row["field"])
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

_CONFIDENCE_THRESHOLDS = (0.85, 0.50)


def decision_from_confidence(confidence: float) -> str:
    if confidence >= _CONFIDENCE_THRESHOLDS[0]:
        return "auto_applied"
    if confidence >= _CONFIDENCE_THRESHOLDS[1]:
        return "needs_review"
    return "discarded"


@retry(**_RETRY_KW)
def insert_version(
    listing_id: int,
    batch_id: Optional[int],
    field: str,
    old_value: Optional[str],
    new_value: Optional[str],
    confidence: float,
    signals: Optional[dict] = None,
    reasoning: Optional[str] = None,
) -> dict:
    decision = decision_from_confidence(confidence)
    _required = {"listing_id", "field", "intent_confidence", "decision"}
    resp = (
        get_client()
        .table("versions")
        .insert(
            _compact(
                {
                    "listing_id": listing_id,
                    "batch_id": batch_id,
                    "field": field,
                    "old_value": old_value,
                    "new_value": new_value,
                    "intent_confidence": confidence,
                    "decision": decision,
                    "signals": signals,
                    "reasoning": reasoning,
                },
                always=_required,
            )
        )
        .execute()
    )
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def get_version(version_id: int) -> Optional[dict]:
    resp = (
        get_client()
        .table("versions")
        .select("*")
        .eq("id", version_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry(**_RETRY_KW)
def list_versions_for_listing(listing_id: int) -> list[dict]:
    resp = (
        get_client()
        .table("versions")
        .select("*")
        .eq("listing_id", listing_id)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


@retry(**_RETRY_KW)
def list_pending_reviews(limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    resp = (
        get_client()
        .table("versions")
        .select("*", count="exact")
        .eq("decision", "needs_review")
        .range(offset, offset + limit - 1)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or [], resp.count or 0


@retry(**_RETRY_KW)
def accept_version(version_id: int, applied_by: str) -> dict:
    ver = get_version(version_id)
    if ver is None:
        raise LookupError(f"version {version_id} not found")
    current = ver.get("decision")
    if current in ("auto_applied", "discarded"):
        raise ValueError(f"version {version_id} already decided: {current}")

    now = _now()
    get_client().table("versions").update(
        {"decision": "auto_applied", "applied_at": now, "applied_by": applied_by}
    ).eq("id", version_id).execute()

    if ver.get("new_value") is not None:
        update_listing_field(ver["listing_id"], ver["field"], ver["new_value"])

    return {**ver, "decision": "auto_applied", "applied_at": now, "applied_by": applied_by}


@retry(**_RETRY_KW)
def reject_version(version_id: int, reviewed_by: str, reason: Optional[str] = None) -> dict:
    ver = get_version(version_id)
    if ver is None:
        raise LookupError(f"version {version_id} not found")
    current = ver.get("decision")
    if current in ("auto_applied", "discarded"):
        raise ValueError(f"version {version_id} already decided: {current}")

    now = _now()
    updates: dict = {"decision": "discarded", "reviewed_at": now, "reviewed_by": reviewed_by}
    if reason:
        updates["reasoning"] = reason
    get_client().table("versions").update(updates).eq("id", version_id).execute()
    return {**ver, **updates}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def log_audit(
    action: str,
    outcome: str,
    listing_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    details: Optional[dict] = None,
    cost_eur: float = 0.0,
    duration_ms: Optional[int] = None,
) -> None:
    get_client().table("audit_log").insert(
        {
            "listing_id": listing_id,
            "batch_id": batch_id,
            "action": action,
            "outcome": outcome,
            "details": details,
            "cost_eur": cost_eur,
            "duration_ms": duration_ms,
        }
    ).execute()


# ---------------------------------------------------------------------------
# Cost log
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def bump_cost(
    day_iso: str,
    llm_calls: int = 0,
    llm_tokens_in: int = 0,
    llm_tokens_out: int = 0,
    llm_cost_eur: float = 0.0,
    http_requests: int = 0,
    listings_processed: int = 0,
) -> None:
    existing = (
        get_client()
        .table("cost_log")
        .select("*")
        .eq("day", day_iso)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    if rows:
        row = rows[0]
        get_client().table("cost_log").update(
            {
                "llm_calls": row["llm_calls"] + llm_calls,
                "llm_tokens_in": row["llm_tokens_in"] + llm_tokens_in,
                "llm_tokens_out": row["llm_tokens_out"] + llm_tokens_out,
                "llm_cost_eur": row["llm_cost_eur"] + llm_cost_eur,
                "http_requests": row["http_requests"] + http_requests,
                "listings_processed": row["listings_processed"] + listings_processed,
            }
        ).eq("day", day_iso).execute()
    else:
        get_client().table("cost_log").insert(
            {
                "day": day_iso,
                "llm_calls": llm_calls,
                "llm_tokens_in": llm_tokens_in,
                "llm_tokens_out": llm_tokens_out,
                "llm_cost_eur": llm_cost_eur,
                "http_requests": http_requests,
                "listings_processed": listings_processed,
            }
        ).execute()


@retry(**_RETRY_KW)
def list_cost_log(from_date: str = "", to_date: str = "") -> list[dict]:
    qb = get_client().table("cost_log").select("*")
    if from_date:
        qb = qb.gte("day", from_date)
    if to_date:
        qb = qb.lte("day", to_date)
    return qb.order("day", desc=True).execute().data or []


@retry(**_RETRY_KW)
def cost_today_eur(day_iso: str) -> float:
    resp = (
        get_client()
        .table("cost_log")
        .select("llm_cost_eur")
        .eq("day", day_iso)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return float(rows[0]["llm_cost_eur"]) if rows else 0.0


# ---------------------------------------------------------------------------
# Brain: global patterns
# ---------------------------------------------------------------------------
# NOTE on atomicity: confidence/success/failure bumps are read-modify-write,
# matching `bump_cost` style. Acceptable for single-worker dev. For production
# parallelism, migrate these to Postgres RPC functions (see plan Phase 6).

_BRAIN_ACTIVE_STATUSES = ("trial", "active")


@retry(**_RETRY_KW)
def list_active_patterns(field: str, language: Optional[str] = None) -> list[dict]:
    """Patterns eligible for the online runtime: trial + active, ordered by confidence DESC."""
    qb = (
        get_client()
        .table("global_patterns")
        .select("*")
        .eq("field", field)
        .in_("status", list(_BRAIN_ACTIVE_STATUSES))
    )
    if language:
        qb = qb.in_("language", [language, "any"])
    resp = qb.order("confidence_score", desc=True).execute()
    return resp.data or []


@retry(**_RETRY_KW)
def get_pattern(pattern_id: int) -> Optional[dict]:
    resp = (
        get_client()
        .table("global_patterns")
        .select("*")
        .eq("id", pattern_id)
        .limit(1)
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


@retry(**_RETRY_KW)
def insert_pattern(
    field: str,
    pattern_type: str,
    pattern: str,
    language: str = "any",
    confidence_score: float = 0.5,
    status: str = "trial",
    origin_domain: Optional[str] = None,
    parent_recipe_id: Optional[int] = None,
    rationale: Optional[str] = None,
) -> dict:
    resp = (
        get_client()
        .table("global_patterns")
        .insert(
            _compact(
                {
                    "field": field,
                    "pattern_type": pattern_type,
                    "pattern": pattern,
                    "language": language,
                    "confidence_score": confidence_score,
                    "status": status,
                    "origin_domain": origin_domain,
                    "parent_recipe_id": parent_recipe_id,
                    "rationale": rationale,
                },
                always={"field", "pattern_type", "pattern", "language", "confidence_score", "status"},
            )
        )
        .execute()
    )
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def bump_pattern_success(pattern_id: int, delta: float = 0.01) -> None:
    pat = get_pattern(pattern_id)
    if pat is None:
        return
    new_conf = min(1.0, float(pat["confidence_score"]) + delta)
    get_client().table("global_patterns").update(
        {
            "success_count": int(pat["success_count"]) + 1,
            "confidence_score": new_conf,
            "last_used_at": _now(),
        }
    ).eq("id", pattern_id).execute()


@retry(**_RETRY_KW)
def bump_pattern_failure(pattern_id: int, delta: float = 0.1) -> Optional[float]:
    """Decrement confidence. Returns new confidence_score (or None if pattern not found)."""
    pat = get_pattern(pattern_id)
    if pat is None:
        return None
    new_conf = max(0.0, float(pat["confidence_score"]) - delta)
    get_client().table("global_patterns").update(
        {
            "failure_count": int(pat["failure_count"]) + 1,
            "confidence_score": new_conf,
            "last_used_at": _now(),
        }
    ).eq("id", pattern_id).execute()
    return new_conf


@retry(**_RETRY_KW)
def set_pattern_status(pattern_id: int, status: str) -> None:
    get_client().table("global_patterns").update({"status": status}).eq(
        "id", pattern_id
    ).execute()


@retry(**_RETRY_KW)
def list_patterns(field: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    qb = get_client().table("global_patterns").select("*")
    if field:
        qb = qb.eq("field", field)
    if status:
        qb = qb.eq("status", status)
    return qb.order("confidence_score", desc=True).execute().data or []


# ---------------------------------------------------------------------------
# Brain: pattern execution log
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def record_pattern_execution(
    pattern_id: int,
    outcome: str,
    listing_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    extracted_value: Optional[str] = None,
    validator_passed: Optional[bool] = None,
    failing_snippet: Optional[str] = None,
) -> None:
    get_client().table("pattern_executions").insert(
        _compact(
            {
                "pattern_id": pattern_id,
                "listing_id": listing_id,
                "batch_id": batch_id,
                "outcome": outcome,
                "extracted_value": extracted_value,
                "validator_passed": validator_passed,
                "failing_snippet": failing_snippet,
            },
            always={"pattern_id", "outcome"},
        )
    ).execute()


@retry(**_RETRY_KW)
def recent_failing_snippets(pattern_id: int, limit: int = 3) -> list[str]:
    """Used by repair task to seed the LLM prompt with concrete negatives."""
    resp = (
        get_client()
        .table("pattern_executions")
        .select("failing_snippet")
        .eq("pattern_id", pattern_id)
        .eq("validator_passed", False)
        .order("ts", desc=True)
        .limit(limit)
        .execute()
    )
    return [row["failing_snippet"] for row in (resp.data or []) if row.get("failing_snippet")]


# ---------------------------------------------------------------------------
# Brain: sandbox fixtures
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def insert_fixture(
    source_url: str,
    html_path: str,
    field: str,
    expected_value: Optional[str],
    language: str = "any",
) -> dict:
    resp = (
        get_client()
        .table("sandbox_fixtures")
        .insert(
            {
                "source_url": source_url,
                "html_path": html_path,
                "field": field,
                "expected_value": expected_value,
                "language": language,
            }
        )
        .execute()
    )
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def list_fixtures(field: Optional[str] = None, language: Optional[str] = None) -> list[dict]:
    qb = get_client().table("sandbox_fixtures").select("*")
    if field:
        qb = qb.eq("field", field)
    if language:
        qb = qb.in_("language", [language, "any"])
    return qb.execute().data or []


# ---------------------------------------------------------------------------
# Brain: candidate queue
# ---------------------------------------------------------------------------

@retry(**_RETRY_KW)
def enqueue_candidate(
    field: str,
    pattern_type: str,
    candidate_pattern: str,
    language: str = "any",
    parent_recipe_id: Optional[int] = None,
    parent_pattern_id: Optional[int] = None,
    llm_cost_eur: float = 0.0,
    rationale: Optional[str] = None,
) -> dict:
    resp = (
        get_client()
        .table("candidate_queue")
        .insert(
            _compact(
                {
                    "field": field,
                    "pattern_type": pattern_type,
                    "candidate_pattern": candidate_pattern,
                    "language": language,
                    "status": "queued",
                    "parent_recipe_id": parent_recipe_id,
                    "parent_pattern_id": parent_pattern_id,
                    "llm_cost_eur": llm_cost_eur,
                    "rationale": rationale,
                },
                always={"field", "pattern_type", "candidate_pattern", "language", "status"},
            )
        )
        .execute()
    )
    return (resp.data or [{}])[0]


@retry(**_RETRY_KW)
def list_candidates(status: Optional[str] = "queued") -> list[dict]:
    qb = get_client().table("candidate_queue").select("*")
    if status:
        qb = qb.eq("status", status)
    return qb.order("ts").execute().data or []


@retry(**_RETRY_KW)
def update_candidate(
    candidate_id: int,
    status: str,
    sandbox_precision: Optional[float] = None,
    sandbox_recall: Optional[float] = None,
    sandbox_details: Optional[dict] = None,
) -> None:
    get_client().table("candidate_queue").update(
        _compact(
            {
                "status": status,
                "sandbox_precision": sandbox_precision,
                "sandbox_recall": sandbox_recall,
                "sandbox_details": sandbox_details,
            },
            always={"status"},
        )
    ).eq("id", candidate_id).execute()


def promote_candidate(candidate_id: int, baseline_confidence: float = 0.5) -> dict:
    """Validated candidate → new row in global_patterns (status='trial')."""
    cand = (
        get_client()
        .table("candidate_queue")
        .select("*")
        .eq("id", candidate_id)
        .limit(1)
        .execute()
        .data
        or [{}]
    )[0]
    if not cand:
        raise LookupError(f"candidate {candidate_id} not found")
    pat = insert_pattern(
        field=cand["field"],
        pattern_type=cand["pattern_type"],
        pattern=cand["candidate_pattern"],
        language=cand.get("language") or "any",
        confidence_score=baseline_confidence,
        status="trial",
        origin_domain=None,
        parent_recipe_id=cand.get("parent_recipe_id"),
        rationale=cand.get("rationale"),
    )
    update_candidate(candidate_id, status="promoted")
    return pat
