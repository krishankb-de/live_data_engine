"""
Phase 5 regression tests.

Two layers:
  * Offline — text snippets from the failing cases (Dante / Zadig / Ludwig Wilde).
    Always run. Lock the parser fixes for em-dash, am/pm, French hours,
    target_city reject.
  * Online — full Phase 2 + Phase 3 against real URLs. Run with `--online`.

Run:
    pytest tests/test_extraction.py -v                    # offline only
    pytest tests/test_extraction.py -v --online           # both
    python main.py --phase 5                              # offline only
    python main.py --phase 5 --online                     # both
"""

import pytest

from scraper.parsers import (
    parse_address,
    parse_opening_hours,
    parse_phone,
)


# ── Offline snippets (verbatim text from real pages) ──────────────────────

DANTE_HOMEPAGE_TEXT = """\
Dante Connection bookstore in Berlin-Kreuzberg
Homepage Webshop Events
Visit us in person, call us, or send us an email!

Dante Connection – Oranienstraße 165a – 10999 Berlin-Kreuzberg

030 – 615 76 58

info@danteconnection.de

OUR OPENING HOURS

Open Monday to Saturday,
10am-7pm
"""

ZADIG_MENTIONS_TEXT = """\
La librairie
Librairie française
Patrick Suel

Gipsstraße 12, 10119 Berlin
tel +49 (0)30. 280 999 05
fax +49 (0)30. 280 999 06
Email info@zadigbuchhandlung.de

Le lundi de 14 à 19 heures,
du mardi au vendredi de 11 à 19 heures
et le samedi de 11 à 18 heures
"""

LUDWIG_WILDE_IMPRINT_TEXT = """\
Buchhandlung Ludwig Wilde
Inhaberin: ...
Körtestraße 24, 10967 Berlin
Telefon: 030 / 691 94 08

Öffnungszeiten:
Mo-Fr 08:00-18:00
Sa 10:00-16:00
"""

DANTE_TEMPLATE_IMPRINT_TEXT = """\
Diensteanbieter:
Some Hosting Provider GmbH
Georg-Wilhelm-Straße 17, 21107 Hamburg
"""


# ── Address parser (multilingual + city validation) ────────────────────

class TestAddress:
    def test_dante_em_dash_separator(self):
        addr = parse_address(DANTE_HOMEPAGE_TEXT, target_city="Berlin")
        assert addr is not None
        assert "Oranienstraße 165a" in addr
        assert "Berlin" in addr
        assert "10999" in addr

    def test_zadig_comma_separator(self):
        addr = parse_address(ZADIG_MENTIONS_TEXT, target_city="Berlin")
        assert addr is not None
        assert "Gipsstraße 12" in addr
        assert "10119" in addr

    def test_ludwig_wilde(self):
        addr = parse_address(LUDWIG_WILDE_IMPRINT_TEXT, target_city="Berlin")
        assert addr is not None
        assert "Körtestraße 24" in addr
        assert "10967" in addr

    def test_rejects_wrong_city(self):
        """Template imprint with Hamburg address must NOT match a Berlin record."""
        addr = parse_address(DANTE_TEMPLATE_IMPRINT_TEXT, target_city="Berlin")
        assert addr is None

    def test_no_target_city_returns_first_match(self):
        addr = parse_address(DANTE_TEMPLATE_IMPRINT_TEXT)
        assert addr is not None
        assert "Hamburg" in addr


# ── Phone parser ───────────────────────────────────────────────────────

class TestPhone:
    def test_dante_em_dash(self):
        p = parse_phone(DANTE_HOMEPAGE_TEXT)
        assert p is not None
        assert p.startswith("+49") or p.startswith("030")
        # 030 615 76 58
        assert "6157658" in p.replace("+49", "0").lstrip("0").lstrip("3").lstrip("0") \
            or "6157658" in p

    def test_zadig_plus_49(self):
        p = parse_phone(ZADIG_MENTIONS_TEXT)
        assert p is not None
        assert p.startswith("+49")
        assert "28099905" in p

    def test_ludwig_wilde_slash(self):
        p = parse_phone(LUDWIG_WILDE_IMPRINT_TEXT)
        assert p is not None
        assert "6919408" in p


