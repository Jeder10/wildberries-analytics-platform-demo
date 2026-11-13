"""Per-source scheduling options shared by the Windows panel and its setup."""

from dataclasses import dataclass
from datetime import time

from .export_jobs import PERIODS, SNAPSHOTS, read_object, write_object
from .windows_schedule import FREQUENCIES, STATE_DIR, monthly_day, register_schedule, remove_schedule, schedule_exists


DEFAULTS = {
    "cards": ("daily", "07:30", "day", 0),
    "orders": ("daily", "08:10", "day", 0),
    "sales": ("daily", "11:30", "day", 0),
    "stocks": ("daily", "13:30", "day", 0),
    "funnel": ("daily", "15:00", "current_month", 0),
    "finance": ("weekly", "17:00", "week", 1),
}


def source_periods(source: str) -> tuple[str, ...]:
    if source in SNAPSHOTS:
        return ("day",)
    if source == "finance":
        return ("week", "month")
    return tuple(PERIODS)


def schedule_file(source: str):
    return STATE_DIR / f"{source}-schedule.json"


@dataclass(frozen=True)
class Schedule:
    frequency: str
    clock: time
    period: str
    weekday: int
    day_of_month: int
    enabled: bool = False

    def validate(self, source: str) -> None:
        if self.frequency not in FREQUENCIES or self.period not in source_periods(source):
            raise ValueError("Выберите подходящую частоту и период.")
        if self.clock.second or self.clock.microsecond or self.clock.utcoffset() is not None:
            raise ValueError("Укажите московское время в формате ЧЧ:ММ.")
        if self.weekday not in range(7) or not 1 <= self.day_of_month <= 28:
            raise ValueError("Выберите день недели или число от 1 до 28.")
        if source == "finance" and self.frequency == "monthly" and self.period == "month" and self.day_of_month < 8:
            raise ValueError("Для финансов за прошлый месяц выберите число с 8-го по 28-е.")


def get_schedule(source: str) -> Schedule:
    frequency, clock, period, weekday = DEFAULTS[source]
    recorded = read_object(schedule_file(source))
    return Schedule(
        recorded.get("frequency", frequency),
        time.fromisoformat(recorded.get("clock", clock)),
        recorded.get("period", period),
        recorded.get("weekday", weekday if not recorded else 0),
        recorded.get("day_of_month", monthly_day(source)),
        recorded.get("enabled", bool(recorded)),
    )


def save_schedule(source: str, schedule: Schedule) -> None:
    schedule.validate(source)
    if schedule.enabled:
        register_schedule(
            source, schedule.period, schedule.frequency, schedule.clock,
            weekday=schedule.weekday, day_of_month=schedule.day_of_month,
        )
    elif schedule_exists(source):
        remove_schedule(source)
    write_object(schedule_file(source), {
        "frequency": schedule.frequency, "clock": schedule.clock.isoformat(timespec="minutes"),
        "period": schedule.period, "weekday": schedule.weekday,
        "day_of_month": schedule.day_of_month, "enabled": schedule.enabled,
    })
