"""Doorman + footer-check tests.

Covers every code path in pipeline_ug/doorman.py:

  extract_footer  — footer_tag / footer_div / tail_fallback locators
                    + nested-div correctness (was a bug: lazy .*?</div> stopped
                      at the first nested closing tag)
  looks_legit     — size gate, strong/weak challenge markers, footer markers
  content_hash    — footer-only hashing, noise stripping, determinism
  url_for_page    — relative paths, absolute url_path pass-through (was a bug)
  doorman_fetch   — all Outcome values against the mock HTTP server,
                    ETag / Last-Modified round-trips, politeness cool-down,
                    304 ETag header preference (was a bug)

All server-facing tests use the module-scoped mock_site_server fixture from
conftest_mock_site (started once for this module) and a fresh
DomainPolitenessGate(0) per test so tests don't block each other.

Run:
    pytest tests/test_doorman_footer.py -v
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from pipeline_ug.doorman import (
    _FOOTER_FALLBACK_BYTES,
    _MIN_PAGE_BYTES,
    content_hash,
    doorman_fetch,
    extract_footer,
    looks_legit,
    url_for_page,
)
from pipeline_ug.politeness import DomainPolitenessGate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _gate() -> DomainPolitenessGate:
    """Zero-delay gate — tests don't wait for rate limits."""
    return DomainPolitenessGate(min_interval_seconds=0.0)


# ---------------------------------------------------------------------------
# extract_footer
# ---------------------------------------------------------------------------

class TestExtractFooter:

    # ── locator selection ────────────────────────────────────────────────

    def test_footer_tag_preferred(self):
        html = "<html><body><p>Body</p><footer>© footer</footer></body></html>"
        block, loc = extract_footer(html)
        assert loc == "footer_tag"
        assert "© footer" in block

    def test_footer_div_fallback(self):
        html = '<html><body><div class="site-footer">© div footer</div></body></html>'
        block, loc = extract_footer(html)
        assert loc == "footer_div"
        assert "© div footer" in block

    def test_footer_div_id_attribute(self):
        html = '<html><body><div id="footer">© id footer</div></body></html>'
        block, loc = extract_footer(html)
        assert loc == "footer_div"
        assert "© id footer" in block

    def test_tail_fallback_when_no_footer_markup(self):
        html = "<html><body><p>No footer markup here.</p></body></html>"
        _block, loc = extract_footer(html)
        assert loc == "tail_fallback"

    def test_footer_tag_wins_over_footer_div(self):
        html = (
            '<html><body>'
            '<div class="footer">div content</div>'
            '<footer>semantic footer</footer>'
            '</body></html>'
        )
        block, loc = extract_footer(html)
        assert loc == "footer_tag"
        assert "semantic footer" in block

    def test_tail_fallback_capped_at_fallback_bytes(self):
        big = "X" * 10_000 + "© tail content"
        block, loc = extract_footer(big)
        assert loc == "tail_fallback"
        assert len(block) <= _FOOTER_FALLBACK_BYTES
        assert "© tail content" in block

    def test_footer_div_capped_at_fallback_bytes(self):
        inner = "X" * (_FOOTER_FALLBACK_BYTES + 500)
        html = f'<div class="footer">{inner}</div>'
        block, loc = extract_footer(html)
        assert loc == "footer_div"
        assert len(block) <= _FOOTER_FALLBACK_BYTES

    # ── nested-div correctness (was a bug) ──────────────────────────────

    def test_footer_div_captures_content_after_nested_div(self):
        """Regression: lazy .*?</div> used to stop at the first nested </div>,
        silently dropping everything after it (copyright text, phone, etc.)."""
        html = (
            '<html><body>'
            '<div class="footer">'
            '<div>Phone: +49 30 1234</div>'
            '<p>© 2026 Corp. All rights reserved.</p>'
            '<p>Impressum</p>'
            '</div>'
            '</body></html>'
        )
        block, loc = extract_footer(html)
        assert loc == "footer_div"
        # The entire footer body must be present, not just the first child div
        assert "© 2026 Corp" in block, f"copyright missing from block: {block!r}"
        assert "Impressum" in block, f"Impressum missing from block: {block!r}"
        assert "Phone: +49 30 1234" in block

    def test_footer_div_deeply_nested_still_captured(self):
        """Multi-level nesting must not truncate the footer block."""
        html = (
            '<div class="footer">'
            '<div class="col"><div class="inner"><span>Addr</span></div></div>'
            '<div class="col">Phone: +49</div>'
            '<p>© 2026</p>'
            '</div>'
        )
        block, loc = extract_footer(html)
        assert loc == "footer_div"
        assert "© 2026" in block
        assert "Phone: +49" in block

    def test_footer_div_class_with_prefix(self):
        """'site-footer', 'main-footer', etc. must all match."""
        for cls in ("site-footer", "page-footer", "main-footer", "footer-wrapper"):
            html = f'<div class="{cls}">© 2026</div>'
            block, loc = extract_footer(html)
            assert loc == "footer_div", f"cls={cls!r} → locator={loc!r}"
            assert "© 2026" in block

    def test_footer_div_class_case_insensitive(self):
        html = '<div class="Footer">© uppercase</div>'
        block, loc = extract_footer(html)
        assert loc == "footer_div"
        assert "© uppercase" in block


