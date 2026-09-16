import pytest

from services.campus.portal_login_page import (
    MAX_PORTAL_LOGIN_BYTES,
    validate_portal_login_page,
)

VALID_PAGE = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>登录</title>
<style>body { margin: 0; }</style>
</head>
<body>
  <form method="post" action="/console/api/campus/auth/portal-form">
    <input name="subject" placeholder="学号" required>
    <input name="credential" type="password" placeholder="访问码" required>
    <button type="submit">登录</button>
  </form>
</body>
</html>
"""


def test_accepts_a_self_contained_form_page():
    report = validate_portal_login_page(VALID_PAGE)

    assert report.ok, report.errors
    assert any("portal-form" in check for check in report.checks)
    assert any("subject" in check for check in report.checks)


def test_rejects_inline_script_and_external_assets():
    page = VALID_PAGE.replace("<style>", "<script src=\"https://evil.example/x.js\"></script><style>")
    report = validate_portal_login_page(page)

    assert not report.ok
    assert any("被禁止的标签" in error for error in report.errors)


def test_rejects_inline_event_handlers_and_javascript_urls():
    page = VALID_PAGE.replace("<body>", '<body onload="steal()">').replace(
        'placeholder="学号"', 'placeholder="学号" onclick="javascript:alert(1)"'
    )
    report = validate_portal_login_page(page)

    assert not report.ok
    assert any("内联事件属性" in error for error in report.errors)


def test_rejects_forms_that_submit_elsewhere():
    page = VALID_PAGE.replace('action="/console/api/campus/auth/portal-form"', 'action="https://evil.example/login"')
    report = validate_portal_login_page(page)

    assert not report.ok
    assert any("未找到提交到" in error or "外部地址" in error for error in report.errors)


def test_rejects_a_page_without_the_login_fields():
    page = VALID_PAGE.replace('<input name="credential" type="password" placeholder="访问码" required>', "")
    report = validate_portal_login_page(page)

    assert not report.ok
    assert any('name="credential"' in error for error in report.errors)


def test_rejects_a_page_without_a_submit_control():
    page = VALID_PAGE.replace('<button type="submit">登录</button>', "<p>请按回车</p>")
    report = validate_portal_login_page(page)

    assert not report.ok
    assert any("提交按钮" in error for error in report.errors)


def test_requires_post_on_the_login_form():
    page = VALID_PAGE.replace('method="post"', 'method="get"')
    report = validate_portal_login_page(page)

    assert not report.ok
    assert any("method=\"post\"" in error for error in report.errors)


@pytest.mark.parametrize("page", ["", "   \n"])
def test_rejects_empty_uploads(page: str):
    assert not validate_portal_login_page(page).ok


def test_rejects_files_over_the_size_limit():
    oversized = VALID_PAGE + "<!--" + ("x" * MAX_PORTAL_LOGIN_BYTES) + "-->"
    report = validate_portal_login_page(oversized)

    assert not report.ok
    assert any("上限" in error for error in report.errors)


def test_warns_about_meta_refresh_without_blocking():
    page = VALID_PAGE.replace("<title>登录</title>", '<title>登录</title><meta http-equiv="refresh" content="5">')
    report = validate_portal_login_page(page)

    assert report.ok
    assert any("meta refresh" in warning for warning in report.warnings)
