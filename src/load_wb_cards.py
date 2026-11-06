"""Load the current Wildberries product catalog into PostgreSQL."""

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from time import sleep

import psycopg
from psycopg.types.json import Jsonb

from .app_paths import RESOURCE_ROOT
from .wb_cards import (
    CardCheckError,
    ENV_PATH,
    MAX_CARD_PAGE_SIZE,
    PAGE_DELAY_SECONDS,
    load_token,
    read_settings,
    request_card_sample,
)


SCHEMA_PATH = RESOURCE_ROOT / "sql" / "001_product_cards.sql"
DATABASE_KEYS = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")
LOCAL_DATABASE_HOSTS = ("::1", "127.0.0.1", "localhost")
UPSERT_CARD = """
    INSERT INTO wb.product_cards AS stored (
        nm_id, title, vendor_code, source_updated_at, payload
    )
    VALUES (%s, %s, %s, %s, %s)
    ON CONFLICT (nm_id) DO UPDATE SET
        title = EXCLUDED.title,
        vendor_code = EXCLUDED.vendor_code,
        source_updated_at = EXCLUDED.source_updated_at,
        payload = EXCLUDED.payload,
        loaded_at = CURRENT_TIMESTAMP
    WHERE EXCLUDED.source_updated_at > stored.source_updated_at
"""


@dataclass(frozen=True)
class ProductCard:
    nm_id: int
    title: str | None
    vendor_code: str | None
    source_updated_at: datetime
    payload: dict


def parse_card(payload: dict) -> ProductCard:
    nm_id = payload.get("nmID")
    if type(nm_id) is not int or not 0 < nm_id < 2**63:
        raise CardCheckError("Артикул nmID карточки должен быть положительным числом для bigint.")

    try:
        updated_at = datetime.fromisoformat(payload["updatedAt"])
    except (KeyError, TypeError, ValueError):
        raise CardCheckError("В карточке отсутствует корректная дата updatedAt.") from None
    if updated_at.utcoffset() is None:
        raise CardCheckError("В дате updatedAt карточки отсутствует часовой пояс.")

    title = payload.get("title")
    vendor_code = payload.get("vendorCode")
    for value in (title, vendor_code):
        if value is not None and not isinstance(value, str):
            raise CardCheckError("Название и артикул продавца в карточке должны быть строками.")

    return ProductCard(nm_id, title, vendor_code, updated_at, payload)


def collect_cards(token: str, *, sample: bool = False) -> list[ProductCard]:
    limit = 1 if sample else MAX_CARD_PAGE_SIZE
    after = None
    seen_cursors = set()
    newest_cards = {}

    while True:
        page = request_card_sample(token, after=after, limit=limit)
        for payload in page["cards"]:
            card = parse_card(payload)
            stored = newest_cards.get(card.nm_id)
            if stored is None or card.source_updated_at > stored.source_updated_at:
                newest_cards[card.nm_id] = card

        if page["cards"]:
            after = (page["cursor"]["updatedAt"], page["cursor"]["nmID"])
            if after in seen_cursors:
                raise CardCheckError("Wildberries повторил курсор. Загрузка остановлена.")
            seen_cursors.add(after)
        if sample or page["cursor"]["total"] < limit:
            return list(newest_cards.values())
        sleep(PAGE_DELAY_SECONDS)


def load_database_config(path: Path) -> dict[str, str | int]:
    settings = read_settings(path, *DATABASE_KEYS)
    missing = [key for key in DATABASE_KEYS if not settings.get(key)]
    if missing:
        raise CardCheckError(f"Заполните настройки PostgreSQL в .env. Отсутствуют {', '.join(missing)}.")
    if (
        settings["PGDATABASE"] == "wb_analytics_example"
        or settings["PGUSER"] == "wb_etl_example"
        or settings["PGPASSWORD"] == "replace_me"
    ):
        raise CardCheckError("В .env остались примеры настроек PostgreSQL. Укажите базу проекта и роль загрузки.")

    if settings["PGHOST"] not in LOCAL_DATABASE_HOSTS:
        raise CardCheckError("Укажите локальный адрес PostgreSQL в PGHOST.")
    try:
        port = int(settings["PGPORT"])
    except ValueError:
        raise CardCheckError("PGPORT должен быть номером порта.") from None
    if not 1 <= port <= 65535:
        raise CardCheckError("PGPORT должен быть номером порта от 1 до 65535.")

    return {
        "host": settings["PGHOST"],
        "port": port,
        "dbname": settings["PGDATABASE"],
        "user": settings["PGUSER"],
        "password": settings["PGPASSWORD"],
        "connect_timeout": 5,
    }


def store_cards(connection: psycopg.Connection, cards: list[ProductCard]) -> int:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    # A stable order gives overlapping loads the same order when locking rows.
    rows = [
        (card.nm_id, card.title, card.vendor_code, card.source_updated_at, Jsonb(card.payload))
        for card in sorted(cards, key=lambda card: card.nm_id)
    ]
    with connection.transaction():
        connection.execute(schema, prepare=False)
        if not rows:
            return 0
        with connection.cursor() as cursor:
            cursor.executemany(UPSERT_CARD, rows)
            return cursor.rowcount


def main(env_path: Path = ENV_PATH, *, sample: bool = False) -> int:
    try:
        config = load_database_config(env_path)
        token = load_token(env_path)
        with psycopg.connect(**config) as connection:
            cards = collect_cards(token, sample=sample)
            changed = store_cards(connection, cards)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except psycopg.Error as error:
        code = f" (SQLSTATE {error.sqlstate})" if error.sqlstate else ""
        print(f"Ошибка PostgreSQL{code}. Проверьте подключение и права роли.", file=sys.stderr)
        return 1
    except OSError:
        print("Не удалось прочитать файл схемы PostgreSQL.", file=sys.stderr)
        return 1

    print(f"Карточек подготовлено: {len(cards)}. Вставлено или обновлено строк: {changed}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Локальная загрузка карточек Wildberries в PostgreSQL.")
    parser.add_argument("--sample", action="store_true", help="Загрузить максимум одну карточку.")
    arguments = parser.parse_args()
    raise SystemExit(main(sample=arguments.sample))