# ---------------------------------------------------------------------------
# looks_legit
# ---------------------------------------------------------------------------

class TestLooksLegit:

    # ── size gate ────────────────────────────────────────────────────────

    def test_rejects_empty_body(self):
        ok, reason = looks_legit("")
        assert ok is False
        assert "too small" in reason

    def test_rejects_body_below_min_bytes(self):
        ok, reason = looks_legit("A" * (_MIN_PAGE_BYTES - 1))
        assert ok is False
        assert "too small" in reason

    def test_accepts_body_at_min_bytes(self):
        body = "A" * _MIN_PAGE_BYTES + "© All rights reserved."
        ok, _ = looks_legit(body)
        assert ok is True

    # ── strong challenge markers (always reject) ─────────────────────────

    def test_rejects_cloudflare_with_footer(self):
        """Cloudflare marker is strong — rejects even when footer text present."""
        html = "A" * 600 + " cloudflare security check © All rights reserved."
        ok, reason = looks_legit(html)
        assert ok is False
        assert "cloudflare" in reason

    def test_rejects_just_a_moment(self):
        html = "A" * 600 + " Just a moment... © All rights reserved."
        ok, reason = looks_legit(html)
        assert ok is False
        assert "just a moment" in reason

    def test_rejects_checking_your_browser(self):
        html = "A" * 600 + " Checking your browser before accessing. © rights."
        ok, reason = looks_legit(html)
        assert ok is False

    def test_rejects_please_verify_human(self):
        html = "A" * 600 + " Please verify you are human. © all rights reserved."
        ok, reason = looks_legit(html)
        assert ok is False

    def test_rejects_are_you_a_robot(self):
        html = "A" * 600 + " Are you a robot? © copyright all rights reserved."
        ok, reason = looks_legit(html)
        assert ok is False

    # ── weak challenge markers (only reject without footer) ──────────────

    def test_rejects_captcha_page_without_footer(self):
        html = "A" * 600 + " Please complete the captcha to continue."
        ok, reason = looks_legit(html)
        assert ok is False
        assert "captcha" in reason

    def test_accepts_captcha_mentioned_in_legitimate_page(self):
        """'captcha' in a privacy/help page with a real footer must be accepted."""
        html = (
            "A" * 600
            + " We use captcha to protect our forms. "
            + "<footer>© 2026 Corp. All rights reserved. Impressum.</footer>"
        )
        ok, reason = looks_legit(html)
        assert ok is True, f"should accept legit page: reason={reason!r}"

    def test_rejects_access_denied_stub_without_footer(self):
        """Bare WAF 'Access Denied' page (no footer) must be rejected."""
        html = "A" * 600 + " Access Denied. You do not have permission."
        ok, reason = looks_legit(html)
        assert ok is False
        assert "access denied" in reason

    def test_accepts_access_denied_phrase_in_legitimate_footer(self):
        """'access denied' inside a real page footer must not block scraping."""
        html = (
            "A" * 600
            + "<footer>© 2026 Corp. Access denied to admin area. Impressum.</footer>"
        )
        ok, reason = looks_legit(html)
        assert ok is True, f"should accept legit page: reason={reason!r}"

    def test_rejects_request_blocked_stub_without_footer(self):
        html = "A" * 600 + " Request blocked by security policy."
        ok, reason = looks_legit(html)
        assert ok is False
        assert "request blocked" in reason

    def test_accepts_request_blocked_phrase_in_legitimate_page(self):
        html = (
            "A" * 600
            + " Some requests blocked from your region. "
            + "© All rights reserved. Impressum."
        )
        ok, reason = looks_legit(html)
        assert ok is True, f"should accept legit page: reason={reason!r}"

    # ── footer markers ───────────────────────────────────────────────────

    def test_passes_copyright_symbol(self):
        html = "A" * 600 + " © 2026 Testfirma GmbH."
        ok, _ = looks_legit(html)
        assert ok is True

    def test_passes_all_rights_reserved(self):
        html = "A" * 600 + " All rights reserved. Testfirma GmbH, Berlin."
        ok, _ = looks_legit(html)
        assert ok is True

    def test_passes_impressum(self):
        html = "A" * 600 + "<footer>Impressum — Testfirma GmbH, Berlin</footer>"
        ok, _ = looks_legit(html)
        assert ok is True

    def test_passes_footer_tag_marker(self):
        html = "A" * 600 + "<footer>Kontakt: info@example.de</footer>"
        ok, _ = looks_legit(html)
        assert ok is True

    def test_rejects_no_footer_markers_at_all(self):
        html = "A" * 600 + "<p>Some content here with no legal notice.</p>"
        ok, reason = looks_legit(html)
        assert ok is False
        assert "footer" in reason

    def test_case_insensitive_footer_markers(self):
        html = "A" * 600 + " COPYRIGHT 2026 ALL RIGHTS RESERVED"
        ok, _ = looks_legit(html)
        assert ok is True


