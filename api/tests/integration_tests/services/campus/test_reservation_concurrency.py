"""PostgreSQL concurrency proof for the Campus capacity boundary."""

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from models.campus import CampusAccessSlot, CampusReservation, CampusStudent, ReservationStatus, StudentStatus
from services.campus.reservation_service import ReservationService


class AllowCurrentSlotAdmission:
    def allows_current_slot_reservation(self) -> bool:
        return True


@pytest.mark.parametrize(
    ("now", "load_admission"),
    [
        (datetime(2026, 8, 11, 0, 30, tzinfo=UTC), None),
        (datetime(2026, 8, 11, 2, 30, tzinfo=UTC), AllowCurrentSlotAdmission()),
    ],
    ids=("advance-booking", "current-slot-supplemental-booking"),
)
def test_concurrent_last_seat_creates_one_confirmation_and_one_waiter(
    now: datetime,
    load_admission: AllowCurrentSlotAdmission | None,
) -> None:
    database_url = os.getenv("CAMPUS_TEST_DATABASE_URL")
    if not database_url or not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.skip("CAMPUS_TEST_DATABASE_URL must point to an isolated PostgreSQL test database")

    admin_engine = create_engine(database_url)
    schema = f"campus_test_{uuid.uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(database_url, connect_args={"options": f"-csearch_path={schema}"})
    tables = [CampusStudent.__table__, CampusAccessSlot.__table__, CampusReservation.__table__]
    CampusStudent.metadata.create_all(engine, tables=tables)
    try:
        with Session(engine, expire_on_commit=False) as session:
            students = [
                CampusStudent(student_number="20260001", display_name="Student One", status=StudentStatus.ACTIVE),
                CampusStudent(student_number="20260002", display_name="Student Two", status=StudentStatus.ACTIVE),
            ]
            session.add_all(students)
            session.commit()
            student_ids = [student.id for student in students]

        barrier = threading.Barrier(2)
        results: list[ReservationStatus] = []
        errors: list[BaseException] = []

        def reserve(student_id: str) -> None:
            try:
                with Session(engine, expire_on_commit=False) as session:
                    barrier.wait(timeout=5)
                    result = ReservationService(
                        session=session,
                        capacity=1,
                        booking_days=7,
                        current_slot_load_admission=load_admission,
                    ).reserve(student_id, datetime(2026, 8, 11, 2, 0, tzinfo=UTC), now=now)
                    results.append(result.status)
            except BaseException as error:  # thread failures must be asserted in the parent
                errors.append(error)

        threads = [threading.Thread(target=reserve, args=(student_id,)) for student_id in student_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert errors == []
        assert sorted(status.value for status in results) == [
            ReservationStatus.CONFIRMED.value,
            ReservationStatus.WAITLISTED.value,
        ]
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()
