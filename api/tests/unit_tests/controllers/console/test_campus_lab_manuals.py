"""Route-level contract for the lab manual endpoints.

The service layer is tested directly; what these check is the wiring nobody
notices until it is wrong: that the routes exist under the paths the portal and
the administration page call, that the student-facing ones sit on the portal
session rather than the console session, and that a student route cannot serve a
draft.
"""

import pytest
from flask import Flask

from controllers.console import console_ns


def _routes() -> set[str]:
    return {
        rule
        for route in console_ns.resources
        for rule in route.urls
        if "lab-manual" in rule or "experiment-track" in rule
    }


@pytest.mark.parametrize(
    "route",
    [
        "/campus/experiment-tracks",
        "/campus/lab-manuals/<string:track>",
        "/campus/lab-manuals/documents/<string:chapter_id>/view",
        "/campus/lab-manuals/documents/<string:chapter_id>/content",
        "/campus/admin/lab-manuals/<string:track>/chapters",
        "/campus/admin/lab-manuals/chapters/<string:chapter_id>",
        "/campus/admin/lab-manuals/chapters/<string:chapter_id>/status",
        "/campus/admin/lab-manuals/chapters/<string:chapter_id>/position",
    ],
)
def test_route_is_registered(route: str) -> None:
    assert route in _routes(), f"missing route {route}; registered: {sorted(_routes())}"


def test_administration_routes_are_all_under_the_admin_prefix() -> None:
    # nginx and the portal both key off this prefix, and an admin route that
    # escaped it would be reachable from the campus listener.
    from controllers.console import campus_admin

    admin_rules = {
        rule
        for route in console_ns.resources
        for rule in route.urls
        if getattr(route.resource, "__module__", "") == campus_admin.__name__
    }

    assert admin_rules
    assert all(rule.startswith("/campus/admin/") for rule in admin_rules), sorted(admin_rules)


def test_student_manual_routes_require_a_portal_session_not_a_console_login() -> None:
    # A student has no Dify console session of their own on this path, so
    # login_required here would lock every student out.
    import inspect

    from controllers.console import campus

    source = inspect.getsource(campus.CampusLabManualApi)
    assert "portal_student()" in source
    assert "@login_required" not in source

    tracks = inspect.getsource(campus.CampusExperimentTrackListApi)
    assert "portal_student()" in tracks
    assert "@login_required" not in tracks


def test_student_manual_route_serves_published_chapters_only() -> None:
    import inspect

    from controllers.console import campus

    source = inspect.getsource(campus.CampusLabManualApi)
    assert "published_chapters" in source
    assert "all_chapters" not in source


def test_administration_manual_routes_require_an_administrator() -> None:
    import inspect

    from controllers.console import campus_admin

    for resource in (
        campus_admin.CampusAdminLabManualChapterListApi,
        campus_admin.CampusAdminLabManualChapterApi,
        campus_admin.CampusAdminLabManualChapterStatusApi,
        campus_admin.CampusAdminLabManualChapterPositionApi,
    ):
        source = inspect.getsource(resource)
        assert "require_admin(current_user)" in source, resource.__name__
        assert "@login_required" in source, resource.__name__


def test_authoring_routes_attribute_the_acting_administrator() -> None:
    # ADR-0009: management actions are attributable. Checking the class as a
    # whole would pass while one method quietly forgot the actor, so every
    # write call is checked on its own.
    import inspect
    import re

    from controllers.console import campus_admin

    write_calls = ("create_chapter", "update_chapter", "delete_chapter", "set_chapter_status", "move_chapter")
    checked = 0
    for resource in (
        campus_admin.CampusAdminLabManualChapterListApi,
        campus_admin.CampusAdminLabManualChapterApi,
        campus_admin.CampusAdminLabManualChapterStatusApi,
        campus_admin.CampusAdminLabManualChapterPositionApi,
    ):
        source = inspect.getsource(resource)
        for call in write_calls:
            # Each call plus its arguments, up to the closing parenthesis.
            for match in re.finditer(rf"\.{call}\(", source):
                depth, index = 1, match.end()
                while index < len(source) and depth:
                    depth += {"(": 1, ")": -1}.get(source[index], 0)
                    index += 1
                arguments = source[match.end() : index]
                assert "actor_account_id=current_user.id" in arguments, (
                    f"{resource.__name__}.{call} does not attribute the acting administrator"
                )
                checked += 1
    assert checked >= 5, f"expected every authoring call to be checked, saw {checked}"


def test_administrators_replace_documents_with_multipart_files() -> None:
    import inspect

    from controllers.console import campus_admin

    assert "/campus/admin/lab-manuals/chapters/<string:chapter_id>" in _routes()
    source = inspect.getsource(campus_admin.CampusAdminLabManualChapterApi)
    assert 'request.files.get("file")' in source
    assert "upload.read(MAX_DOCUMENT_BYTES + 1)" in source
    assert "body_html" not in source


def test_manual_images_are_uploaded_by_administrators_and_served_to_students() -> None:
    assert "/campus/admin/lab-manuals/<string:track>/images" in _routes()
    assert "/campus/lab-manuals/images/<string:image_id>" in _routes()


