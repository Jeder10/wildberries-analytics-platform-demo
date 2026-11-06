"""Import the Wildberries daily stock report archive into PostgreSQL."""

import argparse
from collections import defaultdict
import csv
from datetime import date, datetime
from io import TextIOWrapper
from pathlib import Path
import re
import sys
from zipfile import BadZipFile, ZipFile

import psycopg

from .load_wb_cards import load_database_config
from .load_wb_stocks import SCHEMA_PATH
from .wb_cards import CardCheckError, ENV_PATH


MAX_SIGNED = 2**63 - 1
KEY_FIELDS = ("NmID", "ChrtID", "OfficeName")
UPSERT_HISTORY = """
    INSERT INTO wb.stock_daily_history AS stored (day, nm_id, quantity)
    VALUES (%s, %s, %s)
    ON CONFLICT (day, nm_id) DO UPDATE SET quantity = EXCLUDED.quantity
    WHERE stored.quantity IS DISTINCT FROM EXCLUDED.quantity
"""


def stock_history_rows(archive: Path) -> list[tuple[date, int, int]]:
    with ZipFile(archive) as report:
        files = [file for file in report.infolist() if not file.is_dir()]
        if len(files) != 1 or not files[0].filename.lower().endswith(".csv"):
            raise CardCheckError("В ZIP-отчёте ожидается один CSV-файл остатков.")
        with report.open(files[0]) as item, TextIOWrapper(item, encoding="utf-8-sig", newline="") as source:
            return parse_history(csv.DictReader(source))


def parse_history(reader: csv.DictReader) -> list[tuple[date, int, int]]:
    headers = reader.fieldnames or []
    if not set(KEY_FIELDS) <= set(headers) or len(headers) != len(set(headers)):
        raise CardCheckError("В CSV отсутствуют ключи остатков или повторяются заголовки.")
    days = {
        label: datetime.strptime(label, "%d.%m.%Y").date()
        for label in headers
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", label)
    }
    if not days:
        raise CardCheckError("В CSV нет столбцов с датами остатков.")

    totals: dict[tuple[date, int], int] = defaultdict(int)
    seen = set()
    for row in reader:
        if None in row or any(row[column] is None for column in headers):
            raise CardCheckError("Количество полей в строке CSV отличается от заголовка.")
        article, size, office = (row[field].strip() for field in KEY_FIELDS)
        if not article.isascii() or not article.isdecimal() or not 0 < int(article) <= MAX_SIGNED or not size or not office:
            raise CardCheckError("В CSV не заполнены ключи артикула, размера и склада.")
        nm_id = int(article)
        key = nm_id, size, office
        if key in seen:
            raise CardCheckError("В CSV повторяется сочетание артикула, размера и склада.")
        seen.add(key)
        for column, day in days.items():
            value = row[column].strip()
            if not value:
                continue
            if not value.isascii() or not value.isdecimal():
                raise CardCheckError("В CSV найден некорректный остаток.")
            totals[day, nm_id] += int(value)
            if totals[day, nm_id] > MAX_SIGNED:
                raise CardCheckError("Суммарный остаток выходит за пределы целого числа в отчёте.")

    return [(day, nm_id, quantity) for (day, nm_id), quantity in sorted(totals.items())]


def main(archive: Path, env_path: Path = ENV_PATH) -> int:
    try:
        observations = stock_history_rows(archive)
        with psycopg.connect(**load_database_config(env_path)) as connection:
            connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"), prepare=False)
            with connection.cursor() as cursor:
                cursor.executemany(UPSERT_HISTORY, observations)
                changed = cursor.rowcount
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    except (BadZipFile, OSError, ValueError, csv.Error):
        print("Не удалось прочитать ZIP-отчёт с историей остатков.", file=sys.stderr)
        return 1
    except psycopg.Error as error:
        print(f"Ошибка PostgreSQL. SQLSTATE {error.sqlstate or 'не указан'}.", file=sys.stderr)
        return 1

    print(f"Дневных записей остатков прочитано {len(observations)}, записано или обновлено {changed}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Загрузить историю остатков из ZIP-отчёта Wildberries.")
    parser.add_argument("archive", type=Path)
    raise SystemExit(main(parser.parse_args().archive))
