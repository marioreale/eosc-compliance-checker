"""Test harness.

Two kinds of test are worth writing for a scanner like this:

1. Fast, hermetic rule tests over hand-written HTML — no browser, no network.
   These are the ones you will run hundreds of times a day.
2. One end-to-end test through a real browser against a local fixture server,
   to prove the collector and the rules agree about reality.

Both use the same evidence model, which is the point of splitting collection
from evaluation.
"""

from __future__ import annotations

import functools
import socket
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from checker import extract
from checker.models import EvidenceBundle, PageEvidence
from checker.registry import Target

FIXTURE_PAGES = Path(__file__).parent / "fixtures" / "pages"
FIXTURE_PORT = 8009


def _port_is_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def fixture_server() -> str:
    """Serve tests/fixtures/pages on 127.0.0.1:8009 for the duration of the session.

    Reuses an already-running server on that port (e.g. one you started by hand
    with `python -m tests.serve_fixtures` for manual CLI runs) instead of failing
    with 'address already in use'.
    """
    url = f"http://127.0.0.1:{FIXTURE_PORT}"
    if _port_is_open(FIXTURE_PORT):
        yield url
        return

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(FIXTURE_PAGES))
    httpd = ThreadingHTTPServer(("127.0.0.1", FIXTURE_PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield url
    httpd.shutdown()
    httpd.server_close()


def page_from_html(
    html: str,
    url: str = "https://node.example.org/",
    *,
    depth: int = 0,
) -> PageEvidence:
    """Build a PageEvidence from raw HTML, exactly as the collector would.

    This is the workhorse of rule testing: it lets a rule be tested against a
    twelve-line HTML snippet in microseconds.
    """
    return PageEvidence(
        requested_url=url,
        final_url=url,
        status=200,
        ok=True,
        depth=depth,
        html=html,
        title=extract.extract_title(html),
        lang_attr=extract.extract_lang(html),
        hreflang=extract.extract_hreflang(html),
        links=extract.extract_links(html, url),
        controls=extract.extract_controls(html, url),
        text=extract.extract_text(html),
        content_sha256=extract.sha256_text(html),
    )


def bundle_from_html(
    html: str,
    *,
    url: str = "https://node.example.org/",
    target_id: str = "test-node",
    declarations: dict | None = None,
    link_liveness: dict[str, int] | None = None,
    catalogue_services: list[dict] | None = None,
) -> EvidenceBundle:
    return EvidenceBundle(
        run_id="test-run",
        target_id=target_id,
        entry_url=url,
        pages={url: page_from_html(html, url)},
        declarations=declarations or {},
        link_liveness=link_liveness or {},
        catalogue_services=catalogue_services or [],
    )


def make_target(
    *,
    target_id: str = "test-node",
    url: str = "https://node.example.org/",
    declarations: dict | None = None,
    exemptions: list[dict] | None = None,
) -> Target:
    return Target(
        id=target_id,
        name="Test node",
        landing_page=url,
        declarations=declarations or {},
        exemptions=exemptions or [],
    )


@pytest.fixture
def html_page():
    return page_from_html


@pytest.fixture
def html_bundle():
    return bundle_from_html


@pytest.fixture
def target_factory():
    return make_target
