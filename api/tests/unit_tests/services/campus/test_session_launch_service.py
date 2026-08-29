import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from models.campus import (
    CampusAccessSlot,
    CampusPortalSession,
    CampusReservation,
    CampusStudent,
    CampusWorkspaceBinding,
    ReservationStatus,
    StudentStatus,
)
from services.account_service import TokenPair
from services.campus.errors import AccessSlotRequiredError
from services.campus.identity_source import UnconfiguredIdentitySource
from services.campus.portal_session_service import PortalSessionService
from services.campus.reservation_service import ReservationService
from services.campus.session_launch_service import SessionLaunchService


class FakeSessionIssuer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def issue(self, account_id: str, tenant_id: str, *, ip_address: str | None) -> TokenPair:
        self.calls.append((account_id, tenant_id, ip_address))
        return TokenPair(access_token="access", refresh_token="refresh", csrf_token="csrf")


class UnusedPlatformProvisioner:
    def ensure_ready(self, student_id: str) -> None:
        raise AssertionError("session resolution must not provision a platform")


@pytest.fixture
def campus_session(sqlite_engine) -> Session:
    tables = [
        CampusStudent.__table__,
        CampusWorkspaceBinding.__table__,
        CampusPortalSession.__table__,
        CampusAccessSlot.__table__,
        CampusReservation.__table__,
    ]
    CampusStudent.metadata.create_all(sqlite_engine, tables=tables)
    with Session(sqlite_engine, expire_on_commit=False) as session:
        student = CampusStudent(
            student_number="20260001",
            display_name="Student One",
            status=StudentStatus.ACTIVE,
            initial_allowance_usd=Decimal(0),
        )
        session.add(student)
        session.flush()
        session.add(
            CampusWorkspaceBinding(
                student_id=student.id,
                dify_account_id="account-1",
                dify_tenant_id="tenant-1",
            )
        )
        session.commit()
        yield session


def _real_session_composition(campus_session: Session) -> tuple[str, FakeSessionIssuer, SessionLaunchService]:
    student = campus_session.query(CampusStudent).one()
    portal_token = "portal-token"
    campus_session.add(
        CampusPortalSession(
            student_id=student.id,
            token_hash=hashlib.sha256(portal_token.encode()).hexdigest(),
            expires_at=datetime(2026, 8, 11, 5, 0),
            last_seen_at=datetime(2026, 8, 11, 2, 0),
        )
    )
    slot = CampusAccessSlot(
        starts_at=datetime(2026, 8, 11, 2, 0),
        ends_at=datetime(2026, 8, 11, 4, 0),
        capacity=500,
    )
    campus_session.add(slot)
    campus_session.flush()
    campus_session.add(
        CampusReservation(
            student_id=student.id,
            slot_id=slot.id,
            status=ReservationStatus.CONFIRMED,
            queued_at=datetime(2026, 8, 11, 0, 30),
            queue_sequence=1,
            confirmed_at=datetime(2026, 8, 11, 0, 30),
        )
    )
    campus_session.commit()
    issuer = FakeSessionIssuer()
    service = SessionLaunchService(
        session=campus_session,
        portal_sessions=PortalSessionService(
            session=campus_session,
            identity_source=UnconfiguredIdentitySource(),
            platform_provisioner=UnusedPlatformProvisioner(),
            session_ttl=timedelta(hours=3),
        ),
        reservations=ReservationService(session=campus_session, capacity=500, booking_days=7),
        session_issuer=issuer,
    )
    return portal_token, issuer, service


def test_public_session_composition_launches_inside_slot_even_with_zero_model_allowance(campus_session: Session):
    portal_token, issuer, service = _real_session_composition(campus_session)

    tokens = service.launch(
        portal_token,
        now=datetime(2026, 8, 11, 2, 30, tzinfo=UTC),
        ip_address="10.0.0.8",
    )

    assert tokens.access_token == "access"
    assert issuer.calls == [("account-1", "tenant-1", "10.0.0.8")]


def test_public_session_composition_fails_closed_outside_reserved_slot(campus_session: Session):
    portal_token, issuer, service = _real_session_composition(campus_session)

    with pytest.raises(AccessSlotRequiredError):
        service.launch(
            portal_token,
            now=datetime(2026, 8, 11, 1, 59, tzinfo=UTC),
            ip_address=None,
        )

    assert issuer.calls == []
