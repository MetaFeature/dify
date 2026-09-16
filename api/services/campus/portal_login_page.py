"""Validate administrator-uploaded portal login pages.

The portal origin runs under a strict Content-Security-Policy: no inline or
remote scripts, no inline event handlers, same-origin forms only. That makes a
pure-HTML login page (a form plus optional inline CSS) the supported shape, and
it makes the contract statically checkable before a page is allowed to replace
the built-in one:

* the form posts ``subject`` and ``credential`` to ``/campus/auth/portal-form``
* it carries a submit control
* nothing in it can load code or submit anywhere else

Validation is a pure function over the file's text so it can be unit tested and
so the administrator gets a precise report instead of a silent refusal.
"""

import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Final

MAX_PORTAL_LOGIN_BYTES: Final = 512 * 1024
# The path the browser must post to: the console API prefix is what the campus
# nginx routes to the API container (a bare /campus/... path never reaches it).
LOGIN_ACTION: Final = "/console/api/campus/auth/portal-form"
# Where the form login sends the browser back to (the portal itself).
PORTAL_LOGIN_REDIRECT: Final = "/portal/"
SUBJECT_FIELD: Final = "subject"
CREDENTIAL_FIELD: Final = "credential"

# Attributes that carry script. CSP already blocks them in the browser; the scan
# rejects the file so the administrator learns the page will not work.
EVENT_ATTRIBUTE_PREFIX: Final = "on"
FORBIDDEN_TAGS: Final = frozenset({"script", "iframe", "object", "embed", "base", "frame", "frameset"})
BLOCKED_VALUE_PREFIXES: Final = ("javascript:", "data:text/html")


@dataclass(frozen=True)
class PortalLoginValidation:
    """Outcome of one validation pass: errors block activation."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    checks: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


class _LoginPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, str]] = []
        self.inputs: list[dict[str, str]] = []
        self.has_submit = False
        self.inline_script = False
        self.forbidden_tags: set[str] = set()
        self.event_attributes: set[str] = set()
        self.blocked_values: set[str] = set()
        self.external_references: set[str] = set()
        self.meta_refresh = False
        self._open_forms: list[dict[str, str]] = []

    # -- helpers ---------------------------------------------------------
    def _inspect_value(self, name: str, value: str) -> None:
        lowered = value.strip().lower()
        if lowered.startswith("javascript:"):
            self.blocked_values.add(name)
        if lowered.startswith(("http://", "https://", "//")):
            self.external_references.add(name)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}
        if tag in FORBIDDEN_TAGS:
            if tag == "script" and not attributes.get("src"):
                self.inline_script = True
            self.forbidden_tags.add(tag)
        for name, value in attributes.items():
            if name.startswith(EVENT_ATTRIBUTE_PREFIX) and len(name) > 2:
                self.event_attributes.add(name)
            if name in ("href", "src", "action", "formaction", "data", "poster", "srcset"):
                self._inspect_value(name, value)
        if tag == "meta" and attributes.get("http-equiv", "").lower() == "refresh":
            self.meta_refresh = True
        if tag == "form":
            form = {
                "action": attributes.get("action", ""),
                "method": attributes.get("method", "get").upper(),
            }
            self.forms.append(form)
            self._open_forms.append(form)
        if tag == "input":
            self.inputs.append(
                {
                    "name": attributes.get("name", ""),
                    "type": attributes.get("type", "text").lower(),
                }
            )
            if attributes.get("type", "").lower() == "submit":
                self.has_submit = True
        if tag == "button" and attributes.get("type", "submit").lower() == "submit":
            self.has_submit = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._open_forms:
            self._open_forms.pop()


def validate_portal_login_page(html: str) -> PortalLoginValidation:
    """Check an uploaded login page against the portal contract and CSP."""
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[str] = []

    encoded = html.encode("utf-8")
    if not html.strip():
        return PortalLoginValidation(errors=("文件为空。",), warnings=(), checks=())
    if len(encoded) > MAX_PORTAL_LOGIN_BYTES:
        errors.append(f"文件超过 {MAX_PORTAL_LOGIN_BYTES // 1024} KB 上限。")

    parser = _LoginPageParser()
    try:
        parser.feed(html)
    except Exception:
        errors.append("HTML 解析失败，请检查文件是否为合法 HTML。")
        return PortalLoginValidation(tuple(errors), (), ())

    # -- security scan ---------------------------------------------------
    if parser.forbidden_tags:
        tags = "、".join(sorted(parser.forbidden_tags))
        errors.append(f"页面包含被禁止的标签：{tags}。门户不允许脚本、内嵌框架与 base 标签。")
    if parser.event_attributes:
        names = "、".join(sorted(parser.event_attributes))
        errors.append(f"页面包含内联事件属性（{names}），CSP 会阻止它们执行。请改用表单提交。")
    if parser.blocked_values:
        names = "、".join(sorted(parser.blocked_values))
        errors.append(f"页面包含 javascript: 或 data:text/html 链接（{names}）。")
    if parser.external_references:
        names = "、".join(sorted(parser.external_references))
        errors.append(f"页面引用了外部地址（{names}）。登录页必须自包含或只用同源资源。")
    if parser.meta_refresh:
        warnings.append("页面包含 meta refresh 跳转，建议改用表单提交后的服务端跳转。")
    checks.append("安全扫描：无脚本标签、无内联事件、无外部引用")

    # -- contract --------------------------------------------------------
    method = "POST"
    action_forms = [form for form in parser.forms if form.get("action", "").rstrip("/") == LOGIN_ACTION.rstrip("/")]
    if not action_forms:
        errors.append(f"未找到提交到 {LOGIN_ACTION} 的表单（form action）。")
    else:
        checks.append(f"契约：表单提交到 {LOGIN_ACTION}")
        if any(form.get("method") != method for form in action_forms):
            errors.append(f"{LOGIN_ACTION} 的表单必须使用 method=\"post\"。")

    names = {item["name"] for item in parser.inputs}
    missing = [field for field in (SUBJECT_FIELD, CREDENTIAL_FIELD) if field not in names]
    if missing:
        errors.append(
            "缺少登录字段："
            + "、".join(f'name="{field}"' for field in missing)
            + "。学号框用 name=\"subject\"，访问码框用 name=\"credential\"。"
        )
    else:
        checks.append(f"契约：包含 {SUBJECT_FIELD} 与 {CREDENTIAL_FIELD} 输入框")

    if not parser.has_submit:
        errors.append("缺少提交按钮（button type=\"submit\" 或 input type=\"submit\"）。")
    else:
        checks.append("契约：包含提交按钮")

    if not parser.forms:
        warnings.append("页面没有任何表单，用户将无法登录。")

    return PortalLoginValidation(tuple(errors), tuple(warnings), tuple(checks))


def portal_login_digest(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()
