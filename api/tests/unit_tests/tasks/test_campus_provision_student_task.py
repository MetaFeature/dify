"""Unit tests for the Campus roster warm-up task.

The task exists so a student's first sign-in does not have to build their
workspace. What it must do: provision the named student, skip one that is gone,
and swallow a failure because signing in repeats the same work with a user
waiting.
"""

from unittest.mock import MagicMock, patch

from tasks.campus_provision_student_task import provision_student_workspace_task


def _run_with_student(student):
    """Run the task against a fake session that returns ``student``, and report the calls."""
    session = MagicMock()
    session.scalar.return_value = student
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = None
    factory = MagicMock()
    factory.create_session.return_value = context
    provisioner = MagicMock()
    with (
        patch("tasks.campus_provision_student_task.session_factory", factory),
        patch(
            "controllers.console.campus_dependencies.build_platform_provisioner",
            return_value=provisioner,
        ),
    ):
        provision_student_workspace_task.run("20260001")
    return provisioner


def test_warm_up_provisions_the_named_student() -> None:
    provisioner = _run_with_student(MagicMock(id="student-1", deleted_at=None))

    provisioner.ensure_ready.assert_called_once_with("student-1")


def test_warm_up_skips_a_student_that_is_gone() -> None:
    assert not _run_with_student(None).ensure_ready.called
    assert not _run_with_student(MagicMock(id="student-1", deleted_at=object())).ensure_ready.called


def test_warm_up_failure_does_not_propagate() -> None:
    provisioner = MagicMock()
    provisioner.ensure_ready.side_effect = RuntimeError("gateway is down")

    session = MagicMock()
    session.scalar.return_value = MagicMock(id="student-1", deleted_at=None)
    context = MagicMock()
    context.__enter__.return_value = session
    context.__exit__.return_value = None
    factory = MagicMock()
    factory.create_session.return_value = context

    with (
        patch("tasks.campus_provision_student_task.session_factory", factory),
        patch(
            "controllers.console.campus_dependencies.build_platform_provisioner",
            return_value=provisioner,
        ),
    ):
        provision_student_workspace_task.run("20260001")  # must not raise

    provisioner.ensure_ready.assert_called_once_with("student-1")

