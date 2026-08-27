"""Fail-closed runtime load admission for supplemental current-slot reservations."""

import logging
import math
import os
from collections.abc import Callable

logger = logging.getLogger(__name__)


class SystemLoadAdmission:
    """Compare the one-minute system load average with schedulable CPU capacity."""

    _max_load_per_cpu: float
    _load_average: Callable[[], tuple[float, float, float]]
    _cpu_count: Callable[[], int | None]

    def __init__(
        self,
        *,
        max_load_per_cpu: float,
        load_average: Callable[[], tuple[float, float, float]] = os.getloadavg,
        cpu_count: Callable[[], int | None] = os.cpu_count,
    ) -> None:
        if not math.isfinite(max_load_per_cpu) or max_load_per_cpu <= 0:
            raise ValueError("max_load_per_cpu must be positive and finite")
        self._max_load_per_cpu = max_load_per_cpu
        self._load_average = load_average
        self._cpu_count = cpu_count

    def allows_current_slot_reservation(self) -> bool:
        """Return true only when a valid one-minute load signal is within the configured threshold."""

        try:
            one_minute_load = self._load_average()[0]
            cpu_count = self._cpu_count()
        except (IndexError, OSError, TypeError, ValueError) as error:
            logger.warning("Current-slot load admission could not read the system load signal: %s", error)
            return False
        if cpu_count is None or cpu_count <= 0 or not math.isfinite(one_minute_load) or one_minute_load < 0:
            logger.warning(
                "Current-slot load admission received an invalid system load signal: load=%r cpu_count=%r",
                one_minute_load,
                cpu_count,
            )
            return False
        load_per_cpu = one_minute_load / cpu_count
        allowed = load_per_cpu <= self._max_load_per_cpu
        if not allowed:
            logger.warning(
                "Current-slot supplemental reservation rejected by load admission",
                extra={"load_per_cpu": load_per_cpu, "max_load_per_cpu": self._max_load_per_cpu},
            )
        return allowed
