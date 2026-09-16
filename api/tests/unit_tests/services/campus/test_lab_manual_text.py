from services.campus.lab_manual_text import (
    SUMMARY_LENGTH,
    decode_document,
    display_title,
    summarize_document,
    summarize_html_text,
)


def test_display_title_drops_only_a_trailing_extension() -> None:
    assert display_title("大模型实验指导书-1-进入平台.html") == "大模型实验指导书-1-进入平台"
    assert display_title("manual.HTML") == "manual"
    assert display_title("notes.htm") == "notes"
    # Nothing else is treated as an extension.
    assert display_title("第 1 章") == "第 1 章"
    assert display_title("v1.2 讲义") == "v1.2 讲义"
    assert display_title("  spaced.html  ") == "spaced"


def test_display_title_never_returns_nothing() -> None:
    # A title that is only the extension keeps its text rather than becoming blank.
    assert display_title(".html") == ".html"
    assert display_title("") == ""


def test_summary_keeps_the_prose_and_drops_the_markup() -> None:
    raw = (
        "<!doctype html><html><head><title>t</title><style>body{color:red}</style></head>"
        "<body><script>alert('x')</script><!-- note --><h1>第一章</h1>"
        "<p>本节介绍知识库的建立与检索。</p></body></html>"
    )

    summary = summarize_html_text(raw)

    assert "第一章" in summary
    assert "本节介绍知识库的建立与检索。" in summary
    for dropped in ("alert", "color:red", "note", "<", ">"):
        assert dropped not in summary


def test_summary_unescapes_entities_and_collapses_whitespace() -> None:
    assert summarize_html_text("<p>a &amp; b</p>\n\n   <p>c</p>") == "a & b c"


def test_summary_is_capped_and_marked_as_truncated() -> None:
    summary = summarize_html_text("<p>" + ("知" * 400) + "</p>")

    assert len(summary) == SUMMARY_LENGTH + 1
    assert summary.endswith("…")


def test_a_document_with_no_prose_has_no_summary() -> None:
    assert summarize_html_text("<style>a{}</style><script>b()</script>") == ""
    assert summarize_document(b"<html><body><script>x()</script></body></html>") is None


def test_document_text_is_decoded_from_the_declared_charset() -> None:
    html = '<html><head><meta charset="gb18030"></head><body><p>中文内容</p></body></html>'

    assert "中文内容" in decode_document(html.encode("gb18030"))


def test_document_text_falls_back_to_gb18030_for_chinese_exports() -> None:
    # No declared charset, and the bytes are not valid UTF-8.
    raw = "<html><body><p>中文内容</p></body></html>".encode("gb18030")

    assert "中文内容" in decode_document(raw)


def test_a_document_without_bytes_uses_the_legacy_body() -> None:
    assert summarize_document(None, fallback_html="<p>旧版正文</p>") == "旧版正文"
    assert summarize_document(None) is None


def test_summary_prefers_the_opening_paragraph_over_the_navigation() -> None:
    raw = (
        "<nav><a href='/a'>第1篇</a><a href='/b'>第2篇</a><a href='/c'>第3篇</a></nav>"
        "<h1>大模型实验指导书 · 第 1 篇</h1>"
        "<p>先搞清楚这门实验要做什么、账号从哪来，再学会登录预约，最后看懂工作流。</p>"
        "<p>后面的段落不该出现在简介里。</p>"
    )

    summary = summarize_html_text(raw)

    assert summary.startswith("先搞清楚这门实验要做什么")
    assert "第1篇" not in summary
    assert "后面的段落" not in summary


def test_summary_falls_back_to_the_document_text_without_a_real_paragraph() -> None:
    # Every paragraph is too short to be prose, so the flat text is all there is.
    assert summarize_html_text("<h1>第一章</h1><p>短标签</p><p>另一个</p>") == "第一章 短标签 另一个"