def test_served_images_cannot_act_as_documents() -> None:
    # An image route that echoes caller-supplied bytes is a stored-content
    # channel; the headers are what stop a browser from treating one as a page.
    import inspect

    from controllers.console import campus

    source = inspect.getsource(campus.CampusLabManualImageApi)

    assert "X-Content-Type-Options" in source
    assert "nosniff" in source
    assert "Content-Security-Policy" in source
    assert "sandbox" in source


def test_original_document_bytes_are_returned_without_functional_response_restrictions() -> None:
    import inspect

    from controllers.console import campus

    source = inspect.getsource(campus.CampusLearningDocumentContentApi)

    assert "original.data" in source
    assert "Content-Security-Policy" not in source
    assert "X-Content-Type-Options" not in source
    assert "LabManualChapterStatus.PUBLISHED" in source


def test_document_links_open_the_wrapped_view_on_the_dedicated_manual_origin() -> None:
    from controllers.console import campus

    app = Flask(__name__)
    with app.test_request_context("http://10.20.10.193/console/api/campus/lab-manuals/agent"):
        view_url = campus._manual_view_url("chapter-1")

    assert view_url == ("http://10.20.10.193:18083/console/api/campus/lab-manuals/documents/chapter-1/view")


def test_manual_view_wraps_the_untouched_document_in_a_reloadable_frame() -> None:
    from controllers.console import campus

    page = campus._manual_view_html(
        title="交互式 <手册>",
        summary="本手册教你用 <WebGPU> 跑通第一个模型。",
        content_url="/console/api/campus/lab-manuals/documents/chapter-1/content",
        portal_url="http://10.20.10.193/portal/",
    )

    assert '<iframe name="manual-content"' in page
    assert 'src="/console/api/campus/lab-manuals/documents/chapter-1/content"' in page
    assert 'target="manual-content"' in page
    assert "sandbox=" not in page
    assert "交互式 &lt;手册&gt;" in page
    assert "本手册教你用 &lt;WebGPU&gt; 跑通第一个模型。" in page
    assert "提示" not in page


def test_the_subtitle_is_a_teaser_rather_than_the_title_again() -> None:
    import inspect

    from controllers.console import campus

    # The header used to repeat the chapter title as the original file name
    # ("…指导书" over "…指导书.html"), which told a student nothing. It now
    # carries the document's own opening paragraph.
    source = inspect.getsource(campus._manual_view_html)
    assert "summary" in inspect.signature(campus._manual_view_html).parameters
    assert "filename" not in inspect.signature(campus._manual_view_html).parameters
    assert "safe_filename" not in source

    page = campus._manual_view_html(
        title="深度学习实验指导书",
        summary="",
        content_url="/content",
        portal_url="/portal/",
    )
    # A chapter saved before summaries existed shows no subtitle at all rather
    # than an empty line under the title.
    assert '<div class="title"><h1>深度学习实验指导书</h1></div>' in page
    assert "<p></p>" not in page


def test_manual_view_shell_cannot_grow_wider_than_the_window() -> None:
    from controllers.console import campus

    page = campus._manual_view_html(
        title="WorkBuddy智能体进阶实验指导书-3-任务三、四、五-出文档、汇报、PPT",
        summary="这一篇把调研结果做成文档、汇报与 PPT。",
        content_url="/content",
        portal_url="/portal/",
    )

    # The grid declared rows only, so the implicit column was sized by the
    # nowrap header; on a narrow window that pushed the page -- and the frame
    # with it -- wider than the window itself.
    assert "grid-template-columns: minmax(0, 1fr)" in page
    assert "iframe { display: block; width: 100%; height: 100%; max-width: 100%;" in page
    # A phone's address bar sits outside vh, so the frame must not be sized by vh alone.
    assert "height: 100dvh" in page
    # On a narrow screen the title wraps and the buttons may wrap with it.
    assert "white-space: normal" in page
    assert "flex-wrap: wrap" in page


def test_the_framed_document_is_fitted_at_read_time_only_when_it_overflows() -> None:
    from controllers.console import campus

    page = campus._manual_view_html(
        title="深度学习实验指导书",
        summary="",
        content_url="/content",
        portal_url="/portal/",
    )

    # Uploaded bytes are never rewritten (ADR-0026), so the baseline has to come
    # from the viewer at read time.
    assert 'iframe[name="manual-content"]' in page
    assert "contentDocument" in page
    assert "catch (error)" in page
    # ...and it must stay conditional: a document that already fits keeps the
    # layout its author wrote.
    overflow_check = page.index("scrollWidth <= root.clientWidth")
    add_style = page.index("doc.head.appendChild(style)")
    assert overflow_check < add_style
    assert page.index("No overflow") < add_style
    # The baseline itself: images and code blocks may shrink, nothing is hidden.
    assert "max-width: 100% !important" in page
    assert "overflow-x: auto" in page


def test_manual_view_returns_to_the_portal_experiment_chooser() -> None:
    import inspect

    from controllers.console import campus

    source = inspect.getsource(campus.CampusLearningDocumentViewApi)

    # The plain chooser URL: the portal restores a live session on any load,
    # so no return marker is needed.
    assert '/portal/"' in source
    assert "from=manual" not in source


def test_image_upload_is_administrator_only_and_attributed() -> None:
    import inspect

    from controllers.console import campus_admin

    source = inspect.getsource(campus_admin.CampusAdminLabManualImageApi)

    assert "require_admin(current_user)" in source
    assert "@login_required" in source
    assert "actor_account_id=current_user.id" in source
