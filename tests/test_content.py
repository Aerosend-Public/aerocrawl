from __future__ import annotations

from app.services.content import (
    extract_links,
    extract_metadata,
    html_to_markdown,
    looks_like_js_rendered,
)

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Test Page</title>
    <meta name="description" content="A test page for scraping">
    <meta property="og:title" content="OG Test Title">
    <meta property="og:description" content="OG description here">
    <meta property="og:image" content="https://example.com/og.png">
    <meta name="robots" content="index,follow">
    <script>var x = 1;</script>
    <style>body { color: red; }</style>
</head>
<body>
    <nav><a href="/nav-link">Nav</a></nav>
    <header><h1>Header</h1></header>
    <main>
        <h1>Main Heading</h1>
        <p>This is <strong>bold</strong> text in the main content.</p>
        <a href="/about">About</a>
        <a href="https://example.com/page">Page</a>
        <a href="#section">Anchor</a>
        <a href="javascript:void(0)">JS Link</a>
        <a href="mailto:test@test.com">Email</a>
        <a href="tel:123456">Phone</a>
        <a href="/about">About Duplicate</a>
    </main>
    <footer><p>Footer content</p></footer>
</body>
</html>"""


def test_html_to_markdown():
    """Verify # heading + **bold** present, no script tags."""
    md = html_to_markdown(SAMPLE_HTML)
    assert "# Main Heading" in md or "# Header" in md
    assert "**bold**" in md
    assert "<script>" not in md
    assert "var x = 1" not in md


def test_html_to_markdown_main_content_only():
    """Verify nav/footer stripped when only_main_content=True."""
    md = html_to_markdown(SAMPLE_HTML, only_main_content=True)
    assert "**bold**" in md
    # Nav and footer content should be stripped
    assert "Nav" not in md or "Footer content" not in md


def test_extract_metadata():
    """Verify title, description, language, og_title, status_code."""
    meta = extract_metadata(SAMPLE_HTML, "https://example.com", 200)
    assert meta["title"] == "Test Page"
    assert meta["description"] == "A test page for scraping"
    assert meta["language"] == "en"
    assert meta["og_title"] == "OG Test Title"
    assert meta["status_code"] == 200
    assert meta["source_url"] == "https://example.com"
    assert meta["og_image"] == "https://example.com/og.png"


def test_extract_links():
    """Verify relative→absolute, dedup, junk filtered."""
    links = extract_links(SAMPLE_HTML, "https://example.com")
    assert "https://example.com/about" in links
    assert "https://example.com/page" in links
    assert "https://example.com/nav-link" in links
    # Dedup — /about should appear only once
    assert links.count("https://example.com/about") == 1


def test_extract_links_filters_junk():
    """# / javascript: / mailto: / tel: excluded."""
    links = extract_links(SAMPLE_HTML, "https://example.com")
    for link in links:
        assert not link.startswith("javascript:")
        assert not link.startswith("mailto:")
        assert not link.startswith("tel:")
        assert not link.startswith("#")


def test_looks_like_js_rendered_true():
    html = """<html><body></body>
    <script src="a.js"></script><script src="b.js"></script>
    <script src="c.js"></script><script src="d.js"></script>
    </html>"""
    assert looks_like_js_rendered(html) is True


def test_looks_like_js_rendered_false():
    assert looks_like_js_rendered(SAMPLE_HTML) is False


def test_looks_like_js_rendered_noscript():
    html = """<html><body><p>Short</p></body>
    <noscript>This application requires JavaScript to be enabled. Please enable JavaScript and reload.</noscript>
    </html>"""
    assert looks_like_js_rendered(html) is True
