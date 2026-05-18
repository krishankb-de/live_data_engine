"""Phase 6 — mock-site HTTP fixtures for change-detection E2E tests.

Provides a lightweight threaded HTTP server that:
  - Serves HTML fixture files with ETag / Last-Modified headers
  - Returns 304 when If-None-Match matches the current ETag
  - Exposes GET /set-fixture?name=<jsonld|regex|garbled|wrong-city> to swap HTML
  - Exposes POST /update-phone with JSON {"phone": "..."} to patch active page

Fixtures:
  mock_site_server  (session)  — start/stop server thread
  mock_site_url     (session)  — "http://127.0.0.1:<port>/"
  mock_site         (function) — resets to jsonld baseline, yields base URL
  set_fixture       (function) — callable(name) that hot-swaps the HTML fixture
  update_phone      (function) — callable(phone) that injects phone into active page
"""
from __future__ import annotations

import hashlib
import json
import re as _re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_LISTING_RE = _re.compile(r"^/listing/(jsonld|regex|brain|llm)/(\d+)$")


def _generate_listing_html(kind: str, n: int) -> str:
    """Generate unique deterministic HTML per (kind, n) for 300-listing tests."""
    if kind == "jsonld":
        phone = f"+49 30 {100000 + n}"
        street = f"Hauptstraße {n % 200 + 1}"
        postal = f"{10000 + n % 900}"
        opens = f"{8 + n % 3:02d}:00"
        closes = f"{17 + n % 3:02d}:00"
        name = f"Testfirma {n} Berlin"
        return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"/><title>{name}</title>
<script type="application/ld+json">
{{
  "@context":"https://schema.org","@type":"LocalBusiness",
  "name":"{name}","telephone":"{phone}",
  "address":{{"@type":"PostalAddress","streetAddress":"{street}",
    "addressLocality":"Berlin","postalCode":"{postal}","addressCountry":"DE"}},
  "openingHoursSpecification":[{{"@type":"OpeningHoursSpecification",
    "dayOfWeek":["Monday","Tuesday","Wednesday","Thursday","Friday"],
    "opens":"{opens}","closes":"{closes}"}}]
}}
</script></head>
<body><h1>{name}</h1>
<p>Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler.</p>
<footer><p>© {name}. Impressum. Alle Rechte vorbehalten.</p></footer>
</body></html>"""

    if kind == "regex":
        phone = f"+49 30 {200000 + n}"
        street = f"Berliner Straße {n % 200 + 1}"
        postal = f"{10000 + n % 900}"
        name = f"Regex Firma {n} Berlin"
        return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"/><title>{name}</title></head>
<body><h1>{name}</h1>
<div class="impressum">
  <p>{name} GbR<br/>{street}<br/>{postal} Berlin</p>
  <p>Telefon: {phone}</p>
  <p>Öffnungszeiten: Mo-Fr 09:00-18:00</p>
</div>
<p>Wir bieten Dienstleistungen in Berlin an. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler.</p>
<footer><p>© {name}. Impressum. Alle Rechte vorbehalten.</p></footer>
</body></html>"""

    if kind == "brain":
        phone = f"+49 30 {300000 + n}"
        street = f"Testweg {n % 100 + 1}"
        postal = f"1{n % 9000 + 1000}"
        name = f"Brain Firma {n}"
        return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"/><title>{name}</title></head>
<body><h1>{name}</h1>
<div class="contact-block">
  <span class="tel" data-value="{phone}">{phone}</span>
  <div class="addr">{street}, {postal} Berlin</div>
  <div class="hrs">Mo-Fr 10:00-17:00</div>
</div>
<p>Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler.</p>
<footer><p>Impressum. Alle Rechte vorbehalten.</p></footer>
</body></html>"""

    # kind == "llm" — obfuscated data elements that bypass standard regex
    phone = f"+49 30 {400000 + n}"
    street = f"Serviceweg {n % 100 + 1}"
    postal = f"1{n % 9000 + 1000}"
    name = f"LLM Service {n}"
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"/><title>{name}</title></head>
<body><h1>{name}</h1>
<section id="info">
  <data class="p" value="{phone}">Kontakt</data>
  <data class="a">{street}, {postal} Berlin</data>
  <data class="h">Mo bis Fr, neun bis achtzehn Uhr</data>
</section>
<p>Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler. Filler.</p>
<footer><p>Impressum.</p></footer>
</body></html>"""

import pytest

ROOT = Path(__file__).parent.parent
_FIXTURES_DIR = ROOT / "mock-source-site" / "fixtures"

