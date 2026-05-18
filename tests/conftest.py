"""Make team-4/ importable so `from scraper...` resolves under pytest."""

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest_plugins = ["tests.conftest_mock_site"]

# ── public constants used by E2E test modules ────────────────────────────────

# Python threaded mock server (conftest_mock_site.py, port 15174)
MOCK_SITE_URL = "http://127.0.0.1:15174"

# business.json written by the Vite dev server middleware
MOCK_DATA_FILE = ROOT / "mock-source-site" / "data" / "business.json"


def seed_phase1(prefix: str, listings: list) -> None:
    """Write phase1_listings.json so downstream phases can run without Phase 1."""
    path = ROOT / "output" / f"{prefix}phase1_listings.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(listings, ensure_ascii=False, indent=2))


# ── pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def vite_mock_site(mock_site_server):
    """Alias for mock_site_server — ensures the Python mock HTTP server is up."""
    return mock_site_server


@pytest.fixture
def e2e_prefix():
    """Unique artifact prefix so parallel/successive test runs don't collide."""
    return f"e2e_{uuid.uuid4().hex[:8]}_"


@pytest.fixture
def mock_business_snapshot():
    """Save and restore mock-source-site/data/business.json around mutations."""
    original = MOCK_DATA_FILE.read_bytes() if MOCK_DATA_FILE.exists() else None
    yield
    if original is not None:
        MOCK_DATA_FILE.write_bytes(original)


# ── CLI options ───────────────────────────────────────────────────────────────

def pytest_addoption(parser):
    parser.addoption(
        "--online",
        action="store_true",
        default=False,
        help="Run live-network integration tests against real golden URLs",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--online"):
        return
    skip_online = pytest.mark.skip(reason="needs --online flag")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)