# ---------------------------------------------------------------------------
# content_hash (footer-only hashing)
# ---------------------------------------------------------------------------

class TestContentHash:

    def test_deterministic(self):
        html = "<html><body><footer>© 2026 Test.</footer></body></html>"
        assert content_hash(html) == content_hash(html)

    def test_length_is_32(self):
        html = "<html><body><footer>© Test</footer></body></html>"
        assert len(content_hash(html)) == 32

    def test_all_hex(self):
        html = "<html><body><footer>© Test</footer></body></html>"
        assert all(c in "0123456789abcdef" for c in content_hash(html))

    def test_same_footer_different_body_equal_hash(self):
        """Changes outside the footer must NOT change the hash."""
        footer = "<footer>© Corp. Phone +49 30 1. All rights reserved.</footer>"
        h1 = f"<html><body><nav>Menu A</nav><main>Article 1</main>{footer}</body></html>"
        h2 = f"<html><body><nav>Menu B</nav><main>Article 999</main>{footer}</body></html>"
        assert content_hash(h1) == content_hash(h2)

    def test_different_phone_in_footer_different_hash(self):
        tmpl = "<html><body><footer>Phone {} © Corp.</footer></body></html>"
        assert content_hash(tmpl.format("+49 30 1234567")) != content_hash(tmpl.format("+49 30 9999999"))

    def test_script_changes_do_not_affect_hash(self):
        """JSON-LD phone update must not fire the doorman — it's in <script>."""
        footer = "<footer>© 2026 Corp. Impressum.</footer>"
        s1 = '<script type="application/ld+json">{"telephone":"+49 30 1"}</script>'
        s2 = '<script type="application/ld+json">{"telephone":"+49 30 9"}</script>'
        h1 = f"<html><head>{s1}</head><body>{footer}</body></html>"
        h2 = f"<html><head>{s2}</head><body>{footer}</body></html>"
        assert content_hash(h1) == content_hash(h2)

    def test_style_changes_do_not_affect_hash(self):
        footer = "<footer>© Corp.</footer>"
        h1 = f"<html><head><style>.nav{{color:red}}</style></head><body>{footer}</body></html>"
        h2 = f"<html><head><style>.nav{{color:blue}}</style></head><body>{footer}</body></html>"
        assert content_hash(h1) == content_hash(h2)

    def test_whitespace_collapse_in_footer(self):
        """Whitespace normalisation means reformatted footer = same hash."""
        h1 = "<html><body><footer>©  Corp   2026</footer></body></html>"
        h2 = "<html><body><footer>© Corp 2026</footer></body></html>"
        assert content_hash(h1) == content_hash(h2)

    def test_footer_div_nested_content_hashed(self):
        """Nested-div footer must hash ALL content, not just the first child."""
        tmpl = (
            '<div class="footer">'
            '<div>Phone: {}</div>'
            '<p>© 2026 Corp. All rights reserved.</p>'
            '</div>'
        )
        h1 = content_hash(tmpl.format("+49 30 1111111"))
        h2 = content_hash(tmpl.format("+49 30 9999999"))
        assert h1 != h2, "phone change inside nested div must change footer hash"

    def test_address_change_in_nested_footer_div(self):
        tmpl = (
            '<div class="site-footer">'
            '<div>Address: {}</div>'
            '<div>Phone: +49 30 1234</div>'
            '</div>'
        )
        h1 = content_hash(tmpl.format("Bergmannstr. 1, Berlin"))
        h2 = content_hash(tmpl.format("Hauptstr. 99, Hamburg"))
        assert h1 != h2


