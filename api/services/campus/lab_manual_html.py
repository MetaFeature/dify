"""Sanitize administrator-uploaded lab manual HTML.

Administrators upload chapters as HTML, typically exported from a word
processor. Two things make that unsafe to store as-is. The obvious one is
injection: scripts, frames, and event handlers reach every student who opens the
chapter. The quieter one is that the portal's content security policy admits
neither inline styles nor inline scripts nor remote images, so an unsanitized
upload renders wrong with nothing in any log to explain it.

Sanitizing runs in two passes. The first drops whole subtrees whose text must
not survive -- a stylesheet's rules and a script's source would otherwise land
in the page as visible text. The second is the authoritative security pass and
allows none of those tags, so a mistake in the first pass costs readability, not
safety. Both run once, at upload; serving a chapter is a plain string read.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass

import bleach
from bleach import html5lib_shim
from bleach.sanitizer import Cleaner

from services.campus.errors import CampusValidationError

# Structure and semantics an experiment manual needs, and nothing that carries
# behaviour, layout, or a second document.
ALLOWED_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "br",
        "hr",
        "div",
        "span",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "s",
        "sub",
        "sup",
        "mark",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "blockquote",
        "pre",
        "code",
        "kbd",
        "samp",
        "var",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "caption",
        "colgroup",
        "col",
        "a",
        "img",
        "figure",
        "figcaption",
    }
)

# Tags whose content must go with them.
DROPPED_TAGS = frozenset(
    {
        "script",
        "style",
        "iframe",
        "object",
        "embed",
        "applet",
        "form",
        "input",
        "button",
        "select",
        "option",
        "textarea",
        "noscript",
        "template",
        "link",
        "meta",
        "base",
        "svg",
        "math",
    }
)

ALLOWED_ATTRIBUTES: Mapping[str, list[str]] = {
    "a": ["href", "title"],
    "img": ["src", "alt", "width", "height"],
    "th": ["colspan", "rowspan", "scope"],
    "td": ["colspan", "rowspan"],
    "col": ["span"],
    "colgroup": ["span"],
    "ol": ["start", "type"],
}

ALLOWED_PROTOCOLS = frozenset({"http", "https", "mailto"})

# Images must be same-origin because the portal sets img-src 'self'. Anything
# the platform itself serves is addressed by a root-relative path.
_SAME_ORIGIN_PREFIX = "/"


@dataclass(frozen=True)
class SanitizedHtml:
    """Chapter HTML that is safe to store, and what was taken out of it."""

    html: str
    removed: Mapping[str, int]

    def summary(self) -> str:
        """Describe the removals for an administrator, most frequent first."""
        if not self.removed:
            return ""
        parts = sorted(self.removed.items(), key=lambda item: (-item[1], item[0]))
        return "; ".join(f"{name} x{count}" for name, count in parts)


# bleach ships no annotation for its vendored html5lib Filter, which is the
# documented way to extend the token stream.
class _DropSubtrees(html5lib_shim.Filter):  # pyrefly: ignore[missing-attribute]
    """Remove dropped elements with their content, and remote images.

    bleach judges a URL by protocol alone, so it keeps an https image. The
    portal serves img-src 'self', which means a remote image is blocked by the
    browser with nothing in any log, so it is dropped here instead.
    """

    def __init__(self, source, removed: Counter[str]) -> None:
        super().__init__(source)
        self._removed = removed

    def __iter__(self):
        depth = 0
        dropping: list[str] = []
        for token in html5lib_shim.Filter.__iter__(self):  # pyrefly: ignore[missing-attribute]
            token_type = token["type"]
            name = token.get("name")
            if token_type in ("StartTag", "EmptyTag") and name in DROPPED_TAGS:
                if token_type == "StartTag":
                    depth += 1
                    dropping.append(name)
                if depth <= 1:
                    self._removed[name] += 1
                continue
            if token_type == "EndTag" and name in DROPPED_TAGS and dropping:
                if dropping[-1] == name:
                    dropping.pop()
                    depth = max(0, depth - 1)
                continue
            if depth:
                continue
            if token_type in ("StartTag", "EmptyTag") and name == "img" and not self._is_same_origin(token):
                continue
            yield token

    @staticmethod
    def _is_same_origin(token) -> bool:
        """Report whether an img token points at something this platform serves."""
        for key, value in (token.get("data") or {}).items():
            attribute = key[1] if isinstance(key, tuple) else key
            if attribute == "src":
                return str(value).strip().startswith(_SAME_ORIGIN_PREFIX)
        return False


def sanitize_lab_manual_html(raw: str) -> SanitizedHtml:
    """Return storable chapter HTML, or reject an upload with nothing to read."""
    removed: Counter[str] = Counter()
    _count_attribute_removals(raw, removed)

    def build_filter(source):
        return _DropSubtrees(source, removed)

    structural = Cleaner(
        tags=ALLOWED_TAGS | DROPPED_TAGS,
        attributes={key: list(value) for key, value in ALLOWED_ATTRIBUTES.items()},
        protocols=sorted(ALLOWED_PROTOCOLS),
        strip=True,
        strip_comments=True,
        filters=[build_filter],
    ).clean(raw)

    html = bleach.clean(
        structural,
        tags=set(ALLOWED_TAGS),
        attributes={key: list(value) for key, value in ALLOWED_ATTRIBUTES.items()},
        protocols=sorted(ALLOWED_PROTOCOLS),
        strip=True,
        strip_comments=True,
    ).strip()

    if not _has_readable_text(html):
        raise CampusValidationError("Chapter upload has no readable content after sanitizing")
    return SanitizedHtml(html=html, removed=dict(removed))


def _count_attribute_removals(raw: str, removed: Counter[str]) -> None:
    """Count attributes the security pass will drop, so the report can name them.

    bleach reports nothing about what it removed, and an administrator whose
    formatting silently vanished needs to be told which kind of thing went.
    """
    for name, count in (
        ("style attribute", _count_attributes(raw, ("style",))),
        ("event handler", _count_event_handlers(raw)),
        ("unsafe link", _count_unsafe_urls(raw, "href")),
        ("remote image", _count_unsafe_urls(raw, "src")),
    ):
        if count:
            removed[name] += count


def _attribute_values(raw: str, attribute: str) -> list[str]:
    """Collect the values of one attribute across the document, quotes only."""
    values: list[str] = []
    lowered = raw.lower()
    needle = attribute.lower() + "="
    index = 0
    while True:
        found = lowered.find(needle, index)
        if found == -1:
            return values
        if found > 0 and (lowered[found - 1].isalnum() or lowered[found - 1] in "-_:"):
            index = found + len(needle)
            continue
        cursor = found + len(needle)
        if cursor >= len(raw) or raw[cursor] not in "\"'":
            index = cursor
            continue
        quote = raw[cursor]
        end = raw.find(quote, cursor + 1)
        if end == -1:
            return values
        values.append(raw[cursor + 1 : end])
        index = end + 1


def _count_attributes(raw: str, attributes: tuple[str, ...]) -> int:
    return sum(len(_attribute_values(raw, attribute)) for attribute in attributes)


def _count_event_handlers(raw: str) -> int:
    """Count on*= attributes, which are always removed."""
    count = 0
    lowered = raw.lower()
    index = 0
    while True:
        found = lowered.find(" on", index)
        if found == -1:
            return count
        cursor = found + 3
        while cursor < len(lowered) and lowered[cursor].isalpha():
            cursor += 1
        if cursor > found + 3 and cursor < len(lowered) and lowered[cursor] == "=":
            count += 1
        index = cursor


def _count_unsafe_urls(raw: str, attribute: str) -> int:
    count = 0
    for value in _attribute_values(raw, attribute):
        candidate = value.strip()
        if attribute == "src":
            if not candidate.startswith(_SAME_ORIGIN_PREFIX):
                count += 1
            continue
        scheme, separator, _ = candidate.partition(":")
        if separator and scheme.strip().lower() not in ALLOWED_PROTOCOLS:
            count += 1
    return count


def _has_readable_text(html: str) -> bool:
    """Report whether anything a student could read survived.

    A chapter can legitimately be nothing but a diagram, so a surviving image
    counts as content even with no text beside it.
    """
    if "<img" in html:
        return True
    return bool(bleach.clean(html, tags=set(), attributes={}, strip=True).strip())
