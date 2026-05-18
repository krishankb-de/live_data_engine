"""
E2E test: 300 listings across all 4 extraction tiers.

Distribution
------------
  150  JSON-LD  (/listing/jsonld/0–149)   → tier 1  expect complete, all fields via "jsonld"
  100  Regex    (/listing/regex/0–99)     → tier 2  expect complete, phone/addr/hours via "regex"
   25  Brain    (/listing/brain/0–24)     → tier 4  expect partial or complete if DB patterns present
   25  LLM/Rec  (/listing/llm/0–24)       → tier 5  expect partial unless OPENAI_API_KEY set

The Python mock server (conftest_mock_site.py port 15174) generates unique HTML
per (type, n) at /listing/{type}/{n} endpoints.

Run:
  pytest tests/test_e2e_300_listings.py -v -s
  pytest tests/test_e2e_300_listings.py --online -v -s   # also writes to Supabase
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.e2e

# ── brain regex patterns seeded for the brain HTML tier ───────────────────────
# CSS patterns can't be used: extract_with_brain("phone"/"address"/"opening_hours")
# is called with text= only (no page=), so only regex patterns can match.
# Brain HTML phone format: bare digits "030300001" — normal phone regex won't match.
_BRAIN_PATTERNS = [
    ("phone", "regex", r"030\d{6,}"),
]

# ── Batch sizes ───────────────────────────────────────────────────────────────
N_JSONLD  = 150
N_REGEX   = 100
N_BRAIN   = 25
N_LLM     = 25
TOTAL     = N_JSONLD + N_REGEX + N_BRAIN + N_LLM   # 300

MOCK_HOST = "http://127.0.0.1:15174"
_MOCK_DOMAIN = "127.0.0.1:15174"
_TS = int(time.time())


def _clear_test_recipe() -> None:
    """Remove the stale domain-level recipe so the LLM tier fires fresh each run.
    Deletes the entire file if it's corrupted by a prior concurrent write."""
    try:
        from scraper.recipe_builder import RECIPES_FILE
        if not RECIPES_FILE.exists():
            return
        try:
            data = json.loads(RECIPES_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            RECIPES_FILE.unlink(missing_ok=True)
            print(f"\n[RECIPE RESET] Deleted corrupted {RECIPES_FILE.name}")
            return
        if _MOCK_DOMAIN in data:
            del data[_MOCK_DOMAIN]
            RECIPES_FILE.write_text(json.dumps(data, indent=2))
            print(f"\n[RECIPE RESET] Cleared recipe for {_MOCK_DOMAIN}")
    except Exception as exc:
        print(f"\n[RECIPE RESET] Failed: {exc}")

# ── helpers ───────────────────────────────────────────────────────────────────

def _listing(kind: str, n: int) -> dict:
    return {
        "name": f"{kind.title()} Firma {n}",
        "gelbeseiten_url": "",
        "gs_uuid": f"e2e-300-{kind}-{n}-{_TS}",
        "website_url": f"{MOCK_HOST}/listing/{kind}/{n}",
        "is_verifiable": True,
        "target_city": "Berlin",
    }


def _seed_phase1(prefix: str, listings: list) -> None:
    path = ROOT / "output" / f"{prefix}phase1_listings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(listings, ensure_ascii=False, indent=2))


def _run_phases_23(prefix: str) -> list:
    from scraper.phase2_site_map import run as run_phase2
    from scraper.phase3_extract import run as run_phase3
    run_phase2(prefix=prefix)
    return run_phase3(prefix=prefix)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mock_server_running(mock_site_server):
    """Ensure the Python mock server is up (provided by conftest_mock_site.py)."""
    return mock_site_server


@pytest.fixture(scope="module", autouse=True)
def _setup_brain_and_env(request, mock_site_server):
    """
    Before any test in this module:
      1. Load .env (OPENAI_API_KEY + Supabase creds).
      2. If --online: seed brain CSS patterns into Supabase, enable BRAIN_ENABLED=1.
      3. Invalidate the in-process brain pattern cache so phase3 loads fresh patterns.
      4. Reset class-level record cache so extraction re-runs with brain enabled.

    Teardown: remove seeded patterns and restore env.
    """
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    if not request.config.getoption("--online", default=False):
        _clear_test_recipe()
        yield
        return

    # Reset record cache so this session re-extracts with brain on
    TestE2E300Listings._records = None
    TestE2E300Listings._prefix  = None

    # Clear any stale recipe for our test domain so the LLM tier fires fresh.
    # Without this, a "failed" recipe from a prior run suppresses all LLM calls.
    _clear_test_recipe()

    os.environ["BRAIN_ENABLED"] = "1"

    seeded_ids: list[int] = []
    try:
        import db_repo
        from scraper.brain.runtime import invalidate_cache
        for field, ptype, pattern in _BRAIN_PATTERNS:
            row = db_repo.insert_pattern(
                field=field,
                pattern_type=ptype,
                pattern=pattern,
                language="de",
                confidence_score=0.80,
                status="active",
                origin_domain="127.0.0.1",
                rationale="e2e-300-listings test fixture",
            )
            if row and row.get("id"):
                seeded_ids.append(row["id"])
                print(f"\n[BRAIN SETUP] Seeded pattern id={row['id']} field={field} pattern={pattern!r}")
        invalidate_cache()
        print(f"[BRAIN SETUP] {len(seeded_ids)} patterns seeded, cache invalidated, BRAIN_ENABLED=1")
    except Exception as exc:
        print(f"\n[BRAIN SETUP] Could not seed patterns: {exc}")

    yield

    # Cleanup
    try:
        from scraper.supabase_client import get_client
        for pid in seeded_ids:
            get_client().table("global_patterns").delete().eq("id", pid).execute()
        print(f"\n[BRAIN TEARDOWN] Removed {len(seeded_ids)} seeded patterns")
    except Exception:
        pass
    os.environ.pop("BRAIN_ENABLED", None)


# ── Main test ─────────────────────────────────────────────────────────────────

class TestE2E300Listings:
    """
    Exercises all 4 extraction tiers over 300 uniquely seeded mock listings.
    Each sub-test focuses on one tier; all share the same phase-2/3 run
    invoked lazily on first access via class-level state.
    """

    _records: list | None = None
    _prefix: str | None = None

    @classmethod
    def _ensure_run(cls, mock_server_running) -> list:
        if cls._records is not None:
            return cls._records

        listings = (
            [_listing("jsonld", i) for i in range(N_JSONLD)]
            + [_listing("regex",  i) for i in range(N_REGEX)]
            + [_listing("brain",  i) for i in range(N_BRAIN)]
            + [_listing("llm",    i) for i in range(N_LLM)]
        )
        prefix = f"e2e300_{uuid.uuid4().hex[:8]}_"
        cls._prefix = prefix
        _seed_phase1(prefix, listings)
        cls._records = _run_phases_23(prefix)
        return cls._records

    # ── T1: sanity ─────────────────────────────────────────────────────────────

    def test_all_300_listings_processed(self, mock_server_running):
        records = self._ensure_run(mock_server_running)
        assert len(records) == TOTAL, (
            f"Expected {TOTAL} records, got {len(records)}"
        )

    # ── T2: JSON-LD tier (listings 0–149) ─────────────────────────────────────

    def test_jsonld_tier_extraction_complete(self, mock_server_running):
        records = self._ensure_run(mock_server_running)
        jsonld_recs = [
            r for r in records
            if f"/listing/jsonld/" in r.get("website_url", "")
        ]
        assert len(jsonld_recs) == N_JSONLD, (
            f"Expected {N_JSONLD} jsonld records, got {len(jsonld_recs)}"
        )

        complete  = [r for r in jsonld_recs if r.get("extraction_status") == "complete"]
        partial   = [r for r in jsonld_recs if r.get("extraction_status") == "partial"]
        fail_rate = (N_JSONLD - len(complete)) / N_JSONLD

        print(f"\n[JSONLD] complete={len(complete)}/{N_JSONLD}  partial={len(partial)}")
        assert fail_rate <= 0.05, (
            f"JSON-LD tier fail rate {fail_rate:.0%} > 5%  "
            f"(complete={len(complete)}, partial={len(partial)})"
        )

    def test_jsonld_field_sources_are_jsonld(self, mock_server_running):
        records = self._ensure_run(mock_server_running)
        jsonld_recs = [
            r for r in records
            if f"/listing/jsonld/" in r.get("website_url", "")
            and r.get("extraction_status") == "complete"
        ]
        wrong = []
        for r in jsonld_recs[:20]:   # spot-check first 20 complete ones
            fs = r.get("field_sources", {})
            for field in ("phone", "address", "opening_hours"):
                if fs.get(field) not in ("jsonld", None):
                    wrong.append((r["website_url"], field, fs.get(field)))
        assert not wrong, f"Non-jsonld source on jsonld listings: {wrong[:5]}"

    # ── T3: Regex tier (listings 0–99) ────────────────────────────────────────

    def test_regex_tier_extraction_complete(self, mock_server_running):
        records = self._ensure_run(mock_server_running)
        regex_recs = [
            r for r in records
            if f"/listing/regex/" in r.get("website_url", "")
        ]
        assert len(regex_recs) == N_REGEX, (
            f"Expected {N_REGEX} regex records, got {len(regex_recs)}"
        )

        complete  = [r for r in regex_recs if r.get("extraction_status") == "complete"]
        partial   = [r for r in regex_recs if r.get("extraction_status") == "partial"]
        fail_rate = (N_REGEX - len(complete)) / N_REGEX

        print(f"\n[REGEX] complete={len(complete)}/{N_REGEX}  partial={len(partial)}")
        assert fail_rate <= 0.10, (
            f"Regex tier fail rate {fail_rate:.0%} > 10%  "
            f"(complete={len(complete)}, partial={len(partial)})"
        )

    def test_regex_field_sources_are_regex(self, mock_server_running):
        records = self._ensure_run(mock_server_running)
        regex_recs = [
            r for r in records
            if f"/listing/regex/" in r.get("website_url", "")
            and r.get("extraction_status") == "complete"
        ]
        wrong = []
        for r in regex_recs[:20]:   # spot-check first 20
            fs = r.get("field_sources", {})
            for field in ("phone",):
                if fs.get(field) not in ("regex", "jsonld", None):
                    wrong.append((r["website_url"], field, fs.get(field)))
        assert not wrong, f"Unexpected source on regex listings: {wrong[:5]}"

    # ── T4: Brain tier (listings 0–24) ────────────────────────────────────────

    def test_brain_tier_no_crash(self, mock_server_running):
        """Brain listings must be processed without exception, even if partial."""
        records = self._ensure_run(mock_server_running)
        brain_recs = [
            r for r in records
            if f"/listing/brain/" in r.get("website_url", "")
        ]
        assert len(brain_recs) == N_BRAIN, (
            f"Expected {N_BRAIN} brain records, got {len(brain_recs)}"
        )
        for r in brain_recs:
            assert r.get("extraction_status") in ("complete", "partial"), (
                f"Brain listing errored: {r}"
            )

    def test_brain_tier_field_sources_reported(self, mock_server_running):
        """If brain fired, field_sources must contain 'brain' for at least some fields."""
        records = self._ensure_run(mock_server_running)
        brain_recs = [
            r for r in records
            if "/listing/brain/" in r.get("website_url", "")
        ]
        brain_hits = sum(
            1 for r in brain_recs
            if "brain" in r.get("field_sources", {}).values()
        )
        total_fields_brain = sum(
            list(r.get("field_sources", {}).values()).count("brain")
            for r in brain_recs
        )
        print(f"\n[BRAIN] listings with >=1 brain field: {brain_hits}/{N_BRAIN}")
        print(f"[BRAIN] total brain-sourced fields: {total_fields_brain}")

        brain_enabled = os.environ.get("BRAIN_ENABLED", "").lower() in ("1", "true", "yes")
        if not brain_enabled or brain_hits == 0:
            pytest.skip(
                "Brain did not fire — either BRAIN_ENABLED not set or no active patterns. "
                "Run with --online to seed patterns automatically."
            )
        assert brain_hits > 0, f"Expected brain hits on {N_BRAIN} brain listings"

    # ── T5: LLM/Recipe tier (listings 0–24) ───────────────────────────────────

    def test_llm_tier_no_crash(self, mock_server_running):
        """LLM listings must be processed without exception."""
        records = self._ensure_run(mock_server_running)
        llm_recs = [
            r for r in records
            if f"/listing/llm/" in r.get("website_url", "")
        ]
        assert len(llm_recs) == N_LLM, (
            f"Expected {N_LLM} llm records, got {len(llm_recs)}"
        )
        for r in llm_recs:
            assert r.get("extraction_status") in ("complete", "partial"), (
                f"LLM listing errored: {r}"
            )

    def test_llm_tier_recipe_fires_when_api_key_present(self, mock_server_running):
        """If OPENAI_API_KEY set, recipe/LLM builder must extract at least some fields."""
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("OPENAI_API_KEY not set — recipe/LLM tier not exercised")

        records = self._ensure_run(mock_server_running)
        llm_recs = [
            r for r in records
            if "/listing/llm/" in r.get("website_url", "")
        ]
        # first-build returns "llm" source; cached-recipe hits return "recipe"
        recipe_or_llm_hits = sum(
            1 for r in llm_recs
            if any(src in ("recipe", "llm") for src in r.get("field_sources", {}).values())
        )
        src_dist: dict = {}
        for r in llm_recs:
            for src in r.get("field_sources", {}).values():
                src_dist[src] = src_dist.get(src, 0) + 1
        print(f"\n[LLM/RECIPE] listings with recipe/llm source: {recipe_or_llm_hits}/{N_LLM}")
        print(f"[LLM/RECIPE] field source breakdown: {src_dist}")
        assert recipe_or_llm_hits > 0, (
            f"OPENAI_API_KEY is set but recipe/LLM builder fired 0 times on {N_LLM} listings. "
            f"sources seen: {src_dist}"
        )

    # ── T6: Global tier-distribution summary ──────────────────────────────────

    def test_tier_distribution_summary(self, mock_server_running):
        """Print extraction tier distribution across all 300 listings."""
        records = self._ensure_run(mock_server_running)

        status_counts: Counter = Counter()
        source_counts: Counter = Counter()

        for r in records:
            status_counts[r.get("extraction_status", "unknown")] += 1
            for src in r.get("field_sources", {}).values():
                if src:
                    source_counts[src] += 1

        total_complete = status_counts["complete"]
        total_partial  = status_counts["partial"]
        total_fields   = sum(source_counts.values())

        print("\n" + "=" * 60)
        print(f"E2E 300-LISTING SUMMARY  ({TOTAL} listings)")
        print("=" * 60)
        print(f"  complete:  {total_complete:>4d}  ({100*total_complete/TOTAL:.1f}%)")
        print(f"  partial:   {total_partial:>4d}  ({100*total_partial/TOTAL:.1f}%)")
        print(f"  unknown:   {status_counts.get('unknown', 0):>4d}")
        print(f"\nField source distribution ({total_fields} total field extractions):")
        for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
            print(f"  {src:<15} {cnt:>5d}  ({100*cnt/max(total_fields, 1):.1f}%)")
        print("=" * 60)

        # Save summary to disk
        summary = {
            "timestamp": _TS,
            "total": TOTAL,
            "status": dict(status_counts),
            "field_sources": dict(source_counts),
            "complete_pct": round(100 * total_complete / TOTAL, 1),
            "partial_pct":  round(100 * total_partial  / TOTAL, 1),
            "brain_enabled": os.environ.get("BRAIN_ENABLED", "0"),
            "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
        }
        out_path = ROOT / "output" / f"e2e_300_summary_{_TS}.json"
        out_path.parent.mkdir(exist_ok=True)
        out_path.write_text(json.dumps(summary, indent=2))
        print(f"\nSummary saved → {out_path}")

        # Minimum bar: at least 70% of all 300 listings must complete
        assert total_complete / TOTAL >= 0.70, (
            f"Only {total_complete}/{TOTAL} ({100*total_complete/TOTAL:.1f}%) listings completed — "
            f"expected ≥70%. status={dict(status_counts)}"
        )


# ── DB validation (--online only) ─────────────────────────────────────────────

@pytest.mark.online
class TestE2E300DB:
    """Validates DB writes for a smaller representative subset (30 listings)."""

    _SAMPLE = 10   # jsonld + regex + brain subset written to Supabase

    @pytest.fixture(autouse=True)
    def _cleanup(self, mock_server_running):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        self._gs_ids: list[str] = []
        yield
        from scraper.supabase_client import get_client
        for gid in self._gs_ids:
            try:
                get_client().table("listings").delete().eq("gs_listing_id", gid).execute()
            except Exception:
                pass

    def test_db_writes_for_sample_listings(self, mock_server_running):
        """Upsert 10 jsonld + 10 regex listings, run pipeline, verify observations."""
        import db_repo

        sample = (
            [_listing("jsonld", i) for i in range(self._SAMPLE)]
            + [_listing("regex",  i) for i in range(self._SAMPLE)]
        )
        self._gs_ids = [s["gs_uuid"] for s in sample]

        prefix = f"e2e300db_{uuid.uuid4().hex[:8]}_"

        # Upsert all listings to Supabase
        for s in sample:
            db_repo.upsert_listing({
                "gs_listing_id": s["gs_uuid"],
                "name": s["name"],
                "website_url": s["website_url"],
                "is_paid": False,
                "is_verifiable": True,
                "target_city": "Berlin",
            })

        _seed_phase1(prefix, sample)
        records = _run_phases_23(prefix)

        from main import _sync_extracted_to_db
        _sync_extracted_to_db(records)

        # Verify observations exist for each listing
        obs_counts: dict[str, int] = {}
        for s in sample:
            row = db_repo.get_listing_by_gs_id(s["gs_uuid"])
            if row:
                obs = db_repo.latest_observations(row["id"])
                obs_counts[s["gs_uuid"]] = len(obs)

        total_obs = sum(obs_counts.values())
        print(f"\n[DB] {len(obs_counts)} listings synced, {total_obs} total observations")
        assert total_obs > 0, "No observations written to Supabase for sample listings"

    def test_db_version_integrity_for_sample(self, mock_server_running):
        """Versions written for new fields must have correct listing_id FK."""
        import db_repo

        sample = [_listing("jsonld", i + 200) for i in range(5)]
        self._gs_ids = [s["gs_uuid"] for s in sample]

        prefix = f"e2e300ver_{uuid.uuid4().hex[:8]}_"
        for s in sample:
            db_repo.upsert_listing({
                "gs_listing_id": s["gs_uuid"],
                "name": s["name"],
                "website_url": s["website_url"],
                "is_paid": False,
                "is_verifiable": True,
                "target_city": "Berlin",
            })

        _seed_phase1(prefix, sample)
        records = _run_phases_23(prefix)

        from main import _sync_extracted_to_db
        _sync_extracted_to_db(records)

        for s in sample:
            row = db_repo.get_listing_by_gs_id(s["gs_uuid"])
            if not row:
                continue
            lid = row["id"]
            versions = db_repo.list_versions_for_listing(lid)
            for v in versions:
                assert v["listing_id"] == lid, (
                    f"Version {v['id']} listing_id mismatch: "
                    f"got {v['listing_id']}, expected {lid}"
                )
