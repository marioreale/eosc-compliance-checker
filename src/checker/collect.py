"""Phase 1: collection.

The only component allowed to touch the network. Its single job is to produce a
complete, durable EvidenceBundle. It makes no compliance judgements at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import urllib.robotparser as robotparser
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from . import extract
from .models import EvidenceBundle, PageEvidence, utcnow
from .registry import Target

USER_AGENT = (
    "EOSC-ComplianceChecker/0.1 (+https://example.org/compliance-checker; "
    "automated conformance verification; contact: compliance@example.org)"
)

# Politeness: one request at a time per host, with a real delay between them.
# We are hitting other institutions' infrastructure on a schedule.
PER_HOST_DELAY_S = 1.0
NAV_TIMEOUT_MS = 30_000
SETTLE_MS = 800
BLOCKED_RESOURCES = {"image", "media", "font"}


class RobotsCache:
    def __init__(self) -> None:
        self._cache: dict[str, robotparser.RobotFileParser | None] = {}

    async def allowed(self, url: str, client: httpx.AsyncClient) -> bool:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._cache:
            rp: robotparser.RobotFileParser | None = robotparser.RobotFileParser()
            try:
                resp = await client.get(urljoin(root, "/robots.txt"), timeout=10)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp = None  # no robots.txt -> unrestricted
            except Exception:
                rp = None
            self._cache[root] = rp
        rp = self._cache[root]
        if rp is None:
            return True
        return rp.can_fetch(USER_AGENT, url)


def _wanted(url: str, target: Target, entry: str) -> bool:
    if not extract.same_host(url, entry):
        return False
    for pattern in target.crawl.exclude:
        if re.search(pattern, url):
            return False
    if target.crawl.include:
        return any(re.search(p, url) for p in target.crawl.include)
    return True


async def _capture_page(page, url: str, depth: int) -> PageEvidence:
    """Render one page and record everything we might later want."""
    started = time.perf_counter()
    console_errors: list[str] = []
    page.on(
        "console",
        lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
    )
    ev = PageEvidence(requested_url=url, depth=depth)
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        # Analytics and chat widgets mean many real sites never reach networkidle.
        # Give it a bounded chance, then take whatever has rendered.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=5_000)
        await page.wait_for_timeout(SETTLE_MS)

        html = await page.content()
        ev.final_url = page.url
        ev.status = resp.status if resp else 0
        ev.ok = bool(resp and resp.ok)
        ev.headers = dict(resp.headers) if resp else {}
        ev.html = html
        ev.title = extract.extract_title(html) or (await page.title())
        ev.lang_attr = extract.extract_lang(html)
        ev.hreflang = extract.extract_hreflang(html)
        ev.links = extract.extract_links(html, ev.final_url)
        ev.controls = extract.extract_controls(html, ev.final_url)
        ev.text = extract.extract_text(html)
        ev.content_sha256 = extract.sha256_text(html)
        ev.console_errors = console_errors[:20]
    except Exception as exc:  # collection failure is OUR error, never their FAIL
        ev.error = f"{type(exc).__name__}: {exc}"
        ev.ok = False
    ev.fetch_duration_ms = int((time.perf_counter() - started) * 1000)
    ev.fetched_at = utcnow()
    return ev


async def collect_target(
    target: Target,
    run_id: str,
    artefact_dir: Path,
    *,
    screenshots: bool = True,
) -> EvidenceBundle:
    from playwright.async_api import async_playwright

    bundle = EvidenceBundle(
        run_id=run_id,
        target_id=target.id,
        target_type=target.type,
        entry_url=target.landing_page,
        declarations=dict(target.declarations),
    )
    shots_dir = artefact_dir / target.id
    shots_dir.mkdir(parents=True, exist_ok=True)
    robots = RobotsCache()

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=15
    ) as client, async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
            ignore_https_errors=False,
        )

        async def _route(route):
            if route.request.resource_type in BLOCKED_RESOURCES and not screenshots:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", _route)
        page = await context.new_page()

        queue: list[tuple[str, int]] = [(target.landing_page, 0)]
        seen: set[str] = set()

        while queue and len(bundle.pages) < target.crawl.max_pages:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)

            if target.crawl.respect_robots and not await robots.allowed(url, client):
                bundle.notes.append(f"robots.txt disallows {url}; skipped")
                continue

            ev = await _capture_page(page, url, depth)

            if screenshots and ev.ok:
                shot = shots_dir / f"{extract.sha256_text(url)[:12]}.png"
                # A failed screenshot must not lose the page evidence we already have.
                with contextlib.suppress(Exception):
                    await page.screenshot(path=str(shot), full_page=False)
                    ev.screenshot = str(shot.relative_to(artefact_dir.parent))

            # Key by requested URL so rules can find the entry page deterministically.
            bundle.pages[url] = ev

            if depth < target.crawl.max_depth and ev.ok:
                for link in ev.links:
                    if link.href not in seen and _wanted(link.href, target, target.landing_page):
                        queue.append((link.href, depth + 1))

            await asyncio.sleep(PER_HOST_DELAY_S)

        await context.close()
        await browser.close()

        if target.crawl.check_link_liveness:
            bundle.link_liveness = await _probe_links(bundle, client)

        if target.catalogue_api:
            bundle.catalogue_services = await _fetch_catalogue(target.catalogue_api, client)

    return bundle


async def _probe_links(bundle: EvidenceBundle, client: httpx.AsyncClient) -> dict[str, int]:
    """HEAD-probe outbound links so rules can distinguish 'linked' from 'reachable'."""
    candidates: set[str] = set()
    for page in bundle.page_list():
        for link in page.links:
            if link.href.startswith("http"):
                candidates.add(link.href)
    results: dict[str, int] = {}
    sem = asyncio.Semaphore(4)

    async def probe(url: str) -> None:
        async with sem:
            try:
                resp = await client.head(url, timeout=10)
                if resp.status_code in (405, 501):  # HEAD not allowed
                    resp = await client.get(url, timeout=15)
                results[url] = resp.status_code
            except Exception:
                results[url] = 0
            await asyncio.sleep(0.2)

    await asyncio.gather(*(probe(u) for u in sorted(candidates)[:200]))
    return results


async def _fetch_catalogue(api_url: str, client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(api_url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    if isinstance(data, dict):
        for key in ("results", "services", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def new_run_id() -> str:
    return f"{date.today().isoformat()}-{int(time.time()) % 100000:05d}"
