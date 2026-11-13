"""Run the same export jobs from the panel and Windows Task Scheduler."""

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
import msvcrt
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep

import psycopg

from . import (
    load_wb_cards, load_wb_finance, load_wb_funnel_months, load_wb_order_history,
    load_wb_sales_history, load_wb_stocks,
)
from .report_preview import open_report
from .wb_cards import CardCheckError
from .wb_sales import MOSCOW_TIME
from .windows_schedule import FREQUENCIES, STATE_DIR, latest_occurrence, monthly_day


SOURCES = {
    "cards": "Карточки", "orders": "Заказы", "sales": "Продажи и возвраты",
    "stocks": "Остатки", "finance": "Финансы", "funnel": "Воронка",
}
PERIODS = {
    "day": "Вчера", "week": "Прошлая неделя", "month": "Прошлый месяц", "current_month": "Текущий месяц",
}
SNAPSHOTS = {"cards", "stocks"}


def period_dates(period: str, today: date) -> tuple[date, date]:
    if period == "day":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    if period == "week":
        this_monday = today - timedelta(days=today.weekday())
        return this_monday - timedelta(days=7), this_monday - timedelta(days=1)
    if period in {"month", "current_month"}:
        end = today - timedelta(days=1) if period == "current_month" else today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    raise ValueError("Выберите день, неделю или месяц.")


@dataclass(frozen=True)
class Request:
    source: str
    first: date | None = None
    last: date | None = None

    def check(self, today: date) -> None:
        if self.source in SNAPSHOTS:
            return
        if self.first is None or self.last is None or self.first > self.last or self.last >= today:
            raise ValueError("Выберите завершённый период с корректными начальной и конечной датами.")

    def check_for_loading(self, today: date) -> None:
        self.check(today)
        if self.source in {"orders", "sales"} and self.first < today - timedelta(days=90):
            raise ValueError("Оперативная выгрузка доступна в пределах 90 дней. Откройте сохранённые данные в отчёте.")
        if self.source == "finance" and self.last + timedelta(days=6 - self.last.weekday()) >= today:
            raise ValueError("Для финансов выберите период, входящий в завершённые недели.")

    def display_period(self) -> str:
        if self.first is None:
            return "Текущий снимок"
        return f"{self.first:%d.%m.%Y} – {self.last:%d.%m.%Y}"

    def fields(self) -> dict:
        return {
            "first": self.first.isoformat() if self.first is not None else None,
            "last": self.last.isoformat() if self.last is not None else None,
        }

    @classmethod
    def from_state(cls, source: str, state: dict):
        first = state.get("first")
        last = state.get("last")
        return cls(source, date.fromisoformat(first) if first else None, date.fromisoformat(last) if last else None)


def planned_request(source: str, period: str, today: date) -> Request:
    return Request(source) if source in SNAPSHOTS else Request(source, *period_dates(period, today))


def run_loader(request: Request, *, resume: bool = False) -> int:
    source, first, last = request.source, request.first, request.last
    if source == "cards":
        return load_wb_cards.main()
    if source == "stocks":
        return load_wb_stocks.main()
    if source == "orders":
        return load_wb_order_history.main(first, refresh_completed=not resume)
    if source == "sales":
        return load_wb_sales_history.main(first, refresh_completed=not resume)
    if source == "finance":
        start_week = first - timedelta(days=first.weekday())
        end_week = last + timedelta(days=6 - last.weekday())
        return load_wb_finance.main(start_week, end_week)
    if source == "funnel":
        return load_wb_funnel_months.main(first.replace(day=1), last)
    raise ValueError("Неизвестный источник.")


def state_file(source: str) -> Path:
    return STATE_DIR / f"{source}.json"


def log_file(source: str) -> Path:
    return STATE_DIR / f"{source}.log"