# ── Hours parser (DE / EN / FR) ────────────────────────────────────────

class TestHours:
    def test_dante_english_ampm(self):
        h = parse_opening_hours(DANTE_HOMEPAGE_TEXT)
        assert h, f"got {h!r}"
        # Must include Monday through Saturday at 10:00-19:00
        keys = list(h.keys())
        assert any("Mo" in k for k in keys)
        assert any("Sa" in k for k in keys)
        for v in h.values():
            assert "10:00" in v
            assert "19:00" in v

    def test_zadig_french(self):
        h = parse_opening_hours(ZADIG_MENTIONS_TEXT)
        assert h, f"got {h!r}"
        # Lundi → Mo, mardi-vendredi → Di-Fr, samedi → Sa
        all_days = " ".join(h.keys())
        assert "Mo" in all_days
        assert "Sa" in all_days

    def test_ludwig_wilde_german(self):
        h = parse_opening_hours(LUDWIG_WILDE_IMPRINT_TEXT)
        assert h, f"got {h!r}"
        # Mo-Fr expected
        joined = " ".join(h.keys()) + " " + " ".join(h.values())
        assert "Mo" in joined
        assert "08:00" in joined
        assert "16:00" in joined


# ── Online integration: full Phase 2 + Phase 3 ─────────────────────────

GOLDEN_ONLINE = [
    {
        "name":          "Dante Connection",
        "website_url":   "https://www.danteconnection.de",
        "target_city":   "Berlin",
        "expect_addr":   "Oranienstraße",
        "expect_city":   "Berlin",
    },
    {
        "name":          "Zadig",
        "website_url":   "http://www.zadigbuchhandlung.de",
        "target_city":   "Berlin",
        "expect_addr":   "Gipsstraße",
        "expect_city":   "Berlin",
    },
    {
        "name":          "Ludwig Wilde",
        "website_url":   "https://ludwigwilde.buchhandlung.de/shop",
        "target_city":   "Berlin",
        "expect_addr":   "Körtestraße",
        "expect_city":   "Berlin",
    },
]


@pytest.mark.online
@pytest.mark.parametrize("site", GOLDEN_ONLINE, ids=[s["name"] for s in GOLDEN_ONLINE])
def test_golden_url_extracts_completely(site):
    """End-to-end: Phase 2 site-map → Phase 3 layered extraction."""
    from scraper.phase2_site_map import (
        classify_links,
        extract_same_domain_links,
        fetch_sitemap_urls,
        classify_sitemap_urls,
        probe_known_paths,
        smart_fetch,
    )
    from scraper.phase3_extract import extract_site
    from scraper.phase4_diff import load_cache

    # Build site map for just this URL
    sm_urls = fetch_sitemap_urls(site["website_url"], max_index_depth=1)
    pages = classify_sitemap_urls(sm_urls) if sm_urls else {}

    if "imprint" not in pages or "contact" not in pages:
        home = smart_fetch(site["website_url"])
        if home is not None:
            final_url = getattr(home, "url", site["website_url"]) or site["website_url"]
            for cat, url in classify_links(extract_same_domain_links(home, final_url)).items():
                pages.setdefault(cat, url)

    if "imprint" not in pages or len(pages) < 2:
        pages = probe_known_paths(site["website_url"], pages, max_probes=3)

    entry = {
        "name":           site["name"],
        "website_url":    site["website_url"],
        "target_city":    site["target_city"],
        "gelbeseiten_url": "",
        "pages":          pages,
    }

    record = extract_site(entry, cache=load_cache("online_test_"))

    # Assertions — full extraction expected
    assert record["address"], f"address missing: {record}"
    assert site["expect_addr"] in record["address"], record
    assert site["expect_city"] in record["address"], record
    assert record["phone"],          f"phone missing: {record}"
    assert record["opening_hours"],  f"hours missing: {record}"
    assert record["extraction_status"] == "complete", record
