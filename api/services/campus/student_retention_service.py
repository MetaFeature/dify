"""Destroy the data of students that were soft-deleted long ago.

Deletion is two stages by design. `StudentAdministrationService.soft_delete`
hides a student and signs them out but keeps every row, so a mistake is one
column away from being undone. This service is the second stage, and it is
irreversible:

* the gateway token is released, so nothing can bill through it any more;
* the Dify account is handed to Dify's own account-deletion pipeline, which is
  what removes the tenant, its apps, its knowledge bases and its files;
* the campus-side rows are deleted outright.

Audit events are deliberately kept: the record that a student was deleted is
the one thing a purge must not erase. Their `target_id` becomes a dangling
reference, which is the correct shape for an audit log.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models.campus import (
    CampusAllowanceAdjustment,
    CampusAuditEvent,
    CampusGatewayBinding,
    CampusPortalSession,
    CampusReservation,
    CampusStudent,
    CampusStudentCredential,
    CampusWorkspaceBinding,
)
from services.campus.domain import ModelGateway
from services.campus.time_utils import to_naive_utc

logger = logging.getLogger(__name__)

#: How long a soft-deleted student is kept before the purge may remove them.
DEFAULT_RETENTION_DAYS = 4 * 365


@dataclass(frozen=True)
class RetentionPurgeResult:
    """`failed` is not an error: one unreachable account must not stop the sweep."""

    purged: tuple[str, ...]
    failed: tuple[tuple[str, str], ...]


class StudentRetentionService:
    """Purge students whose soft-deletion has aged past the retention window."""

    _session: Session
    _gateway: ModelGateway
    _delete_dify_account: Callable[[str], None]
    _retention_days: int

    def __init__(
        self,
        *,
        session: Session,
        gateway: ModelGateway,
        delete_dify_account: Callable[[str], None],
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self._session = session
        self._gateway = gateway
        self._delete_dify_account = delete_dify_account
        self._retention_days = retention_days

    def eligible(self, *, now: datetime) -> list[CampusStudent]:
        """Soft-deleted students old enough to purge, oldest first."""
        cutoff = to_naive_utc(now) - timedelta(days=self._retention_days)
        return list(
            self._session.scalars(
                select(CampusStudent)
                .where(CampusStudent.deleted_at.is_not(None), CampusStudent.deleted_at <= cutoff)
                .order_by(CampusStudent.deleted_at)
            ).all()
        )

    def purge(self, *, now: datetime, actor_account_id: str) -> RetentionPurgeResult:
        """Purge every eligible student and report what happened per row."""
        purged: list[str] = []
        failed: list[tuple[str, str]] = []
        for student in self.eligible(now=now):
            try:
                self._purge_one(student)
            except Exception as error:
                self._session.rollback()
                failed.append((student.student_number, str(error) or error.__class__.__name__))
                continue
            purged.append(student.student_number)
        self._session.add(
            CampusAuditEvent(
                actor_account_id=actor_account_id,
                action="student.retention_purged",
                target_type="student_roster",
                target_id="expired",
                details_json=json.dumps(
                    {"purged": purged, "failed": [number for number, _ in failed]},
                    separators=(",", ":"),
                ),
            )
        )
        self._session.commit()
        return RetentionPurgeResult(purged=tuple(purged), failed=tuple(failed))

    def _purge_one(self, student: CampusStudent) -> None:
        gateway_binding = self._session.scalar(
            select(CampusGatewayBinding).where(CampusGatewayBinding.student_id == student.id)
        )
        if gateway_binding is not None:
            # A token the gateway has already forgotten is not a reason to keep
            # the row, so the failure is logged and the purge continues.
            try:
                self._gateway.delete_managed_token(gateway_binding.gateway_token_id)
            except Exception:
                logger.warning(
                    "Campus gateway token %s was already gone while purging student %s",
                    gateway_binding.gateway_token_id,
                    student.student_number,
                    exc_info=True,
                )

        workspace_binding = self._session.scalar(
            select(CampusWorkspaceBinding).where(CampusWorkspaceBinding.student_id == student.id)
        )
        if workspace_binding is not None:
            self._delete_dify_account(workspace_binding.dify_account_id)

        # Audit events survive on purpose (see the module docstring).
        for table in (
            CampusAllowanceAdjustment,
            CampusReservation,
            CampusPortalSession,
            CampusStudentCredential,
            CampusGatewayBinding,
            CampusWorkspaceBinding,
        ):
            self._session.execute(delete(table).where(table.student_id == student.id))  # type: ignore[attr-defined]
        self._session.delete(student)
        self._session.commit()