def read_object(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def write_object(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete_on_close=False) as temporary:
        temporary.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        temporary.close()
        for attempt in range(4):
            try:
                Path(temporary.name).replace(path)
                return
            except PermissionError:
                if attempt == 3:
                    raise
                sleep(0.05)


@contextmanager
def job_lock(source: str):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / f"{source}.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def execute(
    request: Request, *, show_report: bool = False, resume: bool = False, due: str | None = None,
) -> int:
    status_path = state_file(request.source)
    started = datetime.now(MOSCOW_TIME).isoformat(timespec="seconds")
    status = {**request.fields(), "state": "running", "started": started, "due": due}
    write_object(status_path, status)
    with log_file(request.source).open("a" if resume else "w", encoding="utf-8", buffering=1) as stream:
        with redirect_stdout(stream), redirect_stderr(stream):
            print(f"{SOURCES[request.source]} · {request.display_period()}", flush=True)
            try:
                if not resume:
                    request.check_for_loading(datetime.now(MOSCOW_TIME).date())
                result = run_loader(request, resume=resume)
            except ValueError as error:
                print(str(error))
                result = 1
            except (CardCheckError, OSError, psycopg.Error):
                print("Загрузка прервана. Проверьте локальное подключение и файлы проекта.")
                result = 1
            status["state"] = "failed" if result else "done"
            if result == 0 and show_report:
                try:
                    open_report(request.source, request.first, request.last)
                except (CardCheckError, KeyError, OSError, psycopg.Error, ValueError):
                    print("Строки сохранены. Power BI не открылся. Можно повторить открытие отчёта из панели.")
                    status["state"] = "report_error"
            status["finished"] = datetime.now(MOSCOW_TIME).isoformat(timespec="seconds")
            write_object(status_path, status)
    return result


def scheduled(
    source: str, period: str, frequency: str, clock: time, registered: datetime,
    *, weekday: int = 0, day_of_month: int | None = None,
) -> int:
    now = datetime.now(MOSCOW_TIME)
    day_of_month = monthly_day(source) if day_of_month is None else day_of_month
    due = latest_occurrence(frequency, clock, now, weekday=weekday, day_of_month=day_of_month)
    ledger = STATE_DIR / f"{source}-due.json"
    with job_lock(source) as acquired:
        if not acquired:
            return 0
        status = read_object(state_file(source))
        due_key = due.isoformat()
        previous_key = read_object(ledger).get("due")
        request = planned_request(source, period, due.date())
        run_due = due >= registered and previous_key != due_key
        if status.get("state") == "running":
            pending = Request.from_state(source, status)
            pending_due = status.get("due")
            if run_due and pending == request:
                pending_due = due_key
            result = execute(pending, resume=True, due=pending_due)
            if pending_due is not None:
                write_object(ledger, {"due": pending_due})
            if not run_due or pending_due == due_key:
                return result
        elif status.get("due") == due_key:
            write_object(ledger, {"due": due_key})
            return 0
        if not run_due:
            return 0
        result = execute(request, due=due_key)
        write_object(ledger, {"due": due_key})
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    manual = commands.add_parser("run")
    manual.add_argument("source", choices=SOURCES)
    manual.add_argument("--from", dest="first", type=date.fromisoformat)
    manual.add_argument("--to", dest="last", type=date.fromisoformat)
    manual.add_argument("--open-report", action="store_true")
    calendar = commands.add_parser("scheduled")
    calendar.add_argument("source", choices=SOURCES)
    calendar.add_argument("period", choices=PERIODS)
    calendar.add_argument("frequency", choices=FREQUENCIES)
    calendar.add_argument("clock", type=time.fromisoformat)
    calendar.add_argument("registered", type=datetime.fromisoformat)
    calendar.add_argument("--weekday", type=int, choices=range(7), default=0)
    calendar.add_argument("--month-day", type=int, choices=range(1, 29))
    args = parser.parse_args()
    if args.command == "scheduled":
        return scheduled(
            args.source, args.period, args.frequency, args.clock, args.registered,
            weekday=args.weekday, day_of_month=args.month_day,
        )
    with job_lock(args.source) as acquired:
        if not acquired:
            return 2
        return execute(Request(args.source, args.first, args.last), show_report=args.open_report)


if __name__ == "__main__":
    raise SystemExit(main())
