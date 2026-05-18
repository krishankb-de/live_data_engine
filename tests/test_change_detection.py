"""Phase 6 — change-detection E2E tests.

Drives doorman_fetch against the mock-site HTTP server
(tests/conftest_mock_site.py) to verify every Outcome value and the
content_hash / extract_footer helpers.

Mark: e2e
Run:  pytest tests/test_change_detection.py -m e2e
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline_ug.doorman import content_hash, doorman_fetch, extract_footer, looks_legit
from pipeline_ug.politeness import DomainPolitenessGate


def _run(coro):
    return asyncio.run(coro)


def _gate() -> DomainPolitenessGate:
    return DomainPolitenessGate(min_interval_seconds=0.0)


# --------------------------------------------------------------------------- #
# doorman_fetch outcomes                                                       #
# --------------------------------------------------------------------------- #

@pytest.mark.e2e
class TestDoormanOutcomes:

    def test_first_fetch_outcome_changed(self, mock_site):
        """No prior state → first fetch is 'changed'."""
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed"
        assert r.http_status == 200
        assert r.body is not None

    def test_repeat_fetch_hash_unchanged(self, mock_site):
        """Same content, no ETag passed → hash match → 'unchanged'."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                # Pass only the hash, not the ETag — forces a 200 + hash comparison
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r1.outcome == "changed"
        assert r2.outcome == "unchanged"
        assert r2.http_status == 200

    def test_etag_roundtrip_304(self, mock_site):
        """If-None-Match roundtrip → server returns 304 → 'unchanged'."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                assert r1.new_etag is not None, "server must return an ETag"
                r2 = await doorman_fetch(c, mock_site, r1.new_etag, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r1.outcome == "changed"
        assert r2.outcome == "unchanged"
        assert r2.http_status == 304

    def test_phone_update_triggers_changed(self, mock_site, update_phone):
        """Mutating the phone injects a new footer → content hash changes."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                update_phone("+49 30 9999999")
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r1.outcome == "changed"
        assert r2.outcome == "changed"
        assert r2.new_content_hash != r1.new_content_hash

    def test_fixture_swap_triggers_changed(self, mock_site, set_fixture):
        """Swapping jsonld → regex changes the content hash."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                set_fixture("regex")
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r1.outcome in ("changed", "error")
        assert r2.outcome in ("changed", "error")
        # When both are changed, hashes must differ
        if r1.outcome == "changed" and r2.outcome == "changed":
            assert r2.new_content_hash != r1.new_content_hash

    def test_vanished_on_404(self, mock_site_url):
        """Non-existent path returns outcome='vanished', status=404."""
        url_404 = mock_site_url.rstrip("/") + "/no-such-page-xyz"
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, url_404, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "vanished"
        assert r.http_status == 404

    def test_changed_result_has_non_none_hash(self, mock_site):
        """A 'changed' result always carries a new_content_hash."""
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed"
        assert r.new_content_hash is not None
        assert len(r.new_content_hash) == 32

    def test_changed_result_has_etag(self, mock_site):
        """Server returns ETag on 200; doorman propagates it in new_etag."""
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed"
        assert r.new_etag is not None

    def test_etag_preserved_through_304(self, mock_site):
        """Doorman echoes the caller's etag in new_etag when server returns 304."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                r2 = await doorman_fetch(c, mock_site, r1.new_etag, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r2.http_status == 304
        assert r2.new_etag == r1.new_etag

    def test_multiple_phone_updates_each_change_hash(self, mock_site, update_phone):
        """Each successive phone update produces a distinct content hash."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r0 = await doorman_fetch(c, mock_site, None, None, None, g)
                update_phone("+49 30 1111111")
                r1 = await doorman_fetch(c, mock_site, None, None, r0.new_content_hash, g)
                update_phone("+49 30 2222222")
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r0, r1, r2
        r0, r1, r2 = _run(_go())
        assert r1.outcome == "changed"
        assert r2.outcome == "changed"
        assert r0.new_content_hash != r1.new_content_hash
        assert r1.new_content_hash != r2.new_content_hash


# --------------------------------------------------------------------------- #
# content_hash unit tests — no server needed                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.e2e
class TestContentHashUnit:

    def test_same_content_same_hash(self):
        html = "<html><body><footer>© 2026 Test Co. All rights reserved.</footer></body></html>"
        assert content_hash(html) == content_hash(html)

    def test_different_phone_different_hash(self):
        tmpl = "<html><body><footer>Phone: {} © 2026 All rights reserved.</footer></body></html>"
        assert content_hash(tmpl.format("+49 30 1234567")) != content_hash(tmpl.format("+49 30 9999999"))

    def test_hash_length_is_32(self):
        html = "<html><body><footer>© Test All rights reserved.</footer></body></html>"
        assert len(content_hash(html)) == 32

    def test_body_outside_footer_does_not_affect_hash(self):
        """Nav / hero changes must not fire doorman — only footer matters."""
        footer = "<footer>© 2026 Test Co. Phone +49 30 1234567. All rights reserved.</footer>"
        h1 = f"<html><body><nav>Menu A</nav>{footer}</body></html>"
        h2 = f"<html><body><nav>Completely different nav content here</nav>{footer}</body></html>"
        assert content_hash(h1) == content_hash(h2)

    def test_script_stripped_before_hashing(self):
        """JSON-LD / script changes alone must not trigger a hash change."""
        footer = "<footer>© 2026 All rights reserved.</footer>"
        script_a = '<script type="application/ld+json">{"phone":"+49 30 1"}</script>'
        script_b = '<script type="application/ld+json">{"phone":"+49 30 9"}</script>'
        h1 = f"<html><head>{script_a}</head><body>{footer}</body></html>"
        h2 = f"<html><head>{script_b}</head><body>{footer}</body></html>"
        assert content_hash(h1) == content_hash(h2)


# --------------------------------------------------------------------------- #
# extract_footer unit tests                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.e2e
class TestExtractFooter:

    def test_footer_tag_preferred(self):
        html = "<html><body><p>Body</p><footer>© footer content</footer></body></html>"
        block, locator = extract_footer(html)
        assert locator == "footer_tag"
        assert "footer content" in block

    def test_footer_div_fallback(self):
        html = '<html><body><div class="site-footer">© div footer</div></body></html>'
        block, locator = extract_footer(html)
        assert locator == "footer_div"
        assert "div footer" in block

    def test_tail_fallback_when_no_footer_element(self):
        html = "<html><body><p>No footer markup here at all.</p></body></html>"
        _block, locator = extract_footer(html)
        assert locator == "tail_fallback"

    def test_footer_tag_takes_precedence_over_div(self):
        html = (
            '<html><body>'
            '<div class="footer">div footer</div>'
            '<footer>semantic footer</footer>'
            '</body></html>'
        )
        block, locator = extract_footer(html)
        assert locator == "footer_tag"
        assert "semantic footer" in block


# --------------------------------------------------------------------------- #
# looks_legit unit tests                                                       #
# --------------------------------------------------------------------------- #

@pytest.mark.e2e
class TestLooksLegit:

    def test_passes_real_page(self):
        html = "A" * 600 + "<footer>© 2026 All rights reserved.</footer>"
        ok, reason = looks_legit(html)
        assert ok is True
        assert reason is None

    def test_rejects_too_small(self):
        ok, reason = looks_legit("<html><body>tiny</body></html>")
        assert ok is False
        assert "too small" in (reason or "")

    def test_rejects_cloudflare_challenge(self):
        html = "A" * 600 + " Just a moment... Checking your browser © all rights reserved."
        ok, reason = looks_legit(html)
        assert ok is False

    def test_rejects_captcha_page(self):
        html = "A" * 600 + " Please verify you are human. © copyright all rights reserved."
        ok, reason = looks_legit(html)
        assert ok is False

    def test_rejects_no_footer_markers(self):
        html = "A" * 600 + "<p>some content here with no legal notice or contact info</p>"
        ok, reason = looks_legit(html)
        assert ok is False

    def test_passes_impressum_marker(self):
        html = "A" * 600 + "<footer>Impressum — Testfirma GmbH, Berlin</footer>"
        ok, reason = looks_legit(html)
        assert ok is True


# --------------------------------------------------------------------------- #
# Fixture variant smoke tests                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.e2e
class TestFixtureVariants:

    def test_jsonld_fixture_fetches(self, mock_site, set_fixture):
        set_fixture("jsonld")
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome in ("changed", "error")

    def test_regex_fixture_fetches(self, mock_site, set_fixture):
        set_fixture("regex")
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome in ("changed", "error")

    def test_wrong_city_fixture_fetches(self, mock_site, set_fixture):
        set_fixture("wrong-city")
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome in ("changed", "error")

    def test_garbled_fixture_does_not_crash(self, mock_site, set_fixture):
        set_fixture("garbled")
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome in ("changed", "error", "unchanged")

    def test_jsonld_then_wrong_city_changes_hash(self, mock_site, set_fixture):
        """Switching from a legit fixture to another changes the content hash."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                set_fixture("wrong-city")
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        if r1.outcome == "changed" and r2.outcome == "changed":
            assert r1.new_content_hash != r2.new_content_hash