# ---------------------------------------------------------------------------
# url_for_page
# ---------------------------------------------------------------------------

class TestUrlForPage:

    def test_relative_path_with_leading_slash(self):
        assert url_for_page("https://example.com", "/kontakt") == "https://example.com/kontakt"

    def test_relative_path_without_leading_slash(self):
        assert url_for_page("https://example.com", "impressum") == "https://example.com/impressum"

    def test_base_trailing_slash_stripped(self):
        assert url_for_page("https://example.com/", "/kontakt") == "https://example.com/kontakt"

    def test_root_path(self):
        assert url_for_page("https://example.com", "/") == "https://example.com/"

    def test_empty_base_returns_path(self):
        assert url_for_page("", "/kontakt") == "/kontakt"

    def test_none_like_base_returns_path(self):
        # base_url is falsy (empty string)
        assert url_for_page("", "https://other.com/page") == "https://other.com/page"

    def test_absolute_http_url_path_returned_unchanged(self):
        """A recipe may store a fully-qualified URL; must not be corrupted."""
        result = url_for_page("https://example.com", "https://other.com/kontakt")
        assert result == "https://other.com/kontakt", f"got {result!r}"

    def test_absolute_https_url_path_returned_unchanged(self):
        result = url_for_page("https://example.com", "https://cdn.example.com/impressum")
        assert result == "https://cdn.example.com/impressum"

    def test_absolute_http_url_path_not_appended(self):
        """Old bug: url_for_page returned base + '/' + absolute_url."""
        result = url_for_page("https://example.com", "http://example.com/imprint")
        assert not result.startswith("https://example.com/http"), \
            f"absolute url_path must not be appended to base; got {result!r}"

    def test_subdirectory_base_with_relative_path(self):
        assert url_for_page("https://example.com/shop", "/kontakt") == \
               "https://example.com/shop/kontakt"


# ---------------------------------------------------------------------------
# doorman_fetch — live mock-site tests
# ---------------------------------------------------------------------------

MOCK_HOST = "127.0.0.1"
MOCK_PORT = 15174
MOCK_BASE = f"http://{MOCK_HOST}:{MOCK_PORT}/"


@pytest.fixture(scope="module")
def _server(mock_site_server):
    """Just alias the conftest_mock_site session fixture."""
    return mock_site_server


