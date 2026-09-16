"""Display helpers for uploaded learning documents.

Storage keeps what the administrator uploaded — the chapter title *is* the file
name, and the bytes are preserved verbatim. These helpers only shape that
material for students: a title without its extension, and a short teaser taken
from the document body for the experiment chooser.
"""

from __future__ import annotations

import html
import re
from typing import Final

#: How much plain text the chooser gets. It renders two lines, so this is a
#: little more than fits and the CSS clamps the overflow.
SUMMARY_LENGTH: Final = 120

#: A trailing .html/.htm is a file detail, not part of a title.
_EXTENSION: Final = re.compile(r"\.html?$", re.IGNORECASE)
_SCRIPT_OR_STYLE: Final = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT: Final = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG: Final = re.compile(r"<[^>]*>")
_WHITESPACE: Final = re.compile(r"\s+")
#: Documents open with navigation and headings; the first real paragraph is
#: the introduction, so it beats the flat text. The length guard skips labels.
_PARAGRAPH: Final = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_MIN_PARAGRAPH: Final = 20
#: Enough of the head to find a declared charset.
_SNIFF_BYTES: Final = 4096
_CHARSET: Final = re.compile(rb"""charset=["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE)


def display_title(title: str) -> str:
    """The title as a student should read it, without a trailing file extension."""
    trimmed = (title or "").strip()
    stripped = _EXTENSION.sub("", trimmed)
    # A title that is nothing but ".html" keeps its text rather than becoming empty.
    return stripped or trimmed


def decode_document(data: bytes | None, *, fallback_html: str | None = None) -> str:
    """Best-effort text for a stored document.

    Uploads are usually UTF-8; a file exported by a Chinese Windows editor is
    often GB18030. The declared charset wins, then UTF-8, then GB18030, and
    anything left over is decoded with replacement characters rather than
    failing — a teaser is not worth rejecting a document over.
    """
    if not data:
        return fallback_html or ""
    declared = _CHARSET.search(data[:_SNIFF_BYTES])
    candidates = [declared.group(1).decode("ascii", errors="ignore")] if declared else []
    candidates += ["utf-8", "gb18030"]
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def summarize_document(data: bytes | None, *, fallback_html: str | None = None) -> str | None:
    """A short plain-text teaser from a document's body, or None when there is none."""
    text = summarize_html_text(decode_document(data, fallback_html=fallback_html))
    return text or None


def summarize_html_text(raw: str) -> str:
    """A teaser for an HTML document: its opening paragraph, or its opening words."""
    body = _visible_markup(raw)
    return _cap(_opening_paragraph(body) or _plain_text(body))


def _visible_markup(raw: str) -> str:
    """The document without the parts a reader never sees."""
    return _COMMENT.sub(" ", _SCRIPT_OR_STYLE.sub(" ", raw))


def _opening_paragraph(html_body: str) -> str:
    """The first paragraph long enough to be prose rather than a label."""
    for match in _PARAGRAPH.finditer(html_body):
        text = _plain_text(match.group(1))
        if len(text) >= _MIN_PARAGRAPH:
            return text
    return ""


def _plain_text(markup: str) -> str:
    return _WHITESPACE.sub(" ", html.unescape(_TAG.sub(" ", markup))).strip()


def _cap(text: str) -> str:
    if len(text) <= SUMMARY_LENGTH:
        return text
    return f"{text[:SUMMARY_LENGTH].rstrip()}…"
