"""End-to-end pipeline test — 50 batches, all extraction paths exercised.

Groups (50 batches total):
  A (1-20)  : jsonld fixture     + brain OFF  → fields extracted via jsonld
  B (21-35) : regex fixture      + brain OFF  → fields extracted via regex
  C (36-45) : regex fixture      + brain ON   + parsers disabled → fields via brain patterns
  D (46-50) : regex fixture      + brain OFF  + parsers disabled → fields via recipe fallback

Tests end-to-end:
  • Phase 2 → 3 → 4 → 6 pipeline via mock HTTP site
  • DB repo: batch CRUD, listing upsert, observations, versions, scheduler
  • Brain runtime: pattern injection, confidence bumps, per-field hit tracking
  • Recipe builder: LLM-recipe fallback path via fill_missing mock

Outputs saved to output/logs/:
  • batch_{idx:02d}.log       per-batch structured event log
  • run_manifest.json         machine-readable full manifest
  • test_report_50batches.md  human-readable Markdown report

Run:
    pytest tests/test_pipeline_50batches.py -v -s
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Constants / paths
# ---------------------------------------------------------------------------

NUM_BATCHES = 50
MOCK_HOST = "127.0.0.1"
MOCK_PORT = 15174
MOCK_BASE = f"http://{MOCK_HOST}:{MOCK_PORT}"

OUTPUT_DIR = ROOT / "output"
LOG_DIR    = OUTPUT_DIR / "logs"
REPORT_MD  = OUTPUT_DIR / "test_report_50batches.md"
MANIFEST   = LOG_DIR / "run_manifest.json"

# Group boundaries
GROUP_A_END = 20   # batches  1-20  jsonld
GROUP_B_END = 35   # batches 21-35  regex
GROUP_C_END = 45   # batches 36-45  brain
# batches 46-50  recipe  (D)

# Brain patterns injected for Group C
_BRAIN_PHONE_PATTERN = {
    "id": 9001,
    "field": "phone",
    "pattern_type": "regex",
    "pattern": r"\+49\s*30\s*[\d\s]+",
    "language": "any",
    "confidence_score": 0.88,
    "status": "active",
    "success_count": 5,
    "failure_count": 0,
}
_BRAIN_ADDRESS_PATTERN = {
    "id": 9002,
    "field": "address",
    "pattern_type": "regex",
    "pattern": r"\d{5}\s+Berlin",
    "language": "any",
    "confidence_score": 0.80,
    "status": "active",
    "success_count": 3,
    "failure_count": 0,
}
_BRAIN_HOURS_PATTERN = {
    "id": 9003,
    "field": "opening_hours",
    "pattern_type": "regex",
    "pattern": r"Mo[-–]Fr\s+\d{1,2}:\d{2}[-–]\d{1,2}:\d{2}",
    "language": "any",
    "confidence_score": 0.75,
    "status": "active",
    "success_count": 2,
    "failure_count": 0,
}

_BRAIN_PATTERNS_BY_FIELD: dict[str, list[dict]] = {
    "phone":         [_BRAIN_PHONE_PATTERN],
    "address":       [_BRAIN_ADDRESS_PATTERN],
    "opening_hours": [_BRAIN_HOURS_PATTERN],
    "name":          [],
}


# ---------------------------------------------------------------------------
# Logging infrastructure
# ---------------------------------------------------------------------------

class BatchLogger:
    """Captures structured events for one batch and writes to output/logs/."""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        self.events: list[dict] = []
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._path = LOG_DIR / f"batch_{idx:02d}.log"
        self._start = time.perf_counter()

    def log(self, stage: str, msg: str, **kw) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        entry = {"ts": ts, "stage": stage, "msg": msg, **kw}
        self.events.append(entry)

    def flush(self) -> None:
        with open(self._path, "w") as fh:
            for e in self.events:
                fh.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000


# Root Python logger interceptor — captures scraper log lines per batch
class _LogCapture(logging.Handler):
    def __init__(self, blog: BatchLogger):
        super().__init__()
        self._blog = blog

    def emit(self, record: logging.LogRecord) -> None:
        self._blog.log(
            stage="scraper_log",
            msg=record.getMessage(),
            level=record.levelname,
            logger=record.name,
        )


# ---------------------------------------------------------------------------
# In-memory DB backend
# ---------------------------------------------------------------------------

class _InMemoryDB:
    def __init__(self) -> None:
        self._listings: dict[str, dict] = {}
        self._lid_seq = 1
        self._batches: dict[int, dict] = {}
        self._bid_seq = 1
        self._observations: list[dict] = []
        self._versions: list[dict] = []
        self._recipe_pages: dict[str, dict] = {}
        self._pattern_hits: list[dict] = []   # {pattern_id, field, value}
        self._cost: dict[str, dict] = {}

    # ---- listings ----
    def upsert_listing(self, rec: dict) -> dict:
        gid = rec.get("gs_listing_id") or rec.get("gs_uuid", "")
        if gid in self._listings:
            self._listings[gid].update(rec)
            return self._listings[gid]
        row = {"id": self._lid_seq, **rec}
        self._lid_seq += 1
        self._listings[gid] = row
        return row

    def get_listing(self, lid: int) -> Optional[dict]:
        return next((r for r in self._listings.values() if r["id"] == lid), None)

    def get_listing_by_gs_id(self, gid: str) -> Optional[dict]:
        return self._listings.get(gid)

    def list_listings(self, q="", city="", limit=50, offset=0):
        rows = list(self._listings.values())
        return rows[offset:offset + limit], len(rows)

    def update_listing_field(self, lid: int, fld: str, val: Any) -> None:
        row = self.get_listing(lid)
        if row:
            row[fld] = val

    def touch_listing_hash(self, lid: int, ts: str) -> None:
        row = self.get_listing(lid)
        if row:
            row["last_checked"] = ts

    def pick_due_listings(self, limit=50, verifiable_only=True) -> list[dict]:
        rows = [r for r in self._listings.values()
                if not verifiable_only or r.get("is_verifiable", True)]
        return rows[:limit]

    def update_listing_schedule(self, lid, *, interval_days, consecutive_unchanged,
                                 next_check_iso) -> None:
        row = self.get_listing(lid)
        if row:
            row.update({
                "check_interval_days": interval_days,
                "consecutive_unchanged": consecutive_unchanged,
                "next_check": next_check_iso,
                "last_checked": _now(),
            })

    # ---- batches ----
    def create_batch(self) -> dict:
        bid = self._bid_seq; self._bid_seq += 1
        row = {"id": bid, "status": "queued", "created_at": _now()}
        self._batches[bid] = row
        return row

    def get_batch(self, bid: int) -> Optional[dict]:
        return self._batches.get(bid)

    def update_batch(self, bid: int, **kw) -> None:
        if bid in self._batches:
            self._batches[bid].update(kw)

    def finalize_batch(self, bid: int, counts: dict, status="done") -> None:
        if bid in self._batches:
            self._batches[bid].update({"status": status, "finished_at": _now(), **counts})

    def list_batches(self, limit=20, offset=0):
        rows = list(self._batches.values())
        return rows[offset:offset + limit], len(rows)

    # ---- observations ----
    def insert_observation(self, lid, fld, val, src,
                           source_url=None, source_page=None,
                           confidence=None, pattern_id=None) -> dict:
        row = {"id": len(self._observations)+1, "listing_id": lid, "field": fld,
               "value": val, "is_present": val is not None, "source": src,
               "confidence": confidence, "observed_at": _now()}
        self._observations.append(row)
        return row

    def latest_observations(self, lid: int) -> list[dict]:
        seen: set[str] = set()
        out = []
        for o in reversed(self._observations):
            if o["listing_id"] == lid and o["field"] not in seen:
                seen.add(o["field"])
                out.append(o)
        return out

    # ---- versions ----
    def insert_version(self, listing_id, batch_id, field, old_value, new_value,
                       confidence, signals=None, reasoning=None) -> dict:
        decision = ("auto_applied" if confidence >= 0.85
                    else "needs_review" if confidence >= 0.50
                    else "discarded")
        row = {"id": len(self._versions)+1, "listing_id": listing_id,
               "batch_id": batch_id, "field": field, "old_value": old_value,
               "new_value": new_value, "intent_confidence": confidence,
               "decision": decision, "created_at": _now()}
        self._versions.append(row)
        return row

    def list_versions_for_listing(self, lid: int) -> list[dict]:
        return [v for v in self._versions if v["listing_id"] == lid]

    def list_pending_reviews(self, limit=50, offset=0):
        rows = [v for v in self._versions if v["decision"] == "needs_review"]
        return rows[offset:offset + limit], len(rows)

    # ---- brain ----
    def list_active_patterns(self, fld: str, language=None) -> list[dict]:
        return [p for p in _BRAIN_PATTERNS_BY_FIELD.get(fld, [])
                if p["status"] in ("trial", "active")]

    def bump_pattern_success(self, pid: int, delta=0.01) -> None:
        for p in sum(_BRAIN_PATTERNS_BY_FIELD.values(), []):
            if p["id"] == pid:
                p["confidence_score"] = min(1.0, p["confidence_score"] + delta)
                p["success_count"] += 1

    def bump_pattern_failure(self, pid: int, delta=0.1) -> Optional[float]:
        for p in sum(_BRAIN_PATTERNS_BY_FIELD.values(), []):
            if p["id"] == pid:
                p["confidence_score"] = max(0.0, p["confidence_score"] - delta)
                p["failure_count"] += 1
                return p["confidence_score"]
        return None

    def set_pattern_status(self, pid: int, status: str) -> None:
        for p in sum(_BRAIN_PATTERNS_BY_FIELD.values(), []):
            if p["id"] == pid:
                p["status"] = status

    def record_pattern_execution(self, pattern_id, outcome,
                                  listing_id=None, batch_id=None,
                                  extracted_value=None,
                                  validator_passed=None,
                                  failing_snippet=None) -> None:
        self._pattern_hits.append({
            "pattern_id": pattern_id, "outcome": outcome,
            "extracted_value": extracted_value,
        })

    def get_recipe_pages(self, domain: str) -> Optional[dict]:
        return self._recipe_pages.get(domain)

    def save_recipe_pages(self, domain: str, pages: dict) -> None:
        self._recipe_pages[domain] = {"domain": domain, "pages": pages}

    def enqueue_candidate(self, **kw) -> dict:
        return {"id": 1, "status": "queued", **kw}

    def list_candidates(self, status="queued") -> list[dict]:
        return []

    def bump_cost(self, day_iso, **_) -> None: pass
    def cost_today_eur(self, day_iso: str) -> float: return 0.0
    def log_audit(self, *_, **__) -> None: pass

    # ---- helpers ----
    def version_decisions(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for v in self._versions:
            out[v["decision"]] += 1
        return dict(out)

    def observation_sources(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for o in self._observations:
            out[o["source"]] += 1
        return dict(out)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Batch result dataclass
# ---------------------------------------------------------------------------

@dataclass
class BatchResult:
    idx: int
    group: str           # A / B / C / D
    fixture: str         # jsonld / regex / garbled
    brain_enabled: bool
    batch_id: int = -1
    gs_id: str = ""
    name: str = ""

    # Phase outcomes
    phase2_ok: bool = False
    phase3_status: str = ""
    phase4_ok: bool = False
    phase6_ok: bool = False
    phase6_changed: int = 0
    phase6_unchanged: int = 0

    # Per-field extraction sources
    field_sources: dict[str, str] = field(default_factory=dict)   # {field: src}

    # Extraction path tallies
    jsonld_fields: int = 0
    regex_fields: int = 0
    brain_fields: int = 0
    recipe_fields: int = 0
    cache_fields: int = 0
    miss_fields: int = 0

    # DB state
    db_stored: bool = False
    obs_count: int = 0
    ver_count: int = 0

    # Scheduler
    sched_ok: bool = False
    interval_days: float = 0.0

    # Brain
    brain_pattern_hits: list[dict] = field(default_factory=list)

    # Errors
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        must_extract = self.phase3_status in ("complete", "partial")
        return must_extract and self.db_stored and self.sched_ok and not self.errors

    def extraction_path(self) -> str:
        """Dominant extraction path for this record."""
        candidates = [
            ("jsonld", self.jsonld_fields),
            ("brain",  self.brain_fields),
            ("recipe", self.recipe_fields),
            ("regex",  self.regex_fields),
        ]
        dominant = max(candidates, key=lambda x: x[1])
        return dominant[0] if dominant[1] > 0 else "miss"


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db() -> _InMemoryDB:
    return _InMemoryDB()


@pytest.fixture(scope="module")
def mock_http_server():
    """Start the in-process mock site server (port 15174) once for the module."""
    from tests.conftest_mock_site import _Handler, _state, MOCK_SITE_HOST, MOCK_SITE_PORT
    from http.server import ThreadingHTTPServer
    import threading

    _state.reset()
    srv = ThreadingHTTPServer((MOCK_SITE_HOST, MOCK_SITE_PORT), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


# ---------------------------------------------------------------------------
# Helper: determine group for a batch index
# ---------------------------------------------------------------------------

def _group_for(idx: int) -> tuple[str, str, bool]:
    """Returns (group_letter, fixture_name, brain_enabled)."""
    if idx <= GROUP_A_END:
        return "A", "jsonld", False
    if idx <= GROUP_B_END:
        return "B", "regex", False
    if idx <= GROUP_C_END:
        return "C", "regex", True
    return "D", "regex", False


# ---------------------------------------------------------------------------
# Helper: patch db_repo → in-memory backend
# ---------------------------------------------------------------------------

def _patch_db(db_inst: _InMemoryDB):
    import contextlib, db_repo as _r

    @contextlib.contextmanager
    def _cm():
        attrs = [n for n in dir(db_inst) if not n.startswith("_") and callable(getattr(db_inst, n))]
        patchers = []
        for a in attrs:
            if hasattr(_r, a):
                p = patch.object(_r, a, side_effect=getattr(db_inst, a))
                p.start()
                patchers.append(p)
        try:
            yield
        finally:
            for p in patchers:
                p.stop()

    return _cm()


# ---------------------------------------------------------------------------
# Core: run one batch
# ---------------------------------------------------------------------------

def _run_batch(idx: int, db_inst: _InMemoryDB) -> BatchResult:
    from scraper.phase2_site_map import run as run_p2
    from scraper.phase3_extract import run as run_p3
    from scraper import phase4_diff
    from scraper.phase6_content_hash import run as run_p6
    from pipeline_ug import scheduler
    from scraper.brain.runtime import invalidate_cache
    from tests.conftest_mock_site import _state

    group, fixture, brain_on = _group_for(idx)
    prefix = f"p50b_{idx:02d}_"
    gs_id = f"p50b-{idx:04d}"

    res = BatchResult(
        idx=idx, group=group, fixture=fixture,
        brain_enabled=brain_on,
        gs_id=gs_id, name=f"Mock Bookstore {idx:04d}",
    )
    blog = BatchLogger(idx)
    t0 = time.perf_counter()

    # Attach log capture
    root_logger = logging.getLogger()
    handler = _LogCapture(blog)
    root_logger.addHandler(handler)

    try:
        # ---- switch fixture ----
        _state.set_fixture(fixture)
        blog.log("setup", f"fixture={fixture} brain={brain_on} group={group}")

        listing = {
            "gs_listing_id": gs_id,
            "gs_uuid": gs_id,
            "name": res.name,
            "website_url": f"{MOCK_BASE}/?n={idx}",
            "address": f"Teststraße {idx}, 10115 Berlin",
            "phone": f"+4930{idx:07d}",
            "is_paid": idx % 5 == 0,
            "is_verifiable": True,
            "target_city": "Berlin",
        }

        OUTPUT_DIR.mkdir(exist_ok=True)
        (OUTPUT_DIR / f"{prefix}phase1_listings.json").write_text(
            json.dumps([listing], ensure_ascii=False)
        )

        with _patch_db(db_inst):
            # 1. Batch
            batch_row = db_inst.create_batch()
            res.batch_id = batch_row["id"]
            db_inst.update_batch(res.batch_id, status="running")
            blog.log("batch", f"created id={res.batch_id}")

            # 2. Store listing
            lr = db_inst.upsert_listing(listing)
            res.db_stored = bool(lr.get("id"))
            lid = lr["id"]
            blog.log("listing", f"upserted id={lid} gs={gs_id}")

            # 3. Phase 2
            try:
                run_p2(prefix=prefix)
                p2f = OUTPUT_DIR / f"{prefix}phase2_site_map.json"
                res.phase2_ok = p2f.exists() and json.loads(p2f.read_text()) != []
                blog.log("phase2", f"ok={res.phase2_ok}")
            except Exception as e:
                res.errors.append(f"phase2:{e}")
                blog.log("phase2", f"ERROR: {e}", error=True)

            # 4. Phase 3
            try:
                env_override = {"BRAIN_ENABLED": "1" if brain_on else "0"}
                brain_patterns_mock = {
                    "phone":         _BRAIN_PATTERNS_BY_FIELD["phone"],
                    "address":       _BRAIN_PATTERNS_BY_FIELD["address"],
                    "opening_hours": _BRAIN_PATTERNS_BY_FIELD["opening_hours"],
                    "name":          [],
                }

                # Group C: disable jsonld+regex parsers so brain takes over
                # Group D: disable jsonld+regex+brain so recipe takes over
                _null_jsonld  = lambda page: {}
                _null_phone   = lambda text: None
                _null_address = lambda text, target_city=None: None
                _null_hours   = lambda text: None
                _null_name    = lambda page: None

                if brain_on:
                    invalidate_cache()  # force fresh pattern load

                def _fake_list_active(fld, language=None):
                    return brain_patterns_mock.get(fld, [])

                def _fake_fill_missing(domain, fetched_pages, missing_fields,
                                       store, content_hash=None):
                    """Recipe fallback: return plausible values from Group-D pages."""
                    filled: dict[str, str] = {}
                    sources: dict[str, str] = {}
                    for mf in missing_fields:
                        if mf == "phone":
                            filled[mf] = "+49308765431-recipe"
                        elif mf == "address":
                            filled[mf] = "Bergmannstraße 12, 10961 Berlin (recipe)"
                        elif mf == "opening_hours":
                            filled[mf] = "Mo-Fr 10:00-19:00 (recipe)"
                        elif mf == "name":
                            filled[mf] = "Buchhandlung Kreuzberg (recipe)"
                        if mf in filled:
                            sources[mf] = "recipe"
                    return filled, sources

                ctx_patches: list = []

                if group in ("C", "D"):
                    # Disable JSON-LD and regex parsers
                    ctx_patches += [
                        patch("scraper.phase3_extract.parse_jsonld", side_effect=_null_jsonld),
                        patch("scraper.phase3_extract.parse_phone", side_effect=_null_phone),
                        patch("scraper.phase3_extract.parse_address", side_effect=_null_address),
                        patch("scraper.phase3_extract.parse_opening_hours", side_effect=_null_hours),
                        patch("scraper.phase3_extract.parse_name_from_page", side_effect=_null_name),
                    ]

                if group == "C":
                    ctx_patches.append(
                        patch("db_repo.list_active_patterns", side_effect=_fake_list_active)
                    )

                if group == "D":
                    ctx_patches.append(
                        patch("scraper.recipe_builder.fill_missing",
                              side_effect=_fake_fill_missing)
                    )

                import contextlib

                @contextlib.contextmanager
                def _multi_patch(patchers):
                    started = []
                    for p in patchers:
                        p.start()
                        started.append(p)
                    try:
                        yield
                    finally:
                        for p in reversed(started):
                            p.stop()

                with patch.dict(os.environ, env_override), _multi_patch(ctx_patches):
                    records = run_p3(prefix=prefix)

                if records:
                    rec = records[0]
                    res.phase3_status = rec.get("extraction_status", "failed")
                    res.field_sources = rec.get("field_sources") or {}
                    blog.log("phase3", f"status={res.phase3_status} sources={res.field_sources}")

                    # Tally extraction paths
                    src_map = res.field_sources
                    for fld in ("name", "address", "phone", "opening_hours"):
                        src = src_map.get(fld, "miss")
                        if src == "jsonld":   res.jsonld_fields  += 1
                        elif src == "regex":  res.regex_fields   += 1
                        elif src == "brain":  res.brain_fields   += 1
                        elif src == "recipe": res.recipe_fields  += 1
                        elif src == "cache":  res.cache_fields   += 1
                        else:                 res.miss_fields    += 1

                    blog.log("extraction_paths", "",
                             jsonld=res.jsonld_fields, regex=res.regex_fields,
                             brain=res.brain_fields, recipe=res.recipe_fields,
                             miss=res.miss_fields)

                    # Write observations + versions
                    for fld in ("name", "address", "phone", "opening_hours"):
                        val = rec.get(fld)
                        if val is None:
                            continue
                        if isinstance(val, dict):
                            val = json.dumps(val, ensure_ascii=False, sort_keys=True)
                        src = src_map.get(fld, "regex")
                        conf = 0.9 if src in ("jsonld", "recipe") else 0.88 if src == "brain" else 0.75
                        db_inst.insert_observation(lid, fld, str(val), src)
                        old = lr.get(fld)
                        if str(val) != str(old or ""):
                            db_inst.insert_version(
                                listing_id=lid, batch_id=res.batch_id,
                                field=fld, old_value=old, new_value=str(val),
                                confidence=conf,
                            )

                    res.obs_count = len(db_inst.latest_observations(lid))
                    res.ver_count = len(db_inst.list_versions_for_listing(lid))
                    blog.log("db_write", f"obs={res.obs_count} ver={res.ver_count}")

            except Exception as e:
                res.errors.append(f"phase3:{e}")
                blog.log("phase3", f"ERROR: {e}", error=True)
                traceback.print_exc()

            # 5. Phase 4
            try:
                phase4_diff.run(prefix=prefix)
                res.phase4_ok = (OUTPUT_DIR / f"{prefix}phase4_diff.json").exists()
                blog.log("phase4", f"ok={res.phase4_ok}")
            except Exception as e:
                res.errors.append(f"phase4:{e}")
                blog.log("phase4", f"ERROR: {e}", error=True)

            # 6. Phase 6
            try:
                p6 = run_p6(prefix=prefix)
                res.phase6_ok = True
                diff = p6.get("diff", {})
                res.phase6_changed   = len(diff.get("changed", []))
                res.phase6_unchanged = len(diff.get("unchanged", []))
                blog.log("phase6", f"changed={res.phase6_changed} unchanged={res.phase6_unchanged}")
            except Exception as e:
                res.errors.append(f"phase6:{e}")
                blog.log("phase6", f"ERROR: {e}", error=True)

            # 7. Scheduler
            try:
                row = db_inst.get_listing(lid)
                if row:
                    row.setdefault("check_interval_days", 7.0)
                    row.setdefault("consecutive_unchanged", 0)
                    row.setdefault("is_paid", listing.get("is_paid", False))
                    row.setdefault("is_verifiable", True)
                new_interval, next_iso = scheduler.update_next_check(
                    lid, changed=(res.phase3_status == "complete")
                )
                res.sched_ok = True
                res.interval_days = new_interval
                blog.log("scheduler", f"interval={new_interval:.2f}d next={next_iso[:10]}")
            except Exception as e:
                res.errors.append(f"scheduler:{e}")
                blog.log("scheduler", f"ERROR: {e}", error=True)

            # 8. Brain pattern hit log (for groups C)
            if brain_on:
                hits = [h for h in db_inst._pattern_hits
                        if h not in getattr(res, "_seen_hits", [])]
                res.brain_pattern_hits = hits
                blog.log("brain_hits", f"count={len(hits)}",
                         hits=[{"pid": h["pattern_id"], "val": h.get("extracted_value")}
                               for h in hits])

            # 9. Finalize batch
            db_inst.finalize_batch(res.batch_id, {
                "listings_processed": 1,
                "changes_proposed": res.ver_count,
            })
            blog.log("batch_finalized", f"status=done ver={res.ver_count}")

    finally:
        root_logger.removeHandler(handler)
        invalidate_cache()
        res.duration_ms = (time.perf_counter() - t0) * 1000
        blog.log("done", f"passed={res.passed} ms={res.duration_ms:.0f}")
        blog.flush()

    return res


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(results: list[BatchResult], db_inst: _InMemoryDB) -> None:
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    # Aggregate extraction path counts per field
    field_src_counts: dict[str, dict[str, int]] = {
        f: defaultdict(int) for f in ("name", "address", "phone", "opening_hours")
    }
    for r in results:
        for fld, src in r.field_sources.items():
            if fld in field_src_counts:
                field_src_counts[fld][src] += 1

    # Per-group totals
    groups: dict[str, list[BatchResult]] = defaultdict(list)
    for r in results:
        groups[r.group].append(r)

    jsonld_total  = sum(r.jsonld_fields  for r in results)
    regex_total   = sum(r.regex_fields   for r in results)
    brain_total   = sum(r.brain_fields   for r in results)
    recipe_total  = sum(r.recipe_fields  for r in results)
    miss_total    = sum(r.miss_fields    for r in results)
    avg_ms        = sum(r.duration_ms    for r in results) / len(results)

    vd = db_inst.version_decisions()
    obs_src = db_inst.observation_sources()

    lines: list[str] = [
        "# Pipeline E2E — 50-Batch Test Report",
        "",
        f"**Date:** {date.today().isoformat()}  ",
        f"**Total batches:** {len(results)}  ",
        f"**Passed:** {len(passed)} / {len(results)}  ",
        f"**Failed:** {len(failed)} / {len(results)}  ",
        "",
        "---",
        "",
        "## Extraction Path Summary",
        "",
        "| Path | Field-Slots Filled | % of Total |",
        "|------|--------------------|------------|",
    ]
    total_slots = jsonld_total + regex_total + brain_total + recipe_total + miss_total
    for label, cnt in [("JSON-LD", jsonld_total), ("Regex", regex_total),
                       ("Brain pattern", brain_total), ("Recipe (LLM fallback)", recipe_total),
                       ("Miss / not extracted", miss_total)]:
        pct = 100 * cnt / total_slots if total_slots else 0
        lines.append(f"| {label} | {cnt} | {pct:.1f}% |")

    lines += [
        "",
        "## Per-Field Extraction Sources",
        "",
        "| Field | jsonld | regex | brain | recipe | miss |",
        "|-------|--------|-------|-------|--------|------|",
    ]
    for fld in ("name", "address", "phone", "opening_hours"):
        sc = field_src_counts[fld]
        lines.append(
            f"| {fld} | {sc.get('jsonld',0)} | {sc.get('regex',0)} "
            f"| {sc.get('brain',0)} | {sc.get('recipe',0)} | {sc.get('miss',0)} |"
        )

    lines += [
        "",
        "## Group Results",
        "",
        "| Group | Fixture | Brain | Batches | Pass | Phase3 Complete | Avg ms |",
        "|-------|---------|-------|---------|------|-----------------|--------|",
    ]
    for g in sorted(groups.keys()):
        grp = groups[g]
        p3c = sum(1 for r in grp if r.phase3_status == "complete")
        gpass = sum(1 for r in grp if r.passed)
        gms = sum(r.duration_ms for r in grp) / len(grp)
        brain_flag = "ON" if grp[0].brain_enabled else "off"
        lines.append(
            f"| {g} | {grp[0].fixture} | {brain_flag} | {len(grp)} "
            f"| {gpass} | {p3c} | {gms:.0f} |"
        )

    lines += [
        "",
        "## DB State Snapshot",
        "",
        f"- Listings stored: {len(db_inst._listings)}",
        f"- Batches created / finalized: {len(db_inst._batches)} / "
        f"{sum(1 for b in db_inst._batches.values() if b.get('status')=='done')}",
        f"- Total observations: {len(db_inst._observations)}",
        f"- Observation sources: "
        + ", ".join(f"{k}={v}" for k, v in sorted(obs_src.items())),
        f"- Total versions: {len(db_inst._versions)}",
        f"- Version decisions: auto_applied={vd.get('auto_applied',0)}"
        f"  needs_review={vd.get('needs_review',0)}  discarded={vd.get('discarded',0)}",
        f"- Brain pattern executions logged: {len(db_inst._pattern_hits)}",
        "",
        "## Brain Pattern Confidence (after run)",
        "",
        "| Pattern ID | Field | Confidence | Successes | Failures |",
        "|------------|-------|------------|-----------|---------|",
    ]
    for p in [_BRAIN_PHONE_PATTERN, _BRAIN_ADDRESS_PATTERN, _BRAIN_HOURS_PATTERN]:
        lines.append(
            f"| {p['id']} | {p['field']} | {p['confidence_score']:.3f} "
            f"| {p['success_count']} | {p['failure_count']} |"
        )

    lines += [
        "",
        "## Scheduler Interval Distribution",
        "",
        "| Range (days) | Count |",
        "|--------------|-------|",
    ]
    buckets: dict[str, int] = defaultdict(int)
    for r in results:
        if r.sched_ok:
            if r.interval_days < 2:    buckets["< 2"] += 1
            elif r.interval_days < 4:  buckets["2–4"] += 1
            elif r.interval_days < 8:  buckets["4–8"] += 1
            else:                      buckets["8+"]  += 1
    for b in ["< 2", "2–4", "4–8", "8+"]:
        lines.append(f"| {b} | {buckets.get(b, 0)} |")

    lines += [
        "",
        "## Per-Batch Detail",
        "",
        "| # | Grp | Fixture | P2 | P3 Status | P4 | P6 | "
        "JL | RX | BR | RC | Obs | Ver | Sched | ms |",
        "|---|-----|---------|----|-----------|----|----"
        "|----|----|----|-----|-----|-----|-------|-----|",
    ]
    for r in results:
        p2 = "✓" if r.phase2_ok else "✗"
        p4 = "✓" if r.phase4_ok else "✗"
        p6 = "✓" if r.phase6_ok else "✗"
        sc = f"✓{r.interval_days:.1f}" if r.sched_ok else "✗"
        lines.append(
            f"| {r.idx:2d} | {r.group} | {r.fixture[:5]} | {p2} | "
            f"{r.phase3_status:<8} | {p4} | {p6} | "
            f"{r.jsonld_fields} | {r.regex_fields} | {r.brain_fields} | "
            f"{r.recipe_fields} | {r.obs_count} | {r.ver_count} | "
            f"{sc} | {r.duration_ms:.0f} |"
        )
        if r.errors:
            for e in r.errors:
                lines.append(f"|  | | | | ⚠ `{e}` | | | | | | | | | | |")

    if failed:
        lines += ["", "## Failed Batches", ""]
        for r in failed:
            lines.append(f"### Batch {r.idx} ({r.group})")
            for e in r.errors:
                lines.append(f"- `{e}`")
            lines.append("")

    lines += [
        "",
        f"---",
        f"*Logs per batch: `output/logs/batch_XX.log`  •  "
        f"Manifest: `output/logs/run_manifest.json`*",
        "",
    ]

    REPORT_MD.parent.mkdir(exist_ok=True)
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")

    # Write manifest
    manifest = {
        "run_ts": _now(),
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "extraction_paths": {
            "jsonld": jsonld_total, "regex": regex_total,
            "brain": brain_total, "recipe": recipe_total, "miss": miss_total,
        },
        "db": {
            "listings": len(db_inst._listings),
            "observations": len(db_inst._observations),
            "versions": len(db_inst._versions),
            "pattern_hits": len(db_inst._pattern_hits),
        },
        "batches": [
            {
                "idx": r.idx, "group": r.group, "fixture": r.fixture,
                "passed": r.passed, "phase3_status": r.phase3_status,
                "field_sources": r.field_sources,
                "extraction_path": r.extraction_path(),
                "obs": r.obs_count, "ver": r.ver_count,
                "interval_days": r.interval_days,
                "errors": r.errors,
                "duration_ms": round(r.duration_ms, 1),
            }
            for r in results
        ],
    }
    LOG_DIR.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report  → {REPORT_MD}")
    print(f"  Manifest→ {MANIFEST}")
    print(f"  Logs    → {LOG_DIR}/batch_XX.log")


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestPipeline50Batches:

    results: list[BatchResult] = []
    _db: Optional[_InMemoryDB] = None

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def test_50_batches_end_to_end(self, mock_http_server, db):
        """Run all 50 batches end-to-end and verify aggregate quality gates."""
        TestPipeline50Batches._db = db
        all_results: list[BatchResult] = []

        grp_labels = {
            "A": "JSON-LD  path",
            "B": "Regex    path",
            "C": "Brain    path",
            "D": "Recipe   path",
        }

        prev_group = ""
        for idx in range(1, NUM_BATCHES + 1):
            g, _, _ = _group_for(idx)
            if g != prev_group:
                print(f"\n  === Group {g}: {grp_labels[g]} ===")
                prev_group = g

            r = _run_batch(idx, db)
            all_results.append(r)

            status  = "PASS" if r.passed else "FAIL"
            p3s     = r.phase3_status
            paths   = (f"jl={r.jsonld_fields} rx={r.regex_fields} "
                       f"br={r.brain_fields} rc={r.recipe_fields}")
            print(
                f"  [{status}] {idx:02d} | {r.gs_id} | p3={p3s:<8} "
                f"| {paths} | obs={r.obs_count} ver={r.ver_count} "
                f"| sched={r.interval_days:.1f}d | {r.duration_ms:.0f}ms"
            )

        TestPipeline50Batches.results = all_results
        _write_report(all_results, db)

        # ---- aggregate summary ----
        passed  = [r for r in all_results if r.passed]
        failed  = [r for r in all_results if not r.passed]
        jl_cnt  = sum(r.jsonld_fields  for r in all_results)
        rx_cnt  = sum(r.regex_fields   for r in all_results)
        br_cnt  = sum(r.brain_fields   for r in all_results)
        rc_cnt  = sum(r.recipe_fields  for r in all_results)

        print(f"\n  {'='*62}")
        print(f"  Passed: {len(passed)}/{NUM_BATCHES}   "
              f"Failed: {len(failed)}/{NUM_BATCHES}")
        print(f"  Extraction paths — jsonld:{jl_cnt}  regex:{rx_cnt}  "
              f"brain:{br_cnt}  recipe:{rc_cnt}")
        print(f"  DB — listings:{len(db._listings)}  "
              f"obs:{len(db._observations)}  ver:{len(db._versions)}")
        print(f"  Brain hits logged: {len(db._pattern_hits)}")
        print(f"  {'='*62}")

        # ---- assertions ----

        # All batches created & finalized
        assert len(db._batches) == NUM_BATCHES
        assert all(b["status"] == "done" for b in db._batches.values())

        # All listings stored
        assert len(db._listings) == NUM_BATCHES

        # Phase-3 extractable ≥ 90%
        extractable = sum(1 for r in all_results if r.phase3_status in ("complete", "partial"))
        assert extractable >= NUM_BATCHES * 0.90, \
            f"Phase-3 extractable rate: {extractable}/{NUM_BATCHES}"

        # Group A: all via jsonld (fixture has schema.org)
        grp_a = [r for r in all_results if r.group == "A"]
        assert all(r.jsonld_fields > 0 for r in grp_a), \
            "Group A should extract at least some fields via JSON-LD"

        # Group B: all via regex (no schema.org in regex fixture)
        grp_b = [r for r in all_results if r.group == "B"]
        assert all(r.jsonld_fields == 0 for r in grp_b), \
            "Group B regex fixture should have no JSON-LD hits"
        assert all(r.regex_fields > 0 for r in grp_b), \
            "Group B should extract fields via regex"

        # Group C: brain path (parsers disabled)
        grp_c = [r for r in all_results if r.group == "C"]
        assert sum(r.brain_fields for r in grp_c) > 0, \
            "Group C (brain=ON, parsers disabled) should have brain extractions"

        # Group D: recipe path
        grp_d = [r for r in all_results if r.group == "D"]
        assert sum(r.recipe_fields for r in grp_d) > 0, \
            "Group D should have recipe extractions"

        # Brain pattern hits recorded in DB
        assert len(db._pattern_hits) > 0, "Brain executions not logged to DB"

        # Scheduler fired for ≥ 90%
        sched_ok = sum(1 for r in all_results if r.sched_ok)
        assert sched_ok >= NUM_BATCHES * 0.90

        # Overall pass rate ≥ 90%
        assert len(passed) >= NUM_BATCHES * 0.90, \
            f"Pass rate: {len(passed)}/{NUM_BATCHES}. " + \
            "Failed: " + ", ".join(str(r.idx) for r in failed[:10])

    # ------------------------------------------------------------------
    # Sub-checks
    # ------------------------------------------------------------------

    def test_batch_lifecycle(self):
        assert all(b["status"] == "done"
                   for b in TestPipeline50Batches._db._batches.values())

    def test_version_decisions_valid(self):
        valid = {"auto_applied", "needs_review", "discarded"}
        for v in TestPipeline50Batches._db._versions:
            assert v["decision"] in valid, \
                f"Version {v['id']} has bad decision: {v['decision']!r}"

    def test_scheduler_intervals_in_range(self):
        for r in TestPipeline50Batches.results:
            if r.sched_ok:
                assert 1.0 <= r.interval_days <= 120.0, \
                    f"Batch {r.idx}: interval={r.interval_days}"

    def test_groupA_uses_jsonld_path(self):
        grp = [r for r in TestPipeline50Batches.results if r.group == "A"]
        jsonld_batches = sum(1 for r in grp if r.jsonld_fields >= 3)
        assert jsonld_batches >= len(grp) * 0.95, \
            f"Expected ≥95% of Group A to use jsonld for ≥3 fields; got {jsonld_batches}/{len(grp)}"

    def test_groupB_uses_regex_path(self):
        grp = [r for r in TestPipeline50Batches.results if r.group == "B"]
        regex_batches = sum(1 for r in grp if r.regex_fields >= 2)
        assert regex_batches >= len(grp) * 0.90, \
            f"Expected ≥90% of Group B to use regex for ≥2 fields; got {regex_batches}/{len(grp)}"

    def test_groupC_uses_brain_path(self):
        grp = [r for r in TestPipeline50Batches.results if r.group == "C"]
        brain_batches = sum(1 for r in grp if r.brain_fields >= 1)
        assert brain_batches >= len(grp) * 0.80, \
            f"Expected ≥80% of Group C to use brain for ≥1 field; got {brain_batches}/{len(grp)}"

    def test_groupD_uses_recipe_path(self):
        grp = [r for r in TestPipeline50Batches.results if r.group == "D"]
        recipe_batches = sum(1 for r in grp if r.recipe_fields >= 1)
        assert recipe_batches == len(grp), \
            f"All Group D batches should have recipe extraction; got {recipe_batches}/{len(grp)}"

    def test_brain_confidence_increased(self):
        """Pattern confidence must have grown after Group C hits."""
        assert _BRAIN_PHONE_PATTERN["confidence_score"] > 0.88, \
            "Phone pattern confidence did not increase after brain hits"

    def test_report_and_manifest_written(self):
        assert REPORT_MD.exists()
        assert MANIFEST.exists()
        manifest = json.loads(MANIFEST.read_text())
        assert manifest["passed"] == NUM_BATCHES
        assert manifest["extraction_paths"]["brain"] > 0
        assert manifest["extraction_paths"]["recipe"] > 0

    def test_per_batch_logs_written(self):
        for idx in range(1, NUM_BATCHES + 1):
            log_file = LOG_DIR / f"batch_{idx:02d}.log"
            assert log_file.exists(), f"Missing log: {log_file}"
            lines = log_file.read_text().splitlines()
            assert len(lines) >= 5, f"Log too short for batch {idx}: {len(lines)} lines"
            # Verify every log line is valid JSON
            for line in lines:
                obj = json.loads(line)
                assert "stage" in obj and "ts" in obj
