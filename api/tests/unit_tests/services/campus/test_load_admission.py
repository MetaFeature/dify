from services.campus.load_admission import SystemLoadAdmission


def test_system_load_admission_accepts_below_configured_load_per_cpu() -> None:
    admission = SystemLoadAdmission(
        max_load_per_cpu=0.75,
        load_average=lambda: (4.0, 5.0, 6.0),
        cpu_count=lambda: 8,
    )

    assert admission.allows_current_slot_reservation() is True


def test_system_load_admission_rejects_above_configured_load_per_cpu() -> None:
    admission = SystemLoadAdmission(
        max_load_per_cpu=0.75,
        load_average=lambda: (6.1, 5.0, 4.0),
        cpu_count=lambda: 8,
    )

    assert admission.allows_current_slot_reservation() is False


def test_system_load_admission_fails_closed_when_signal_is_unavailable() -> None:
    def unavailable_load_average() -> tuple[float, float, float]:
        raise OSError("load signal unavailable")

    admission = SystemLoadAdmission(
        max_load_per_cpu=1.0,
        load_average=unavailable_load_average,
        cpu_count=lambda: 8,
    )

    assert admission.allows_current_slot_reservation() is False
