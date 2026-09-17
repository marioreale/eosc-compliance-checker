"""DOM -> structured signals.

Pure functions over an HTML string so they can be unit-tested without a browser.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from .models import Link, UiElement

CONTROL_SELECTOR = "a, button, input[type=submit], input[type=button], [role=button]"

# Elements that are present in the DOM but not shown to a user. Crude but
# catches the common cases without needing a live page.
_HIDDEN_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _self_hidden(node) -> bool:
    # Valueless boolean attributes map to None in selectolax, so membership is
    # the only correct test here -- `.get("hidden")` returns None either way.
    if "hidden" in node.attributes:
        return True
    if node.attributes.get("aria-hidden") == "true":
        return True
    style = node.attributes.get("style") or ""
    return bool(_HIDDEN_RE.search(style))


def _is_visible(node) -> bool:
    """Visibility including inherited hiding from ancestors.

    Checking only the element itself is not enough and it produces real false
    positives: the classic case is a collapsed cookie banner
    (`<div style="display:none">`) containing a perfectly ordinary
    "Terms of use" link. The link element carries no hidden markup of its own,
    but no user can see or click it, so it must not satisfy a requirement.

    This is a static approximation. It catches inline styles, `hidden` and
    `aria-hidden`, which is what hand-written and CMS markup actually uses. It
    does not resolve stylesheet rules or computed geometry; if a rule ever needs
    that, ask Playwright for `is_visible()` at collection time and record the
    answer in the evidence rather than guessing here.
    """
    current = node
    depth = 0
    while current is not None and depth < 25:
        if _self_hidden(current):
            return False
        current = current.parent
        depth += 1
    return True


def _node_text(node) -> str:
    return re.sub(r"\s+", " ", node.text(separator=" ", strip=True) or "").strip()


def _alt_texts(node) -> list[str]:
    """Readable labels carried by images and icons inside a control.

    Deduplicated: an icon commonly repeats the same aria-label on both the
    <svg> and its <use> child, which would otherwise show up twice in every
    evidence record and finding message.
    """
    out: list[str] = []
    for child in node.css("img[alt], svg[aria-label], use[aria-label]"):
        val = child.attributes.get("alt") or child.attributes.get("aria-label")
        val = (val or "").strip()
        if val and val not in out:
            out.append(val)
    return out


def same_host(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower() == urlparse(b).netloc.lower()
    except ValueError:
        return False


def extract_title(html: str) -> str | None:
    tree = HTMLParser(html)
    node = tree.css_first("title")
    return _node_text(node) if node else None


def extract_lang(html: str) -> str | None:
    tree = HTMLParser(html)
    node = tree.css_first("html")
    if not node:
        return None
    val = node.attributes.get("lang")
    return val.strip() if val else None


def extract_hreflang(html: str) -> list[str]:
    tree = HTMLParser(html)
    out = []
    for node in tree.css("link[rel=alternate][hreflang]"):
        val = node.attributes.get("hreflang")
        if val:
            out.append(val.strip())
    return out


def extract_links(html: str, base_url: str) -> list[Link]:
    tree = HTMLParser(html)
    out: list[Link] = []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        out.append(
            Link(
                href=absolute,
                text=_node_text(node),
                aria_label=node.attributes.get("aria-label"),
                rel=node.attributes.get("rel"),
                is_internal=same_host(absolute, base_url),
                visible=_is_visible(node),
            )
        )
    return out


def extract_controls(html: str, base_url: str) -> list[UiElement]:
    """Candidate interactive controls, with every readable string attached.

    A login affordance may be an <a>, a <button>, a <div role=button>, or an
    icon whose only label is an SVG aria-label. All of those must be visible to
    a rule, or the rule will produce false negatives on real sites.
    """
    tree = HTMLParser(html)
    out: list[UiElement] = []
    for node in tree.css(CONTROL_SELECTOR):
        href = node.attributes.get("href")
        out.append(
            UiElement(
                tag=node.tag,
                text=_node_text(node) or (node.attributes.get("value") or "").strip(),
                aria_label=node.attributes.get("aria-label"),
                role=node.attributes.get("role"),
                href=urljoin(base_url, href) if href else None,
                title=node.attributes.get("title"),
                alt_texts=_alt_texts(node),
                visible=_is_visible(node),
            )
        )
    return out


def fallback_text(html: str) -> str:
    """Plain text when trafilatura declines to extract (short or odd pages)."""
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript"):
        for node in tree.css(tag):
            node.decompose()
    body = tree.css_first("body") or tree.root
    return re.sub(r"\s+", " ", body.text(separator=" ", strip=True) or "").strip()


def extract_text(html: str) -> str:
    try:
        import trafilatura

        got = trafilatura.extract(html, include_comments=False, include_tables=True)
        if got and len(got.strip()) > 40:
            return got.strip()
    except Exception:
        pass
    return fallback_text(html)


def snippet_around(html: str, needle: str, width: int = 400) -> str | None:
    """Bounded HTML excerpt for the evidence record."""
    if not needle:
        return None
    idx = html.lower().find(needle.lower()[:60])
    if idx < 0:
        return None
    start = max(0, idx - width // 2)
    return html[start : idx + width // 2]
