"""Extraction tests. If these are wrong, every rule above them is wrong."""

from __future__ import annotations

from conftest import page_from_html

from checker import extract


def test_div_role_button_is_a_control():
    """Real sites build buttons out of divs. The extractor must see them."""
    html = '<html><body><div role="button" tabindex="0">Log in</div></body></html>'
    controls = extract.extract_controls(html, "https://x.example/")
    assert len(controls) == 1
    assert controls[0].tag == "div"
    assert controls[0].role == "button"
    assert "Log in" in controls[0].all_text


def test_svg_aria_label_is_readable_text():
    """An icon-only control whose label lives in an SVG must still be matchable."""
    html = (
        '<html><body><div role="button">'
        '<svg aria-label="Sign in with EOSC AAI"></svg>'
        "</div></body></html>"
    )
    controls = extract.extract_controls(html, "https://x.example/")
    assert "Sign in with EOSC AAI" in controls[0].all_text


def test_icon_labels_are_deduplicated():
    """svg + use commonly repeat the same aria-label; it must appear once."""
    html = (
        '<html><body><div role="button">'
        '<svg aria-label="Sign in with EOSC AAI">'
        '<use href="#k" aria-label="Sign in with EOSC AAI"/></svg>'
        "</div></body></html>"
    )
    control = extract.extract_controls(html, "https://x.example/")[0]
    assert control.alt_texts == ["Sign in with EOSC AAI"]
    assert control.all_text.count("EOSC AAI") == 1


def test_hidden_controls_are_marked_invisible():
    html = (
        '<html><body>'
        '<a href="/a" style="display:none">Log in (legacy)</a>'
        '<a href="/b" hidden>Log in (old)</a>'
        '<a href="/c" aria-hidden="true">Log in (aria)</a>'
        '<a href="/d">Log in</a>'
        "</body></html>"
    )
    controls = extract.extract_controls(html, "https://x.example/")
    visible = [c for c in controls if c.visible]
    assert len(controls) == 4
    assert len(visible) == 1
    assert visible[0].href.endswith("/d")


def test_links_inside_a_hidden_container_are_invisible():
    """The cookie-banner trap: the link is fine, its ancestor is not."""
    html = (
        '<html><body>'
        '<div id="cookie-banner" style="display:none">'
        '<a href="/cookies.html">Terms of use</a></div>'
        '<footer><a href="/aup.html">Acceptable Use Policy</a></footer>'
        "</body></html>"
    )
    links = {link.text: link.visible for link in extract.extract_links(html, "https://x.example/")}
    assert links["Terms of use"] is False
    assert links["Acceptable Use Policy"] is True


def test_controls_inside_a_hidden_container_are_invisible():
    html = (
        '<html><body><div hidden><button>Login with EOSC AAI</button></div>'
        "<button>Search</button></body></html>"
    )
    controls = {c.text: c.visible for c in extract.extract_controls(html, "https://x.example/")}
    assert controls["Login with EOSC AAI"] is False
    assert controls["Search"] is True


def test_links_are_absolutised_and_classified():
    html = (
        '<html><body>'
        '<a href="/local">local</a>'
        '<a href="https://other.example/x">remote</a>'
        '<a href="#frag">frag</a>'
        '<a href="javascript:void(0)">js</a>'
        "</body></html>"
    )
    links = extract.extract_links(html, "https://node.example.org/page")
    hrefs = [link.href for link in links]
    assert "https://node.example.org/local" in hrefs
    assert "https://other.example/x" in hrefs
    assert len(links) == 2, "fragment and javascript hrefs must be dropped"
    assert next(link for link in links if "local" in link.href).is_internal
    assert not next(link for link in links if "other" in link.href).is_internal


def test_lang_and_hreflang():
    html = (
        '<html lang="nl-NL"><head>'
        '<link rel="alternate" hreflang="en" href="/en">'
        "</head><body>hi</body></html>"
    )
    assert extract.extract_lang(html) == "nl-NL"
    assert extract.extract_hreflang(html) == ["en"]


def test_text_extraction_falls_back_on_short_pages():
    """trafilatura returns nothing for tiny pages; we must still get text."""
    html = "<html><body><p>Short page.</p><script>var x=1;</script></body></html>"
    text = extract.extract_text(html)
    assert "Short page." in text
    assert "var x" not in text


def test_page_from_html_populates_everything(html_page=page_from_html):
    html = '<html lang="en"><head><title>T</title></head><body><a href="/x">x</a></body></html>'
    page = html_page(html)
    assert page.ok and page.title == "T" and page.lang_attr == "en"
    assert page.content_sha256 and page.links
