"""Load Wildberries order statuses for seven completed Moscow days."""

from pathlib import Path
import sys
from time import sleep

import psycopg

from .app_paths import RESOURCE_ROOT
from .load_wb_cards import load_database_config
from .wb_cards import CardCheckError, ENV_PATH, read_token_claims
from .wb_order_feed import (
    MAX_ORDER_PAGE_SIZE,
    Order,
    completed_moscow_period,
    load_order_token,
    parse_order,
    parse_timestamp,
    request_order_page,
)


SCHEMA_PATH = RESOURCE_ROOT / "sql" / "003_order_feed.sql"
BASIC_TOKEN_TYPE = 1
BASIC_REQUEST_INTERVAL = 3 * 60 * 60
STANDARD_REQUEST_INTERVAL = 60
UPSERT_ORDER = """
    INSERT INTO wb.order_feed AS stored (
        srid, nm_id, chrt_id, created_at, status_updated_at, status
    )
    VALUES (%s, %s, %s, %s, %s, %s)
    ON CONFLICT (srid) DO UPDATE SET
        nm_id = EXCLUDED.nm_id,
        chrt_id = EXCLUDED.chrt_id,
        created_at = EXCLUDED.created_at,
        status_updated_at = EXCLUDED.status_updated_at,
        status = EXCLUDED.status,
        loaded_at = CURRENT_TIMESTAMP
    WHERE EXCLUDED.status_updated_at > stored.status_updated_at
"""


def collect_orders(token: str, token_key: str) -> list[Order]:
    period = completed_moscow_period()
    token_type = read_token_claims(token, token_key).get("acc")
    delay = BASIC_REQUEST_INTERVAL if token_type == BASIC_TOKEN_TYPE else STANDARD_REQUEST_INTERVAL
    snapshot_time = None
    offset = 0
    orders = {}

    while True:
        page = request_order_page(
            token,
            period,
            limit=MAX_ORDER_PAGE_SIZE,
            offset=offset,
            snapshot_time=snapshot_time,
            token_key=token_key,
        )
        count = len(page["orders"])
        if snapshot_time is not None:
            page_time = parse_timestamp(page.get("snapshotTime"), "snapshotTime")
            if page_time != parse_timestamp(snapshot_time, "snapshotTime"):
                raise CardCheckError("Снимок «Ленты заказов» изменился между страницами.")
        elif count == MAX_ORDER_PAGE_SIZE:
            snapshot_time = page.get("snapshotTime")
            parse_timestamp(snapshot_time, "snapshotTime")

        for item in page["orders"]:
            order = parse_order(item)
            if order.srid in orders:
                raise CardCheckError("В одной выборке «Ленты заказов» повторился srid.")
            orders[order.srid] = order

        if count < MAX_ORDER_PAGE_SIZE:
            return list(orders.values())
        offset += count
        print(f"Получено строк: {offset}. Следующая страница через {delay} с.", flush=True)
        sleep(delay)


def store_orders(connection: psycopg.Connection, orders: list[Order]) -> int:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    # Stable key order gives overlapping loads the same order when locking rows.
    rows = [
        (order.srid, order.nm_id, order.chrt_id, order.created_at, order.status_updated_at, order.status)
        for order in sorted(orders, key=lambda order: order.srid)
    ]
    with connection.transaction():
        connection.execute(schema, prepare=False)
        if not rows:
            return 0
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT_ORDER, rows)
            return cursor.rowcount


def main(env_path: Path = ENV_PATH) -> int:
    try:
        config = load_database_config(env_path)
        key, token = load_order_token(env_path)
        with psycopg.connect(**config) as connection:
            orders = collect_orders(token, key)
            changed = store_orders(connection, orders)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}. Проверьте подключение и права роли.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать файл схемы заказов.", file=sys.stderr)
        return 1

    print(f"Заказов подготовлено: {len(orders)}. Вставлено или обновлено строк: {changed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
