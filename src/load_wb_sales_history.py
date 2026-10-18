"""Backfill and resume Wildberries Statistics sales history."""

import argparse
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
import sys
from time import sleep

import psycopg

from .load_wb_cards import load_database_config
from .load_wb_sales import store_sales
from .wb_cards import (
    CardCheckError, ENV_PATH, RateLimitError, TransportError, load_token, read_token_claims,
)
from .wb_sales import MOSCOW_TIME, parse_sales_timestamp, request_sales_changes


PROGRESS_SCHEMA_PATH = ENV_PATH.parent / "sql" / "005_sales_history_progress.sql"
BASIC_INTERVAL = timedelta(hours=2)
STANDARD_INTERVAL = timedelta(minutes=1)
FAILED_REQUEST_MARGIN = timedelta(minutes=1)
CONNECT_RETRY_INTERVAL = timedelta(minutes=5)
MAX_TRANSPORT_FAILURES = 3


def cursor_sort_key(cursor: str) -> tuple[datetime, Decimal]:
    if len(cursor) == 10:
        return datetime.combine(date.fromisoformat(cursor), time.min, MOSCOW_TIME), Decimal(0)

    timestamp = parse_sales_timestamp(cursor, "lastChangeDate")
    fraction = re.search(r":\d{2}[.,](\d+)", cursor)
    fraction_value = Decimal(f"0.{fraction[1]}") if fraction else Decimal(0)
    return timestamp.replace(microsecond=0), fraction_value


def wait_for_request(next_request_at: datetime) -> None:
    remaining = (next_request_at - datetime.now(timezone.utc)).total_seconds()
    if remaining > 0:
        moscow = next_request_at.astimezone(MOSCOW_TIME)
        print(f"Следующий запрос после {moscow:%d.%m %H:%M:%S} МСК.", flush=True)
    while remaining > 0:
        sleep(min(remaining, 60))
        remaining = (next_request_at - datetime.now(timezone.utc)).total_seconds()


def schedule_request(connection: psycopg.Connection, start_date: date, delay: timedelta) -> None:
    connection.execute(
        """
        UPDATE wb.sales_history_progress
        SET next_request_at = %s WHERE start_date = %s
        """,
        (datetime.now(timezone.utc) + delay, start_date),
    )


def load_history(connection: psycopg.Connection, token: str, start_date: date) -> None:
    """Load pages using a dedicated autocommit connection."""
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(hashtext('wb.sales_history_progress'))"
    ).fetchone()[0]
    if not locked:
        raise CardCheckError("Загрузка истории продаж уже запущена в этом PostgreSQL.")

    account_type = read_token_claims(token, "WB_API_TOKEN").get("acc")
    interval = STANDARD_INTERVAL if account_type in (2, 3) else BASIC_INTERVAL
    connection.execute(PROGRESS_SCHEMA_PATH.read_text(encoding="utf-8"), prepare=False)
    connection.execute(
        """
        INSERT INTO wb.sales_history_progress (start_date, cursor_value)
        VALUES (%s, %s)
        ON CONFLICT (start_date) DO NOTHING
        """,
        (start_date, start_date.isoformat()),
    )

    transport_failures = 0
    while True:
        cursor, completed, ready_at = connection.execute(
            """
            SELECT cursor_value, completed,
                (SELECT max(next_request_at) FROM wb.sales_history_progress)
            FROM wb.sales_history_progress
            WHERE start_date = %s
            """,
            (start_date,),
        ).fetchone()
        if completed:
            print("Выгрузка истории завершена. Последняя страница пуста.", flush=True)
            return
        if ready_at is not None:
            wait_for_request(ready_at)

        # Reserve the pause before the request so a restarted process will also respect it.
        schedule_request(connection, start_date, interval + FAILED_REQUEST_MARGIN)
        try:
            operations = request_sales_changes(token, cursor)
        except RateLimitError as error:
            transport_failures = 0
            pause = interval if error.retry_after is None else timedelta(seconds=error.retry_after + 1)
            schedule_request(connection, start_date, pause)
            print("Wildberries ответил HTTP 429. Загрузчик выдержит паузу и повторит запрос.", flush=True)
            continue
        except TransportError as error:
            transport_failures += 1
            pause = interval if error.request_started else CONNECT_RETRY_INTERVAL
            schedule_request(connection, start_date, pause)
            if transport_failures >= MAX_TRANSPORT_FAILURES:
                raise CardCheckError(f"{error} Исчерпаны автоматические попытки.") from None
            print(f"{error} Загрузчик повторит запрос после паузы.", flush=True)
            continue
        transport_failures = 0

        next_cursor = cursor
        if operations:
            next_cursor = operations[-1]["lastChangeDate"]
            if cursor_sort_key(next_cursor) <= cursor_sort_key(cursor):
                raise CardCheckError("Wildberries вернул непустую страницу без продвижения курсора.")

        with connection.transaction():
            changed = store_sales(connection, operations) if operations else 0
            connection.execute(
                """
                UPDATE wb.sales_history_progress
                SET cursor_value = %s, completed = %s, next_request_at = %s
                WHERE start_date = %s
                """,
                (next_cursor, not operations, datetime.now(timezone.utc) + interval, start_date),
            )

        sales = sum(operation["saleID"].startswith("S") for operation in operations)
        print(
            f"HTTP 200. Операций: {len(operations)}. Продаж: {sales}. "
            f"Возвратов: {len(operations) - sales}. Записано или обновлено: {changed}.",
            flush=True,
        )


def main(start_date: date, env_path: Path = ENV_PATH) -> int:
    try:
        config = load_database_config(env_path)
        token = load_token(env_path)
        with psycopg.connect(**config, autocommit=True) as connection:
            load_history(connection, token, start_date)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать SQL-схему загрузки истории.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Выгрузить историю изменений продаж и возвратов Wildberries.")
    parser.add_argument("--changed-since", required=True, type=date.fromisoformat, help="Дата в формате ГГГГ-ММ-ДД.")
    raise SystemExit(main(parser.parse_args().changed_since))
