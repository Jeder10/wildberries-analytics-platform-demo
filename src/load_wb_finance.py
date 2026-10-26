"""Load a range of weekly Wildberries reports with an on-disk database cursor."""

import argparse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import psycopg

from .load_wb_cards import load_database_config
from .load_wb_sales_history import wait_for_request
from .wb_cards import CardCheckError, ENV_PATH, RateLimitError, TransportError, load_token, read_token_claims
from .wb_finance import FinancePage, request_finance_page


SCHEMA_PATH = ENV_PATH.parent / "sql" / "009_finance_reports.sql"
BASIC_INTERVAL = timedelta(hours=12)
STANDARD_INTERVAL = timedelta(minutes=1)
INSERT_LINE = """
    INSERT INTO wb.finance_report_lines (
        rrd_id, report_id, report_type, report_from, report_to,
        operation_date, nm_id, currency, operation_type, retail_amount, for_pay
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (rrd_id) DO NOTHING
"""


def initialize_load(connection: psycopg.Connection, start_date: date, end_date: date) -> None:
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"), prepare=False)
    connection.execute(
        """
        INSERT INTO wb.finance_report_loads (start_date, end_date)
        VALUES (%s, %s) ON CONFLICT (start_date, end_date) DO NOTHING
        """,
        (start_date, end_date),
    )


def record_page(
    connection: psycopg.Connection,
    start_date: date,
    end_date: date,
    page: FinancePage | None,
    next_request_at: datetime,
) -> int:
    with connection.transaction():
        if page is None:
            connection.execute(
                """
                UPDATE wb.finance_report_loads SET completed = true, next_request_at = %s
                WHERE start_date = %s AND end_date = %s
                """,
                (next_request_at, start_date, end_date),
            )
            return 0

        changed = 0
        if page.operations:
            with connection.cursor() as cursor:
                cursor.executemany(INSERT_LINE, page.operations)
                changed = cursor.rowcount
        connection.execute(
            """
            UPDATE wb.finance_report_loads SET last_rrd_id = %s, next_request_at = %s
            WHERE start_date = %s AND end_date = %s
            """,
            (page.last_rrd_id, next_request_at, start_date, end_date),
        )
        return changed


def schedule_request(
    connection: psycopg.Connection, start_date: date, end_date: date, delay: timedelta
) -> None:
    connection.execute(
        """
        UPDATE wb.finance_report_loads SET next_request_at = %s
        WHERE start_date = %s AND end_date = %s
        """,
        (datetime.now(timezone.utc) + delay, start_date, end_date),
    )


def load_reports(connection: psycopg.Connection, token: str, start_date: date, end_date: date) -> None:
    if end_date < start_date:
        raise CardCheckError("Конец периода должен быть не раньше начала.")
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(hashtext('wb.finance_report_loads'))"
    ).fetchone()[0]
    if not locked:
        raise CardCheckError("Финансовая выгрузка уже запущена в этом PostgreSQL.")

    account_type = read_token_claims(token, "WB_API_TOKEN").get("acc")
    interval = STANDARD_INTERVAL if account_type in (2, 3) else BASIC_INTERVAL
    initialize_load(connection, start_date, end_date)
    consecutive_errors = 0
    while True:
        cursor, complete, ready_at = connection.execute(
            """
            SELECT last_rrd_id, completed, (SELECT max(next_request_at) FROM wb.finance_report_loads)
            FROM wb.finance_report_loads WHERE start_date = %s AND end_date = %s
            """,
            (start_date, end_date),
        ).fetchone()
        if complete:
            print("Выгрузка финансового отчёта завершена.", flush=True)
            return
        if ready_at is not None:
            wait_for_request(ready_at)

        schedule_request(connection, start_date, end_date, interval + timedelta(minutes=1))
        try:
            page = request_finance_page(token, start_date, end_date, cursor)
        except RateLimitError as error:
            consecutive_errors = 0
            delay = interval if error.retry_after is None else timedelta(seconds=error.retry_after + 1)
            schedule_request(connection, start_date, end_date, delay)
            print("Финансовый API ответил HTTP 429. Загрузчик повторит запрос после паузы.", flush=True)
            continue
        except TransportError as error:
            consecutive_errors += 1
            delay = interval if error.request_started else timedelta(minutes=5)
            schedule_request(connection, start_date, end_date, delay)
            if consecutive_errors >= 3:
                raise CardCheckError(f"{error} Исчерпаны автоматические попытки.") from None
            print(f"{error} Загрузчик повторит запрос после паузы.", flush=True)
            continue
        consecutive_errors = 0

        changed = record_page(connection, start_date, end_date, page, datetime.now(timezone.utc) + interval)
        if page is None:
            print("HTTP 204. Wildberries подтвердил конец отчёта.", flush=True)
            return
        print(
            f"HTTP 200. Строк отчёта: {page.row_count}. "
            f"Строк продаж и возвратов: {len(page.operations)}. Вставлено: {changed}.",
            flush=True,
        )


def main(start_date: date, end_date: date, env_path: Path = ENV_PATH) -> int:
    try:
        config = load_database_config(env_path)
        token = load_token(env_path)
        with psycopg.connect(**config, autocommit=True) as connection:
            load_reports(connection, token, start_date, end_date)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать SQL-схему финансового отчёта.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Загрузить детализацию еженедельных отчётов Wildberries.")
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    raise SystemExit(main(args.date_from, args.date_to))
