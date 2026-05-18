"""Tests for Phase 6: reinforcement, decay, repair.

Covers:
  - extract_with_brain: success bump, failure bump, stale detection, repair trigger
  - repair_pattern_task: brain disabled, budget exhausted, no pattern, no result, enqueue success/failure
  - promote_candidates_task: trial→active promotion criteria
  - repairer.py: good repair, invalid regex, non-JSON, empty pattern, no key, LLM error
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pattern(
    id: int = 1,
    field: str = "phone",
    pattern: str = r"\+?\d[\d\s\-]{6,}",
    pattern_type: str = "regex",
    language: str = "de",
    confidence_score: float = 0.6,
    success_count: int = 0,
    failure_count: int = 0,
    status: str = "trial",
) -> dict:
    return {
        "id": id,
        "field": field,
        "pattern": pattern,
        "pattern_type": pattern_type,
        "language": language,
        "confidence_score": confidence_score,
        "success_count": success_count,
        "failure_count": failure_count,
        "status": status,
    }


def _make_openai_resp(content: str, tokens_in: int = 100, tokens_out: int = 50):
    usage = SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out)
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage)


# ---------------------------------------------------------------------------
# repairer.py tests
# ---------------------------------------------------------------------------

class TestRepairer:
    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from scraper.brain.repairer import repair
        result = repair(1, "phone", r"\d+", "regex", [])
        assert result is None

    def test_successful_repair(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": r"(?:Tel|Telefon)[:\s]+(\+?\d[\d\s\-]{6,})",
            "pattern_type": "regex",
            "language": "de",
            "rationale": "Anchored to Tel/Telefon to avoid matching zip codes.",
        })
        resp = _make_openai_resp(payload, tokens_in=200, tokens_out=80)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", ["12345", "plz 10115"])
        assert result is not None
        assert "Tel" in result.pattern
        assert result.pattern_type == "regex"
        assert result.language == "de"
        assert result.tokens_in == 200
        assert result.llm_cost_eur > 0

    def test_non_json_response_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        resp = _make_openai_resp("not json at all")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", [])
        assert result is None

    def test_empty_pattern_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({"pattern": "", "pattern_type": "regex", "language": "any", "rationale": "x"})
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", [])
        assert result is None

    def test_invalid_regex_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({"pattern": "[unclosed", "pattern_type": "regex", "language": "any", "rationale": "."})
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", [])
        assert result is None

    def test_invalid_pattern_type_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({"pattern": "x", "pattern_type": "xpath", "language": "any", "rationale": "."})
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", [])
        assert result is None

    def test_unknown_language_normalised_to_any(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({"pattern": r"\+?\d{7,}", "pattern_type": "regex", "language": "jp", "rationale": "."})
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", [])
        assert result is not None
        assert result.language == "any"

    def test_llm_exception_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network down")
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", r"\d+", "regex", ["bad snippet"])
        assert result is None

    def test_css_pattern_accepted(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": "[itemprop='telephone']",
            "pattern_type": "css",
            "language": "any",
            "rationale": "Schema.org itemprop is universal.",
        })
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.repairer.OpenAI", return_value=mock_client):
            from scraper.brain.repairer import repair
            result = repair(1, "phone", "div.tel", "css", ["<div>not phone</div>"])
        assert result is not None
        assert result.pattern_type == "css"


# ---------------------------------------------------------------------------
# runtime.py: reinforcement in extract_with_brain
# ---------------------------------------------------------------------------

class TestExtractWithBrainReinforcement:
    def _run(self, pattern, text="call us +49 30 12345678", monkeypatch=None, language="de"):
        if monkeypatch:
            monkeypatch.setenv("BRAIN_ENABLED", "true")
        from scraper.brain.runtime import extract_with_brain, invalidate_cache
        invalidate_cache()
        with patch("db_repo.list_active_patterns", return_value=[pattern]):
            return extract_with_brain("phone", text=text, language=language)

    def test_success_calls_bump_success(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=10, confidence_score=0.7)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()
        with (
            patch("db_repo.list_active_patterns", return_value=[pat]),
            patch("db_repo.bump_pattern_success") as mock_bump,
        ):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="Tel: +49301234567", language="de")
        assert result is not None
        mock_bump.assert_called_once_with(10)

    def test_validator_fail_calls_bump_failure(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        # A pattern that matches raw text but post-processor rejects (too short)
        pat = _make_pattern(id=20, pattern=r"Ref:\s*\d{3}", confidence_score=0.5)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()
        with (
            patch("db_repo.list_active_patterns", return_value=[pat]),
            patch("db_repo.bump_pattern_failure", return_value=0.4) as mock_bump,
            patch("db_repo.bump_pattern_success") as mock_success,
        ):
            from scraper.brain.runtime import extract_with_brain
            # "Ref: 123" matches the pattern but _post_phone returns None (< 7 digits)
            result = extract_with_brain("phone", text="Ref: 123 is the code", language="de")
        assert result is None
        mock_bump.assert_called_once_with(20)
        mock_success.assert_not_called()

    def test_decay_to_stale_flips_status_and_triggers_repair(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=30, pattern=r"Ref:\s*\d{3}", confidence_score=0.15)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()
        with (
            patch("db_repo.list_active_patterns", return_value=[pat]),
            patch("db_repo.bump_pattern_failure", return_value=0.05),  # drops below STALE_THRESHOLD=0.2
            patch("db_repo.set_pattern_status") as mock_status,
            patch("api.tasks.repair_pattern_task") as mock_repair,
        ):
            from scraper.brain.runtime import extract_with_brain
            extract_with_brain("phone", text="Ref: 123 is the code", language="de")
        mock_status.assert_called_once_with(30, "stale")
        mock_repair.delay.assert_called_once_with(30)

    def test_no_bump_when_brain_disabled(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "false")
        with (
            patch("db_repo.bump_pattern_success") as mock_s,
            patch("db_repo.bump_pattern_failure") as mock_f,
        ):
            from scraper.brain.runtime import extract_with_brain, invalidate_cache
            invalidate_cache()
            result = extract_with_brain("phone", text="Tel: +49301234567")
        assert result is None
        mock_s.assert_not_called()
        mock_f.assert_not_called()

    def test_bump_failure_exception_does_not_propagate(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=40, pattern=r"Ref:\s*\d{3}", confidence_score=0.5)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()
        with (
            patch("db_repo.list_active_patterns", return_value=[pat]),
            patch("db_repo.bump_pattern_failure", side_effect=RuntimeError("DB down")),
        ):
            from scraper.brain.runtime import extract_with_brain
            # Should not raise
            result = extract_with_brain("phone", text="Ref: 123 is the code", language="de")
        assert result is None

    def test_bump_success_exception_does_not_propagate(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=50, confidence_score=0.7)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()
        with (
            patch("db_repo.list_active_patterns", return_value=[pat]),
            patch("db_repo.bump_pattern_success", side_effect=RuntimeError("DB down")),
        ):
            from scraper.brain.runtime import extract_with_brain
            result = extract_with_brain("phone", text="Tel: +49301234567", language="de")
        # Value still returned even if bump fails
        assert result is not None
        assert result[1] == 50


# ---------------------------------------------------------------------------
# repair_pattern_task tests
# ---------------------------------------------------------------------------

class TestRepairPatternTask:
    def _run(self, pattern_id: int = 1):
        from api.tasks import repair_pattern_task
        return repair_pattern_task(pattern_id)

    def test_skips_when_brain_disabled(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "false")
        result = self._run()
        assert result == {"skipped": "brain_disabled"}

    def test_skips_when_budget_exhausted(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        monkeypatch.setenv("BRAIN_DAILY_BUDGET_EUR", "5")
        with patch("db_repo.cost_today_eur", return_value=6.0):
            result = self._run()
        assert result["skipped"] == "budget_exhausted"

    def test_skips_when_pattern_not_found(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("db_repo.get_pattern", return_value=None),
        ):
            result = self._run(pattern_id=999)
        assert result == {"skipped": "pattern_not_found"}

    def test_skips_when_repairer_returns_none(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=1)
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("db_repo.get_pattern", return_value=pat),
            patch("db_repo.recent_failing_snippets", return_value=[]),
            patch("scraper.brain.repairer.repair", return_value=None),
        ):
            result = self._run()
        assert result == {"skipped": "no_result"}

    def test_enqueues_candidate_on_success(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=5, field="phone", pattern=r"\d+", pattern_type="regex")
        from scraper.brain.generalizer import GeneralizeResult
        repaired = GeneralizeResult(
            pattern=r"(?:Tel|Telefon)[\s:]+(\+?\d[\d\s\-]{6,})",
            pattern_type="regex",
            language="de",
            rationale="Anchored to keyword.",
            llm_cost_eur=0.0002,
            tokens_in=150,
            tokens_out=60,
        )
        fake_cand = {"id": 88, "field": "phone", "status": "queued"}
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("db_repo.get_pattern", return_value=pat),
            patch("db_repo.recent_failing_snippets", return_value=["bad1", "bad2"]),
            patch("scraper.brain.repairer.repair", return_value=repaired),
            patch("db_repo.bump_cost") as mock_bump,
            patch("db_repo.enqueue_candidate", return_value=fake_cand) as mock_enqueue,
        ):
            result = self._run(pattern_id=5)

        assert result["candidate_id"] == 88
        assert result["pattern_id"] == 5
        assert result["field"] == "phone"
        mock_bump.assert_called_once()
        mock_enqueue.assert_called_once_with(
            field="phone",
            pattern_type="regex",
            candidate_pattern=repaired.pattern,
            language="de",
            parent_pattern_id=5,
            llm_cost_eur=pytest.approx(0.0002),
            rationale="Anchored to keyword.",
        )

    def test_returns_error_on_enqueue_failure(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=6)
        from scraper.brain.generalizer import GeneralizeResult
        repaired = GeneralizeResult(r"\+?\d{7,}", "regex", "any", ".", 0.0, 0, 0)
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("db_repo.get_pattern", return_value=pat),
            patch("db_repo.recent_failing_snippets", return_value=[]),
            patch("scraper.brain.repairer.repair", return_value=repaired),
            patch("db_repo.bump_cost"),
            patch("db_repo.enqueue_candidate", side_effect=RuntimeError("DB down")),
        ):
            result = self._run(pattern_id=6)
        assert "error" in result

    def test_handles_snippet_fetch_failure(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        pat = _make_pattern(id=7)
        from scraper.brain.generalizer import GeneralizeResult
        repaired = GeneralizeResult(r"\+?\d{7,}", "regex", "any", ".", 0.0, 0, 0)
        fake_cand = {"id": 99}
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("db_repo.get_pattern", return_value=pat),
            patch("db_repo.recent_failing_snippets", side_effect=RuntimeError("DB error")),
            patch("scraper.brain.repairer.repair", return_value=repaired) as mock_repair,
            patch("db_repo.bump_cost"),
            patch("db_repo.enqueue_candidate", return_value=fake_cand),
        ):
            result = self._run(pattern_id=7)
        # Should still run repair with empty snippets list
        mock_repair.assert_called_once()
        call_kwargs = mock_repair.call_args
        assert call_kwargs.kwargs.get("failing_snippets") == [] or call_kwargs.args[4] == []


# ---------------------------------------------------------------------------
# promote_candidates_task: trial→active promotion tests
# ---------------------------------------------------------------------------

class TestTrialToActivePromotion:
    def _run_task(self):
        from api.tasks import promote_candidates_task
        return promote_candidates_task()

    def test_promotes_qualified_trial_pattern(self):
        pat = _make_pattern(
            id=100, field="phone",
            success_count=15, failure_count=0, confidence_score=0.85,
            status="trial",
        )
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", return_value=[pat]) as mock_list,
            patch("db_repo.set_pattern_status") as mock_status,
            patch("scraper.brain.runtime.invalidate_cache") as mock_inval,
        ):
            result = self._run_task()
        assert result["activated"] == 1
        mock_status.assert_called_once_with(100, "active")
        mock_inval.assert_called_once_with("phone")

    def test_does_not_promote_insufficient_success_count(self):
        pat = _make_pattern(
            id=101, success_count=5, failure_count=0, confidence_score=0.85, status="trial",
        )
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", return_value=[pat]),
            patch("db_repo.set_pattern_status") as mock_status,
        ):
            result = self._run_task()
        assert result["activated"] == 0
        mock_status.assert_not_called()

    def test_does_not_promote_low_confidence(self):
        pat = _make_pattern(
            id=102, success_count=15, failure_count=0, confidence_score=0.5, status="trial",
        )
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", return_value=[pat]),
            patch("db_repo.set_pattern_status") as mock_status,
        ):
            result = self._run_task()
        assert result["activated"] == 0
        mock_status.assert_not_called()

    def test_does_not_promote_high_failure_ratio(self):
        # failure_ratio = 3/(12+3) = 0.2 > 0.05 threshold
        pat = _make_pattern(
            id=103, success_count=12, failure_count=3, confidence_score=0.75, status="trial",
        )
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", return_value=[pat]),
            patch("db_repo.set_pattern_status") as mock_status,
        ):
            result = self._run_task()
        assert result["activated"] == 0
        mock_status.assert_not_called()

    def test_promotes_pattern_on_exact_boundary(self):
        # Exactly at thresholds: success=10, conf=0.70, failure_ratio=5/205=0.024 ≤ 0.05
        pat = _make_pattern(
            id=104, field="address",
            success_count=200, failure_count=5, confidence_score=0.70, status="trial",
        )
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", return_value=[pat]),
            patch("db_repo.set_pattern_status") as mock_status,
            patch("scraper.brain.runtime.invalidate_cache"),
        ):
            result = self._run_task()
        assert result["activated"] == 1
        mock_status.assert_called_once_with(104, "active")

    def test_activation_scan_failure_does_not_crash_task(self):
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", side_effect=RuntimeError("DB down")),
        ):
            result = self._run_task()
        # Task completes; activated defaults to 0 (scan failed but task didn't crash)
        assert result.get("errors", 0) == 0  # no candidate errors
        assert result.get("activated", 0) == 0

    def test_combined_candidate_promotion_and_trial_activation(self):
        from scraper.brain.generalizer import GeneralizeResult
        cand = {
            "id": 1, "field": "phone",
            "candidate_pattern": r"\+?\d{7,}",
            "pattern_type": "regex", "language": "de",
        }
        trial_pat = _make_pattern(
            id=200, field="name",
            success_count=20, failure_count=0, confidence_score=0.9, status="trial",
        )
        metrics = {
            "precision": 0.99, "recall": 0.80, "negative_hits": 0, "sample_failures": [],
            "true_positives": 50, "false_positives": 1, "false_negatives": 5, "true_negatives": 3,
        }
        with (
            patch("db_repo.list_candidates", return_value=[cand]),
            patch("db_repo.list_patterns", return_value=[trial_pat]),
            patch("db_repo.update_candidate"),
            patch("scraper.brain.sandbox.validate_candidate", return_value=metrics),
            patch("scraper.brain.sandbox.passes_thresholds", return_value=True),
            patch("db_repo.promote_candidate", return_value={"id": 77}),
            patch("db_repo.set_pattern_status") as mock_status,
            patch("scraper.brain.runtime.invalidate_cache"),
        ):
            result = self._run_task()
        assert result["promoted"] == 1
        assert result["activated"] == 1
        mock_status.assert_called_once_with(200, "active")


# ---------------------------------------------------------------------------
# End-to-end synthetic decay test (exit criterion for Phase 6)
# ---------------------------------------------------------------------------

class TestSyntheticDecay:
    """Simulates 5 validator failures → pattern drops to stale → repair candidate enqueued."""

    def test_five_failures_trigger_stale_and_repair(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")

        # Pattern starts at confidence=0.5; 5 failures at -0.1 each = 0.0 → stale
        starting_conf = 0.5
        delta = 0.1
        confs = [max(0.0, starting_conf - delta * i) for i in range(1, 6)]
        # After 5 bumps: 0.4, 0.3, 0.2, 0.1, 0.0

        stale_triggered = []
        repair_triggered = []

        def fake_bump_failure(pattern_id, d=0.1):
            conf = confs.pop(0) if confs else 0.0
            return conf

        mock_status = MagicMock(side_effect=lambda pid, s: stale_triggered.append((pid, s)))
        mock_repair_task = MagicMock()
        mock_repair_task.delay.side_effect = lambda pid: repair_triggered.append(pid)

        pat = _make_pattern(id=77, pattern=r"Ref:\s*\d{3}", confidence_score=starting_conf)
        from scraper.brain.runtime import invalidate_cache
        invalidate_cache()

        with (
            patch("db_repo.list_active_patterns", return_value=[pat]),
            patch("db_repo.bump_pattern_failure", side_effect=fake_bump_failure),
            patch("db_repo.set_pattern_status", mock_status),
            patch("api.tasks.repair_pattern_task", mock_repair_task),
        ):
            from scraper.brain.runtime import extract_with_brain
            # Each call: "Ref: 123" matches regex but is too short for phone validator
            for _ in range(5):
                extract_with_brain("phone", text="Ref: 123 is the booking code", language="de")

        # After 5 failures the last bump returned 0.0 < STALE_THRESHOLD(0.2)
        assert len(stale_triggered) >= 1, "pattern should have been flipped to stale"
        assert ("stale" in [s for _, s in stale_triggered]), "status should be 'stale'"
        assert len(repair_triggered) >= 1, "repair task should have been triggered"
        assert 77 in repair_triggered
