"""Load one independently filterable Wildberries sales-funnel result per Moscow month."""

import argparse
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys

import psycopg

from .app_paths import RESOURCE_ROOT
from .load_wb_cards import load_database_config
from .load_wb_sales_history import wait_for_request
from .wb_cards import (
    CardCheckError, ENV_PATH, RateLimitError, TransportError, check_token_scope,
    fetch_readonly_json, load_token, read_token_claims,
)
from .wb_order_feed import ANALYTICS_SCOPE, MOSCOW_TIME


SCHEMA = RESOURCE_ROOT / "sql" / "012_monthly_sales_funnel.sql"
PAGE_SIZE = 1000
BASIC_PAUSE = timedelta(minutes=30)
STANDARD_PAUSE = timedelta(seconds=20)
PERIOD_QUERY = """
    SELECT next_offset, completed, (SELECT MAX(next_request_at) FROM wb.funnel_month_loads)
    FROM wb.funnel_month_loads WHERE period_start = %s AND period_end = %s
"""


def month_windows(first: date, last: date):
    if first.day != 1 or first > last or last >= datetime.now(MOSCOW_TIME).date():
        raise CardCheckError("Укажите первое число месяца и последний день, который уже завершился по Москве.")
    current = first
    while current <= last:
        month_end = current.replace(day=monthrange(current.year, current.month)[1])
        yield current, min(month_end, last)
        current = month_end + timedelta(days=1)


def initialize_period(connection: psycopg.Connection, start: date, end: date) -> None:
    connection.execute(
        """
        INSERT INTO wb.funnel_month_loads (period_start, period_end)
        VALUES (%s, %s) ON CONFLICT (period_start, period_end) DO NOTHING
        """,
        (start, end),
    )


def decode_page(data: object, start: date, end: date) -> list[tuple[int, int, int, int]]:
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list) or len(products) > PAGE_SIZE:
        raise CardCheckError("HTTP 200. В воронке отсутствует корректная страница товаров.")
    expected_period = {"start": start.isoformat(), "end": end.isoformat()}
    rows = {}
    for result in products:
        try:
            nm_id = result["product"]["nmId"]
            metrics = result["statistic"]["selected"]
            period = metrics["period"]
            values = (metrics["orderCount"], metrics["buyoutCount"], metrics["cancelCount"])
        except (KeyError, TypeError):
            raise CardCheckError("HTTP 200. В строке воронки отсутствуют поля отчёта.") from None
        if period != expected_period:
            raise CardCheckError("HTTP 200. Фактический период воронки отличается от запроса.")
        if type(nm_id) is not int or not 0 < nm_id < 2**63:
            raise CardCheckError("HTTP 200. Некорректный артикул WB в воронке.")
        if nm_id in rows or any(type(value) is not int or not 0 <= value < 2**63 for value in values):
            raise CardCheckError("HTTP 200. Дубликат артикула или некорректный показатель воронки.")
        rows[nm_id] = (nm_id, *values)
    return list(rows.values())


def request_page(token: str, start: date, end: date, offset: int) -> list[tuple[int, int, int, int]]:
    response = fetch_readonly_json(
        token, method="POST", host="seller-analytics-api.wildberries.ru",
        path="/api/analytics/v3/sales-funnel/products", category="Аналитика",
        payload={
            "selectedPeriod": {"start": start.isoformat(), "end": end.isoformat()},
            "nmIds": [], "skipDeletedNm": False, "limit": PAGE_SIZE, "offset": offset,
        },
        timeout=60,
    )
    return decode_page(response.get("data") if isinstance(response, dict) else None, start, end)


def store_page(
    connection: psycopg.Connection,
    start: date,
    end: date,
    offset: int,
    rows: list[tuple[int, int, int, int]],
    next_request_at: datetime,
) -> None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO wb.funnel_month_products (
                    period_start, period_end, nm_id, order_count, buyout_count, cancel_or_return_count
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                ((start, end, *row) for row in rows),
            )
        connection.execute(
            """
            UPDATE wb.funnel_month_loads SET next_offset=%s, completed=%s, next_request_at=%s
            WHERE period_start = %s AND period_end = %s
            """,
            (offset + len(rows), len(rows) < PAGE_SIZE, next_request_at, start, end),
        )


def defer_request(connection: psycopg.Connection, start: date, end: date, delay: timedelta) -> None:
    connection.execute(
        """
        UPDATE wb.funnel_month_loads SET next_request_at=%s
        WHERE period_start = %s AND period_end = %s
        """,
        (datetime.now(timezone.utc) + delay, start, end),
    )


def load_months(connection: psycopg.Connection, token: str, first: date, last: date) -> None:
    windows = list(month_windows(first, last))
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(hashtext('wb.funnel_month_loads'))"
    ).fetchone()[0]
    if not locked:
        raise CardCheckError("Помесячная выгрузка воронки уже запущена в этом PostgreSQL.")
    account_type = read_token_claims(token, "WB_API_TOKEN").get("acc")
    pause = STANDARD_PAUSE if account_type in (2, 3) else BASIC_PAUSE
    connection.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
    for start, end in windows:
        initialize_period(connection, start, end)
        failures = 0
        while True:
            offset, completed, ready_at = connection.execute(PERIOD_QUERY, (start, end)).fetchone()
            if completed:
                break
            if ready_at is not None:
                wait_for_request(ready_at)
            defer_request(connection, start, end, pause + timedelta(minutes=1))
            try:
                rows = request_page(token, start, end, offset)
            except RateLimitError as error:
                delay = pause if error.retry_after is None else timedelta(seconds=error.retry_after + 1)
                defer_request(connection, start, end, delay)
                print("Воронка ответила HTTP 429. Повтор после паузы.", flush=True)
                continue
            except TransportError as error:
                failures += 1
                defer_request(connection, start, end, pause if error.request_started else timedelta(minutes=5))
                if failures >= 3:
                    raise CardCheckError(f"{error} Исчерпаны автоматические попытки.") from None
                print(f"{error} Повтор после паузы.", flush=True)
                continue
            failures = 0
            store_page(connection, start, end, offset, rows, datetime.now(timezone.utc) + pause)
            print(f"Воронка за {start} — {end}. HTTP 200. Строк: {len(rows)}.", flush=True)


def main(first: date, last: date, env_path: Path = ENV_PATH) -> int:
    try:
        token = load_token(env_path)
        check_token_scope(token, scope=ANALYTICS_SCOPE, category="Аналитика")
        with psycopg.connect(**load_database_config(env_path), autocommit=True) as connection:
            load_months(connection, token, first, last)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать SQL-схему воронки.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Выгрузить воронку Wildberries по месяцам.")
    parser.add_argument("--date-from", required=True, type=date.fromisoformat)
    parser.add_argument("--date-to", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    raise SystemExit(main(args.date_from, args.date_to))