_FIXTURE_FILES = {
    "jsonld": _FIXTURES_DIR / "jsonld.html",
    "regex": _FIXTURES_DIR / "regex.html",
    "garbled": _FIXTURES_DIR / "garbled.html",
    "wrong-city": _FIXTURES_DIR / "wrong-city.html",
}

MOCK_SITE_PORT = 15174
MOCK_SITE_HOST = "127.0.0.1"


def _etag(content: str) -> str:
    return '"' + hashlib.sha256(content.encode()).hexdigest()[:16] + '"'


class _ServerState:
    """Mutable shared state for the mock HTTP server."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._base_html: str = ""
        self._base_sig: str = ""
        self._phone_override: str | None = None
        self._load("jsonld")

    def _load(self, name: str) -> None:
        self._base_html = _FIXTURE_FILES[name].read_text()
        # Short signature so different fixtures produce different footer hashes
        self._base_sig = hashlib.sha256(self._base_html.encode()).hexdigest()[:8]
        self._phone_override = None

    def get_html(self) -> str:
        import re as _re
        with self.lock:
            html = self._base_html
            phone_part = f" {self._phone_override}" if self._phone_override else ""
            # Patch JSON-LD telephone field so Tier-1 extraction sees the override.
            if self._phone_override:
                html = _re.sub(
                    r'"telephone"\s*:\s*"[^"]*"',
                    f'"telephone": "{self._phone_override}"',
                    html,
                )
            # Always inject a <footer> so looks_legit passes and content_hash
            # reflects both which fixture is active and the current phone value.
            html += (
                f"\n<footer>"
                f"<p data-sig='{self._base_sig}'>{phone_part}"
                f" © All rights reserved. Impressum.</p>"
                f"</footer>"
            )
            return html

    def set_fixture(self, name: str) -> None:
        with self.lock:
            self._load(name)

    def update_phone(self, phone: str) -> None:
        with self.lock:
            self._phone_override = phone

    def reset(self) -> None:
        with self.lock:
            self._load("jsonld")


_state = _ServerState()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # suppress per-request noise
        pass

    # ------------------------------------------------------------------ GET --

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        # /set-fixture?name=<name>
        if parsed.path == "/set-fixture":
            name = qs.get("name", ["jsonld"])[0]
            if name not in _FIXTURE_FILES:
                self._send(400, "text/plain", f"unknown fixture: {name}")
                return
            _state.set_fixture(name)
            self._send(200, "application/json", json.dumps({"fixture": name}))
            return

        # /listing/{jsonld|regex|brain|llm}/{n}  — dynamic per-listing HTML
        m = _LISTING_RE.match(parsed.path)
        if m:
            html = _generate_listing_html(m.group(1), int(m.group(2)))
            etag = _etag(html)
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.end_headers()
                return
            self._send(200, "text/html", html, {"ETag": etag})
            return

        # Any path that isn't a control endpoint serves the main page HTML.
        # This allows SITE_URL = f"{MOCK_SITE_URL}/site" to behave like a real
        # multi-page site where every URL resolves to the same mock content.
        html = _state.get_html()
        etag = _etag(html)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self._send(200, "text/html", html, {"ETag": etag})

    # ----------------------------------------------------------------- POST --

    def do_POST(self):
        if urlparse(self.path).path == "/update-phone":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            try:
                phone = json.loads(body)["phone"]
                _state.update_phone(phone)
                self._send(200, "application/json", json.dumps({"ok": True}))
            except Exception as exc:
                self._send(400, "text/plain", str(exc))
            return
        self.send_response(404)
        self.end_headers()

    # ---------------------------------------------------------------- util --

    def _send(self, status: int, ctype: str, body: str, extra: dict | None = None) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(encoded)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(encoded)


# --------------------------------------------------------------------------- #
# Pytest fixtures                                                              #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def mock_site_server():
    server = ThreadingHTTPServer((MOCK_SITE_HOST, MOCK_SITE_PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()


@pytest.fixture(scope="session")
def mock_site_url(mock_site_server):
    return f"http://{MOCK_SITE_HOST}:{MOCK_SITE_PORT}/"


@pytest.fixture
def mock_site(mock_site_url):
    """Per-test: reset to jsonld baseline, yield base URL."""
    _state.reset()
    yield mock_site_url


@pytest.fixture
def set_fixture(mock_site_server):
    """Return callable(name) that hot-swaps the active HTML fixture."""
    def _set(name: str) -> None:
        _state.set_fixture(name)
    return _set


@pytest.fixture
def update_phone(mock_site_server):
    """Return callable(phone) that injects a phone number into the active page."""
    def _update(phone: str) -> None:
        _state.update_phone(phone)
    return _update
