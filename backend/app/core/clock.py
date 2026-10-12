"""Injectable clock for the identity path, so tests move time instead of sleeping.

The bid path never uses this: arbitration time comes from Redis TIME inside the script (DC-3).
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
