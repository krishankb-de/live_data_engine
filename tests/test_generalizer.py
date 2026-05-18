"""Tests for Phase 5: generalizer.py + Celery tasks (generalize + promote).

All LLM, DB, and Celery calls are mocked. No network or Supabase required.
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

def _make_openai_resp(content: str, tokens_in: int = 100, tokens_out: int = 50):
    usage = SimpleNamespace(prompt_tokens=tokens_in, completion_tokens=tokens_out)
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    return SimpleNamespace(choices=[choice], usage=usage)


def _make_candidate(
    id: int = 1,
    field: str = "phone",
    pattern: str = r"\+?\d[\d\s\-]{6,}",
    pattern_type: str = "regex",
    language: str = "de",
) -> dict:
    return {
        "id": id,
        "field": field,
        "candidate_pattern": pattern,
        "pattern_type": pattern_type,
        "language": language,
    }


# ---------------------------------------------------------------------------
# scraper/brain/generalizer.py tests
# ---------------------------------------------------------------------------

class TestGeneralizer:
    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from scraper.brain.generalizer import generalize
        result = generalize("example.de", "phone", {"css": ".tel"})
        assert result is None

    def test_successful_regex_result(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": r"\+?\d[\d\s\-]{6,}",
            "pattern_type": "regex",
            "language": "de",
            "rationale": "Matches German phone numbers with optional +.",
        })
        resp = _make_openai_resp(payload, tokens_in=200, tokens_out=60)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("buchhandlung.de", "phone", {"css": ".tel", "page_url": "https://buchhandlung.de/kontakt"})
        assert result is not None
        assert result.pattern == r"\+?\d[\d\s\-]{6,}"
        assert result.pattern_type == "regex"
        assert result.language == "de"
        assert result.tokens_in == 200
        assert result.tokens_out == 60
        assert result.llm_cost_eur > 0

    def test_successful_css_result(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": "address, [itemprop='address']",
            "pattern_type": "css",
            "language": "any",
            "rationale": "Targets semantic address elements.",
        })
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("bookshop.de", "address", {"css": "#impressum .adr"})
        assert result is not None
        assert result.pattern_type == "css"
        assert result.language == "any"

    def test_non_json_response_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        resp = _make_openai_resp("This is not JSON at all")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {"css": ".tel"})
        assert result is None

    def test_empty_pattern_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({"pattern": "", "pattern_type": "regex", "language": "any", "rationale": ""})
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {"css": ".tel"})
        assert result is None

    def test_invalid_regex_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": r"[unclosed bracket",
            "pattern_type": "regex",
            "language": "any",
            "rationale": "Bad regex.",
        })
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {"css": ".tel"})
        assert result is None

    def test_invalid_pattern_type_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": "something",
            "pattern_type": "xpath",  # unsupported
            "language": "any",
            "rationale": "x.",
        })
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {"css": ".tel"})
        assert result is None

    def test_unknown_language_normalised_to_any(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({
            "pattern": r"\+?\d{6,}",
            "pattern_type": "regex",
            "language": "es",  # unsupported → normalised to "any"
            "rationale": ".",
        })
        resp = _make_openai_resp(payload)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {})
        assert result is not None
        assert result.language == "any"

    def test_llm_exception_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("network error")
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {"css": ".tel"})
        assert result is None

    def test_cost_calculation(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        payload = json.dumps({"pattern": r"\+?\d{6,}", "pattern_type": "regex", "language": "any", "rationale": "."})
        resp = _make_openai_resp(payload, tokens_in=1_000_000, tokens_out=1_000_000)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch("scraper.brain.generalizer.OpenAI", return_value=mock_client):
            from scraper.brain.generalizer import generalize
            result = generalize("test.de", "phone", {})
        # 1M in × $0.15 + 1M out × $0.60 = $0.75 × 0.92 EUR/USD ≈ €0.69
        assert result is not None
        assert abs(result.llm_cost_eur - 0.75 * 0.92) < 0.001


# ---------------------------------------------------------------------------
# api/tasks.py: generalize_recipe_task tests
# ---------------------------------------------------------------------------

class TestGeneralizeRecipeTask:
    def _run_task(self, domain, field, selector):
        """Run task synchronously (bypasses Celery broker)."""
        from api.tasks import generalize_recipe_task
        return generalize_recipe_task(domain, field, selector)

    def test_skips_when_brain_disabled(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "false")
        result = self._run_task("test.de", "phone", {})
        assert result == {"skipped": "brain_disabled"}

    def test_skips_when_budget_exhausted(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        monkeypatch.setenv("BRAIN_DAILY_BUDGET_EUR", "5")
        with patch("db_repo.cost_today_eur", return_value=6.0):
            result = self._run_task("test.de", "phone", {})
        assert result["skipped"] == "budget_exhausted"

    def test_skips_when_generalizer_returns_none(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("scraper.brain.generalizer.generalize", return_value=None),
        ):
            result = self._run_task("test.de", "phone", {})
        assert result == {"skipped": "no_result"}

    def test_enqueues_candidate_on_success(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        from scraper.brain.generalizer import GeneralizeResult
        fake_result = GeneralizeResult(
            pattern=r"\+?\d{7,}",
            pattern_type="regex",
            language="de",
            rationale="German phone.",
            llm_cost_eur=0.001,
            tokens_in=100,
            tokens_out=30,
        )
        fake_cand = {"id": 99, "field": "phone", "status": "queued"}
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("scraper.brain.generalizer.generalize", return_value=fake_result),
            patch("db_repo.bump_cost") as mock_bump,
            patch("db_repo.enqueue_candidate", return_value=fake_cand) as mock_enqueue,
        ):
            result = self._run_task("buchhandlung.de", "phone", {"css": ".tel"})

        assert result["candidate_id"] == 99
        assert result["field"] == "phone"
        mock_bump.assert_called_once()
        mock_enqueue.assert_called_once_with(
            field="phone",
            pattern_type="regex",
            candidate_pattern=r"\+?\d{7,}",
            language="de",
            llm_cost_eur=pytest.approx(0.001),
            rationale="German phone.",
        )

    def test_returns_error_dict_on_enqueue_failure(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        from scraper.brain.generalizer import GeneralizeResult
        fake_result = GeneralizeResult(
            pattern=r"\+?\d{7,}", pattern_type="regex", language="any",
            rationale=".", llm_cost_eur=0.0, tokens_in=0, tokens_out=0,
        )
        with (
            patch("db_repo.cost_today_eur", return_value=0.0),
            patch("scraper.brain.generalizer.generalize", return_value=fake_result),
            patch("db_repo.bump_cost"),
            patch("db_repo.enqueue_candidate", side_effect=RuntimeError("DB down")),
        ):
            result = self._run_task("test.de", "phone", {})
        assert "error" in result


# ---------------------------------------------------------------------------
# api/tasks.py: promote_candidates_task tests
# ---------------------------------------------------------------------------

class TestPromoteCandidatesTask:
    def _run_task(self):
        from api.tasks import promote_candidates_task
        return promote_candidates_task()

    def test_empty_queue_returns_zeros(self):
        with (
            patch("db_repo.list_candidates", return_value=[]),
            patch("db_repo.list_patterns", return_value=[]),
        ):
            result = self._run_task()
        assert result == {"promoted": 0, "rejected": 0, "activated": 0, "errors": 0}

    def test_promotes_passing_candidate(self):
        cand = _make_candidate(id=1, field="phone", pattern=r"\+?\d{7,}", language="de")
        metrics = {
            "precision": 0.98, "recall": 0.75,
            "true_positives": 50, "false_positives": 1,
            "false_negatives": 16, "true_negatives": 10,
            "negative_hits": 0, "sample_failures": [],
        }
        fake_pat = {"id": 77, "field": "phone", "status": "trial"}

        with (
            patch("db_repo.list_candidates", return_value=[cand]),
            patch("db_repo.update_candidate") as mock_update,
            patch("scraper.brain.sandbox.validate_candidate", return_value=metrics),
            patch("scraper.brain.sandbox.passes_thresholds", return_value=True),
            patch("db_repo.promote_candidate", return_value=fake_pat) as mock_promote,
            patch("scraper.brain.runtime.invalidate_cache") as mock_inval,
        ):
            result = self._run_task()

        assert result["promoted"] == 1
        assert result["rejected"] == 0
        mock_promote.assert_called_once_with(1)
        mock_inval.assert_called_once_with("phone")

    def test_rejects_failing_candidate(self):
        cand = _make_candidate(id=2, field="address", pattern="address", pattern_type="css")
        metrics = {
            "precision": 0.50, "recall": 0.30,
            "true_positives": 10, "false_positives": 10,
            "false_negatives": 23, "true_negatives": 5,
            "negative_hits": 3, "sample_failures": ["<div>bad</div>"],
        }
        with (
            patch("db_repo.list_candidates", return_value=[cand]),
            patch("db_repo.update_candidate") as mock_update,
            patch("scraper.brain.sandbox.validate_candidate", return_value=metrics),
            patch("scraper.brain.sandbox.passes_thresholds", return_value=False),
            patch("db_repo.promote_candidate") as mock_promote,
        ):
            result = self._run_task()

        assert result["rejected"] == 1
        assert result["promoted"] == 0
        mock_promote.assert_not_called()
        # Should call update_candidate with status='rejected'
        calls = [str(c) for c in mock_update.call_args_list]
        assert any("rejected" in c for c in calls)

    def test_handles_validation_error_gracefully(self):
        cand = _make_candidate(id=3)
        with (
            patch("db_repo.list_candidates", return_value=[cand]),
            patch("db_repo.update_candidate"),
            patch("scraper.brain.sandbox.validate_candidate", side_effect=RuntimeError("sandbox boom")),
            patch("db_repo.promote_candidate") as mock_promote,
        ):
            result = self._run_task()

        assert result["errors"] == 1
        assert result["promoted"] == 0
        mock_promote.assert_not_called()

    def test_mixed_batch(self):
        c1 = _make_candidate(id=10, field="phone")
        c2 = _make_candidate(id=11, field="address", pattern="address", pattern_type="css")
        passing = {"precision": 0.99, "recall": 0.80, "negative_hits": 0, "sample_failures": [],
                   "true_positives": 40, "false_positives": 0, "false_negatives": 10, "true_negatives": 5}
        failing = {"precision": 0.60, "recall": 0.40, "negative_hits": 2, "sample_failures": ["x"],
                   "true_positives": 6, "false_positives": 4, "false_negatives": 9, "true_negatives": 3}

        def fake_validate(candidate_pattern, field, pattern_type, language):
            return passing if field == "phone" else failing

        def fake_passes(metrics, field):
            return field == "phone"

        with (
            patch("db_repo.list_candidates", return_value=[c1, c2]),
            patch("db_repo.update_candidate"),
            patch("scraper.brain.sandbox.validate_candidate", side_effect=fake_validate),
            patch("scraper.brain.sandbox.passes_thresholds", side_effect=fake_passes),
            patch("db_repo.promote_candidate", return_value={"id": 99}),
            patch("scraper.brain.runtime.invalidate_cache"),
        ):
            result = self._run_task()

        assert result["promoted"] == 1
        assert result["rejected"] == 1
        assert result["errors"] == 0


# ---------------------------------------------------------------------------
# scraper/recipe_builder.py: _enqueue_generalize hook tests
# ---------------------------------------------------------------------------

class TestEnqueueGeneralizeHook:
    def test_hook_skips_when_brain_disabled(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "false")
        from scraper.recipe_builder import Recipe, _enqueue_generalize
        recipe = Recipe(domain="test.de", field_selectors={"phone": {"page_url": "x", "css": ".tel"}})
        with patch("api.tasks.generalize_recipe_task") as mock_task:
            _enqueue_generalize("test.de", recipe, {"phone": "+49 30 123"})
        mock_task.delay.assert_not_called()

    def test_hook_enqueues_when_brain_enabled(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        from scraper.recipe_builder import Recipe, _enqueue_generalize
        recipe = Recipe(domain="test.de", field_selectors={"phone": {"page_url": "x", "css": ".tel"}})
        mock_task = MagicMock()
        with patch("api.tasks.generalize_recipe_task", mock_task):
            _enqueue_generalize("test.de", recipe, {"phone": "+49 30 123"})
        mock_task.delay.assert_called_once_with("test.de", "phone", {"page_url": "x", "css": ".tel"})

    def test_hook_skips_fields_without_selector(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        from scraper.recipe_builder import Recipe, _enqueue_generalize
        recipe = Recipe(domain="test.de", field_selectors={})  # no selectors
        mock_task = MagicMock()
        with patch("api.tasks.generalize_recipe_task", mock_task):
            _enqueue_generalize("test.de", recipe, {"phone": "+49 30 123"})
        mock_task.delay.assert_not_called()

    def test_hook_survives_celery_unavailable(self, monkeypatch):
        monkeypatch.setenv("BRAIN_ENABLED", "true")
        from scraper.recipe_builder import Recipe, _enqueue_generalize
        recipe = Recipe(domain="test.de", field_selectors={"phone": {"page_url": "x", "css": ".tel"}})
        with patch.dict("sys.modules", {"api.tasks": None}):
            _enqueue_generalize("test.de", recipe, {"phone": "+49 30 123"})
        # Should not raise
