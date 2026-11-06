"""Backfill the Wildberries Statistics order report without replacing order-feed facts."""

import argparse
from datetime import date, timedelta
from pathlib import Path
import sys
from urllib.parse import urlencode

import psycopg

from .app_paths import RESOURCE_ROOT
from .load_wb_cards import load_database_config
from .load_wb_sales_history import HistorySource, load_paginated_history
from .wb_cards import CardCheckError, ENV_PATH, check_token_scope, fetch_readonly_json, load_token
from .wb_sales import STATISTICS_SCOPE, parse_sales_timestamp


SQL_ROOT = RESOURCE_ROOT / "sql"
ORDER_HISTORY_TIMEOUT = 60
ORDER_HISTORY_SCHEMAS = (
    SQL_ROOT / "003_order_feed.sql",
    SQL_ROOT / "004_sales_operations.sql",
    SQL_ROOT / "008_daily_product_sales.sql",
    SQL_ROOT / "011_daily_product_activity.sql",
)
UPSERT_ORDER = """
    INSERT INTO wb.order_statistics AS stored (
        srid, nm_id, created_at, source_updated_at, is_cancelled
    )
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (srid) DO UPDATE SET
        nm_id = EXCLUDED.nm_id,
        created_at = EXCLUDED.created_at,
        source_updated_at = EXCLUDED.source_updated_at,
        is_cancelled = EXCLUDED.is_cancelled
    WHERE EXCLUDED.source_updated_at > stored.source_updated_at
"""


def validate_order(row: object) -> str:
    if not isinstance(row, dict):
        raise CardCheckError("HTTP 200. Заказ из статистики должен быть объектом JSON.")
    srid, nm_id = row.get("srid"), row.get("nmId")
    if not isinstance(srid, str) or not srid.strip():
        raise CardCheckError("HTTP 200. В заказе из статистики отсутствует srid.")
    if type(nm_id) is not int or not 0 < nm_id < 2**63 or type(row.get("isCancel")) is not bool:
        raise CardCheckError("HTTP 200. В заказе из статистики некорректны артикул или признак отмены.")
    parse_sales_timestamp(row.get("date"), "date")
    parse_sales_timestamp(row.get("lastChangeDate"), "lastChangeDate")
    return srid


def request_order_changes(token: str, date_from: str) -> list[dict]:
    check_token_scope(token, scope=STATISTICS_SCOPE, category="Статистика")
    params = urlencode({"dateFrom": date_from, "flag": 0})
    rows = fetch_readonly_json(
        token, method="GET", host="statistics-api.wildberries.ru",
        path=f"/api/v1/supplier/orders?{params}", category="Статистика", timeout=ORDER_HISTORY_TIMEOUT,
    )
    if not isinstance(rows, list):
        raise CardCheckError("HTTP 200. Ожидался список заказов из статистики.")
    if len({validate_order(row) for row in rows}) != len(rows):
        raise CardCheckError("HTTP 200. В статистике заказов повторился srid.")
    return rows


def store_order_statistics(connection: psycopg.Connection, orders: list[dict]) -> int:
    rows = [
        (
            order["srid"], order["nmId"],
            parse_sales_timestamp(order["date"], "date"),
            parse_sales_timestamp(order["lastChangeDate"], "lastChangeDate"),
            order["isCancel"],
        )
        for order in sorted(orders, key=lambda order: order["srid"])
    ]
    with connection.cursor() as cursor:
        cursor.executemany(UPSERT_ORDER, rows)
        return cursor.rowcount


def describe_orders(orders: list[dict]) -> str:
    return f"Заказанных единиц: {len(orders)}. Отменено: {sum(order['isCancel'] for order in orders)}."


def main(start_date: date, env_path: Path = ENV_PATH, *, refresh_completed: bool = False) -> int:
    try:
        token = load_token(env_path)
        check_token_scope(token, scope=STATISTICS_SCOPE, category="Статистика")
        config = load_database_config(env_path)
        source = HistorySource(
            "заказов", "order_history_progress", ORDER_HISTORY_SCHEMAS, timedelta(hours=3),
            request_order_changes, store_order_statistics, describe_orders,
        )
        with psycopg.connect(**config, autocommit=True) as connection:
            load_paginated_history(connection, token, start_date, source, refresh_completed=refresh_completed)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать SQL-схему заказов.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Выгрузить историю заказов из статистики Wildberries.")
    parser.add_argument("--changed-since", required=True, type=date.fromisoformat, help="Дата в формате ГГГГ-ММ-ДД.")
    raise SystemExit(main(parser.parse_args().changed_since))
