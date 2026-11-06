"""Store a snapshot of the current Wildberries stock by catalog article."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import UUID, uuid4

import psycopg

from .app_paths import RESOURCE_ROOT
from .load_wb_cards import load_database_config
from .load_wb_sales_history import wait_for_request
from .wb_cards import CardCheckError, ENV_PATH, RateLimitError, load_token, read_token_claims
from .wb_stocks import MAX_PAGE_SIZE, request_product_stocks


SCHEMA_PATH = RESOURCE_ROOT / "sql" / "006_stock_snapshots.sql"
BASIC_INTERVAL = timedelta(minutes=30)
STANDARD_INTERVAL = timedelta(seconds=20)
INSERT_STOCK = """
    INSERT INTO wb.stock_snapshots (snapshot_id, nm_id, captured_at, quantity)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (snapshot_id, nm_id) DO NOTHING
"""


@dataclass(frozen=True)
class StockObservation:
    nm_id: int
    captured_at: datetime
    quantity: int | None


def collect_stock_snapshot(connection: psycopg.Connection, token: str) -> list[StockObservation]:
    nm_ids = [nm_id for (nm_id,) in connection.execute(
        "SELECT nm_id FROM analytics.product_catalog ORDER BY nm_id"
    )]
    if not nm_ids or len(nm_ids) != len(set(nm_ids)):
        raise CardCheckError("Для снимка остатков нужен непустой каталог с уникальным nm_id.")

    account_type = read_token_claims(token, "WB_API_TOKEN").get("acc")
    interval = STANDARD_INTERVAL if account_type in (2, 3) else BASIC_INTERVAL
    observations = []
    for start in range(0, len(nm_ids), MAX_PAGE_SIZE):
        batch = nm_ids[start:start + MAX_PAGE_SIZE]
        if observations:
            wait_for_request(observations[-1].captured_at + interval)

        try:
            stocks = request_product_stocks(token, batch, limit=len(batch))
        except RateLimitError as error:
            pause = interval if error.retry_after is None else timedelta(seconds=error.retry_after + 1)
            print("Wildberries ответил HTTP 429. Загрузчик повторит запрос после паузы.", flush=True)
            wait_for_request(datetime.now(timezone.utc) + pause)
            stocks = request_product_stocks(token, batch, limit=len(batch))

        captured_at = datetime.now(timezone.utc)
        observations.extend(
            StockObservation(nm_id, captured_at, stocks.get(nm_id)) for nm_id in batch
        )
    return observations


def store_stock_snapshot(
    connection: psycopg.Connection, snapshot_id: UUID, observations: list[StockObservation]
) -> int:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    rows = [
        (snapshot_id, item.nm_id, item.captured_at, item.quantity)
        for item in observations
    ]
    with connection.transaction():
        connection.execute(schema, prepare=False)
        if not rows:
            return 0
        with connection.cursor() as cursor:
            cursor.executemany(INSERT_STOCK, rows)
            return cursor.rowcount


def main(env_path: Path = ENV_PATH, *, not_before: datetime | None = None) -> int:
    try:
        if not_before is not None:
            wait_for_request(not_before)
        config = load_database_config(env_path)
        token = load_token(env_path)
        snapshot_id = uuid4()
        with psycopg.connect(**config, autocommit=True) as connection:
            observations = collect_stock_snapshot(connection, token)
            inserted = store_stock_snapshot(connection, snapshot_id, observations)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать SQL-схему остатков.", file=sys.stderr)
        return 1

    found = sum(item.quantity is not None for item in observations)
    print(
        f"Снимок {snapshot_id}. Строк сохранено: {inserted}. "
        f"Артикулов в каталоге: {len(observations)}. С количеством: {found}. "
        f"Без строки в ответе: {len(observations) - found}.",
        flush=True,
    )
    return 0


def parse_not_before(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Укажите дату и время в формате ISO 8601.") from None
    if timestamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("Укажите часовой пояс.")
    return timestamp


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Сохранить снимок остатков по каталогу Wildberries.")
    parser.add_argument(
        "--not-before", type=parse_not_before, help="Начать запрос после указанной даты и времени."
    )
    raise SystemExit(main(not_before=parser.parse_args().not_before))
