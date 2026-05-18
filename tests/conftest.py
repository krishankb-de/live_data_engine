"""Make team-4/ importable so `from scraper...` resolves under pytest."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest_plugins = ["tests.conftest_mock_site"]


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
    import pytest
    skip_online = pytest.mark.skip(reason="needs --online flag")
    for item in items:
        if "online" in item.keywords:
            item.add_marker(skip_online)
