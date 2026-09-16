"""Prepare a student's Dify workspace right after the roster import.

Signing in used to be where a student's workspace was built: the login request
created the Dify account and tenant and configured all four gateway models,
including the real credential probe Dify runs for every model. That is roughly
six seconds of work placed in front of the first student who signs in.

The roster import is the natural place for it instead -- it runs before class,
and the students it provisions are exactly the ones who will sign in. The login
path keeps its own provisioning call as the safety net, so a student whose warm
up failed or never ran still gets a workspace, only slowly.
"""

import logging

from celery import shared_task
from sqlalchemy import select

from core.db.session_factory import session_factory
from models.campus import CampusStudent

logger = logging.getLogger(__name__)


@shared_task(queue="plugin")
def provision_student_workspace_task(student_number: str) -> None:
    """Build one imported student's workspace and configure its models.

    Failures are logged and swallowed: this is a warm up, and the sign-in path
    repeats it with a user waiting, which is where an error belongs.
    """
    from controllers.console.campus_dependencies import build_platform_provisioner

    with session_factory.create_session() as session:
        student = session.scalar(select(CampusStudent).where(CampusStudent.student_number == student_number))
        if student is None or student.deleted_at is not None:
            logger.info("campus warm up: student %s is gone, nothing to do", student_number)
            return
        try:
            build_platform_provisioner(session).ensure_ready(student.id)
        except Exception:
            logger.exception("campus warm up failed for student %s", student_number)
            return
    logger.info("campus warm up: student %s is ready", student_number)