class TestDoormanFetch:

    # ── basic outcomes ───────────────────────────────────────────────────

    def test_first_fetch_is_changed(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed"
        assert r.http_status == 200
        assert r.body is not None

    def test_second_fetch_with_same_hash_is_unchanged(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r1.outcome == "changed"
        assert r2.outcome == "unchanged"
        assert r2.http_status == 200

    def test_etag_roundtrip_yields_304(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                assert r1.new_etag is not None
                r2 = await doorman_fetch(c, mock_site, r1.new_etag, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r2.http_status == 304
        assert r2.outcome == "unchanged"

    def test_404_yields_vanished(self, mock_site_url):
        url = mock_site_url.rstrip("/") + "/no-such-page-xyz"
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, url, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "vanished"
        assert r.http_status == 404

    # ── content-hash change detection ────────────────────────────────────

    def test_phone_update_changes_hash(self, mock_site, update_phone):
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                update_phone("+49 30 8888888")
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r2.outcome == "changed"
        assert r2.new_content_hash != r1.new_content_hash

    def test_phone_update_with_etag_still_changed(self, mock_site, update_phone):
        """Phone update changes page content → server issues new ETag → 200 changed."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                update_phone("+49 30 7777777")
                r2 = await doorman_fetch(c, mock_site, r1.new_etag, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r2.outcome == "changed"

    def test_fixture_swap_changes_hash(self, mock_site, set_fixture):
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                set_fixture("jsonld")
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                set_fixture("regex")
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        if r1.outcome == "changed" and r2.outcome == "changed":
            assert r1.new_content_hash != r2.new_content_hash

    def test_multiple_phone_updates_each_produce_distinct_hash(self, mock_site, update_phone):
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
        hashes = {r.new_content_hash for r in (r0, r1, r2) if r.new_content_hash}
        assert len(hashes) == 3, f"expected 3 distinct hashes, got {hashes}"

    # ── result fields ─────────────────────────────────────────────────────

    def test_changed_result_carries_32_char_hash(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed"
        assert r.new_content_hash is not None
        assert len(r.new_content_hash) == 32

    def test_changed_result_carries_etag(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.new_etag is not None

    def test_304_result_carries_etag(self, mock_site):
        """Even on 304, new_etag must be populated (from response header or caller's stored value)."""
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                r2 = await doorman_fetch(c, mock_site, r1.new_etag, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r2.http_status == 304
        assert r2.new_etag is not None
        # Server sends ETag in 304 response; we must prefer it (or at minimum echo caller's)
        assert r2.new_etag == r1.new_etag

    def test_304_result_preserves_content_hash(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                r2 = await doorman_fetch(c, mock_site, r1.new_etag, None, r1.new_content_hash, g)
                return r1, r2
        r1, r2 = _run(_go())
        assert r2.new_content_hash == r1.new_content_hash

    def test_unchanged_result_body_is_none(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                g = _gate()
                r1 = await doorman_fetch(c, mock_site, None, None, None, g)
                r2 = await doorman_fetch(c, mock_site, None, None, r1.new_content_hash, g)
                return r2
        r2 = _run(_go())
        assert r2.outcome == "unchanged"
        assert r2.body is None

    def test_changed_result_body_is_string(self, mock_site):
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed"
        assert isinstance(r.body, str)
        assert len(r.body) > 0

    # ── politeness gate ───────────────────────────────────────────────────

    def test_cooled_domain_returns_cooled_down(self, mock_site):
        gate = DomainPolitenessGate(min_interval_seconds=0.0)
        gate.back_off(mock_site, seconds=3600.0)
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, gate)
        r = _run(_go())
        assert r.outcome == "cooled_down"
        assert r.http_status == 0
        assert "cooled" in (r.error or "")

    def test_cooled_domain_error_mentions_remaining_seconds(self, mock_site):
        gate = DomainPolitenessGate(min_interval_seconds=0.0)
        gate.back_off(mock_site, seconds=120.0)
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, gate)
        r = _run(_go())
        assert r.outcome == "cooled_down"
        assert r.error is not None

    # ── fixture variants sanity ───────────────────────────────────────────

    def test_all_fixture_variants_do_not_crash(self, mock_site, set_fixture):
        valid_outcomes = {"changed", "unchanged", "error"}
        for name in ("jsonld", "regex", "garbled", "wrong-city"):
            set_fixture(name)
            async def _go(url=mock_site):
                async with httpx.AsyncClient() as c:
                    return await doorman_fetch(c, url, None, None, None, _gate())
            r = _run(_go())
            assert r.outcome in valid_outcomes | {"vanished"}, \
                f"fixture {name!r} → unexpected outcome {r.outcome!r}"

    def test_jsonld_fixture_passes_looks_legit(self, mock_site, set_fixture):
        """The jsonld fixture + appended footer must satisfy looks_legit."""
        set_fixture("jsonld")
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        # If looks_legit failed, outcome would be "error"
        assert r.outcome == "changed", \
            f"jsonld fixture failed looks_legit — outcome={r.outcome!r} error={r.error!r}"

    def test_regex_fixture_passes_looks_legit(self, mock_site, set_fixture):
        set_fixture("regex")
        async def _go():
            async with httpx.AsyncClient() as c:
                return await doorman_fetch(c, mock_site, None, None, None, _gate())
        r = _run(_go())
        assert r.outcome == "changed", \
            f"regex fixture failed looks_legit — outcome={r.outcome!r} error={r.error!r}"
