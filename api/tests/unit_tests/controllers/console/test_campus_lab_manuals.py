"""Route-level contract for the lab manual endpoints.

The service layer is tested directly; what these check is the wiring nobody
notices until it is wrong: that the routes exist under the paths the portal and
the administration page call, that the student-facing ones sit on the portal
session rather than the console session, and that a student route cannot serve a
draft.
"""

import pytest

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


def test_administrators_can_read_one_chapter_with_its_body() -> None:
    # The chapter list deliberately omits body_html, so editing needs a route
    # that returns it; without one the edit form would open empty and a save
    # would wipe the chapter.
    import inspect

    from controllers.console import campus_admin

    assert "/campus/admin/lab-manuals/chapters/<string:chapter_id>" in _routes()
    source = inspect.getsource(campus_admin.CampusAdminLabManualChapterApi)
    assert "def get(" in source
    assert "body_html" in source
