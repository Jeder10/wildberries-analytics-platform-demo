"""Register a per-source calendar task for the current Windows user."""

import csv
from datetime import datetime, time, timedelta
import subprocess
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET

from .app_paths import APP_ROOT, worker_command
from .wb_sales import MOSCOW_TIME


STATE_DIR = APP_ROOT / "data" / "export-panel"
FREQUENCIES = {"daily": "Ежедневно", "weekly": "Еженедельно", "monthly": "Ежемесячно"}
WEEKDAYS = {
    "Monday": "Понедельник", "Tuesday": "Вторник", "Wednesday": "Среда",
    "Thursday": "Четверг", "Friday": "Пятница", "Saturday": "Суббота", "Sunday": "Воскресенье",
}
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def monthly_day(source: str) -> int:
    return 8 if source == "finance" else 1


def latest_occurrence(
    frequency: str, clock: time, now: datetime, *, weekday: int = 0, day_of_month: int = 1,
) -> datetime:
    day = now.astimezone(MOSCOW_TIME).date()
    if frequency == "weekly":
        day -= timedelta(days=(day.weekday() - weekday) % 7)
    elif frequency == "monthly":
        day = day.replace(day=day_of_month)
    current = datetime.combine(day, clock, MOSCOW_TIME)
    if current <= now:
        return current
    if frequency == "daily":
        return current - timedelta(days=1)
    if frequency == "weekly":
        return current - timedelta(days=7)
    previous = (day.replace(day=1) - timedelta(days=1)).replace(day=day_of_month)
    return datetime.combine(previous, clock, MOSCOW_TIME)


def next_occurrence(
    frequency: str, clock: time, now: datetime, *, weekday: int = 0, day_of_month: int = 1,
) -> datetime:
    previous = latest_occurrence(frequency, clock, now, weekday=weekday, day_of_month=day_of_month)
    if frequency == "monthly":
        first = (previous.date().replace(day=1) + timedelta(days=32)).replace(day=1)
        return datetime.combine(first.replace(day=day_of_month), clock, MOSCOW_TIME)
    return previous + timedelta(days=7 if frequency == "weekly" else 1)


def task_name(source: str) -> str:
    return f"Wildberries Analytics - {source}"


def task_xml(
    source: str, period: str, frequency: str, clock: time, now: datetime, sid: str,
    *, weekday: int = 0, day_of_month: int | None = None,
) -> bytes:
    day_of_month = monthly_day(source) if day_of_month is None else day_of_month
    root = ET.Element("Task", {"version": "1.3", "xmlns": TASK_NAMESPACE})

    def element(parent, name, value=None, **attributes):
        item = ET.SubElement(parent, name, attributes)
        item.text = value
        return item

    triggers = element(root, "Triggers")
    trigger = element(triggers, "CalendarTrigger")
    start = next_occurrence(frequency, clock, now, weekday=weekday, day_of_month=day_of_month)
    element(trigger, "StartBoundary", start.isoformat())
    element(trigger, "Enabled", "true")
    if frequency == "daily":
        element(element(trigger, "ScheduleByDay"), "DaysInterval", "1")
    elif frequency == "weekly":
        weekly = element(trigger, "ScheduleByWeek")
        element(weekly, "WeeksInterval", "1")
        element(element(weekly, "DaysOfWeek"), tuple(WEEKDAYS)[weekday])
    elif frequency == "monthly":
        monthly = element(trigger, "ScheduleByMonth")
        element(element(monthly, "DaysOfMonth"), "Day", str(day_of_month))
        months = element(monthly, "Months")
        for month in (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ):
            element(months, month)
    else:
        raise ValueError("Неизвестная частота запуска.")

    login = element(triggers, "LogonTrigger")
    element(login, "UserId", sid)
    element(login, "Delay", "PT1M")
    principal = element(element(root, "Principals"), "Principal", id="Owner")
    element(principal, "UserId", sid)
    element(principal, "LogonType", "InteractiveToken")
    element(principal, "RunLevel", "LeastPrivilege")
    settings = element(root, "Settings")
    element(settings, "MultipleInstancesPolicy", "IgnoreNew")
    element(settings, "DisallowStartIfOnBatteries", "false")
    element(settings, "StopIfGoingOnBatteries", "false")
    element(settings, "StartWhenAvailable", "true")
    element(settings, "ExecutionTimeLimit", "P3D")

    action = element(element(root, "Actions", Context="Owner"), "Exec")
    executable, *args = worker_command(
        "scheduled", source, period,
        frequency, clock.isoformat(timespec="minutes"), now.isoformat(timespec="seconds"),
        "--weekday", str(weekday), "--month-day", str(day_of_month),
        windowless=True,
    )
    element(action, "Command", executable)
    element(action, "WorkingDirectory", str(APP_ROOT))
    element(action, "Arguments", subprocess.list2cmdline(args))
    return ET.tostring(root, encoding="utf-16", xml_declaration=True)


def register_schedule(
    source: str, period: str, frequency: str, clock: time, *, weekday: int = 0, day_of_month: int | None = None,
) -> None:
    now = datetime.now(MOSCOW_TIME)
    who = subprocess.check_output(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"], text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sid = next(csv.reader([who.strip()]))[-1]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=STATE_DIR, suffix=".xml", delete_on_close=False) as temp:
        temp.write(task_xml(
            source, period, frequency, clock, now, sid, weekday=weekday, day_of_month=day_of_month,
        ))
        temp.close()
        subprocess.run(
            ["schtasks.exe", "/Create", "/TN", task_name(source), "/XML", temp.name, "/F"],
            check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )


def remove_schedule(source: str) -> None:
    subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", task_name(source), "/F"],
        check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def schedule_exists(source: str) -> bool:
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name(source)],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0
