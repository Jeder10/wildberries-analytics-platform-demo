"""Load Wildberries sales and returns for a Moscow calendar day."""

import argparse
from datetime import date
from pathlib import Path
import sys

import psycopg

from .app_paths import RESOURCE_ROOT
from .load_wb_cards import load_database_config
from .wb_cards import CardCheckError, ENV_PATH, load_token
from .wb_sales import parse_sales_timestamp, request_sales_day


SCHEMA_PATH = RESOURCE_ROOT / "sql" / "004_sales_operations.sql"
UPSERT_SALE = """
    INSERT INTO wb.sales_operations AS stored (
        sale_id, srid, nm_id, operation_at, source_updated_at,
        total_price, finished_price, price_with_disc, for_pay
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (sale_id) DO UPDATE SET
        srid = EXCLUDED.srid,
        nm_id = EXCLUDED.nm_id,
        operation_at = EXCLUDED.operation_at,
        source_updated_at = EXCLUDED.source_updated_at,
        total_price = EXCLUDED.total_price,
        finished_price = EXCLUDED.finished_price,
        price_with_disc = EXCLUDED.price_with_disc,
        for_pay = EXCLUDED.for_pay,
        loaded_at = CURRENT_TIMESTAMP
    WHERE EXCLUDED.source_updated_at > stored.source_updated_at
"""


def store_sales(connection: psycopg.Connection, operations: list[dict]) -> int:
    """Store validated operations in one transaction."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    # Matching key order helps concurrent loads lock overlapping rows in the same order.
    rows = [
        (
            operation["saleID"],
            operation["srid"],
            operation["nmId"],
            parse_sales_timestamp(operation["date"], "date"),
            parse_sales_timestamp(operation["lastChangeDate"], "lastChangeDate"),
            operation["totalPrice"],
            operation["finishedPrice"],
            operation["priceWithDisc"],
            operation["forPay"],
        )
        for operation in sorted(operations, key=lambda operation: operation["saleID"])
    ]

    with connection.transaction():
        connection.execute(schema, prepare=False)
        if not rows:
            return 0
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT_SALE, rows)
            return cursor.rowcount


def main(sales_day: date, env_path: Path = ENV_PATH) -> int:
    try:
        config = load_database_config(env_path)
        token = load_token(env_path)
        with psycopg.connect(**config) as connection:
            operations = request_sales_day(token, sales_day)
            changed = store_sales(connection, operations)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}. Проверьте подключение и права роли.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать файл схемы продаж.", file=sys.stderr)
        return 1

    print(f"Операций за {sales_day}: {len(operations)}. Вставлено или обновлено строк: {changed}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Загрузить продажи и возвраты за один день по Москве.")
    parser.add_argument("--date", required=True, type=date.fromisoformat, help="Дата в формате ГГГГ-ММ-ДД.")
    raise SystemExit(main(parser.parse_args().date))
