"""Check Wildberries sales and returns for one Moscow calendar day."""

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys
from urllib.parse import urlencode

from .wb_cards import CardCheckError, ENV_PATH, check_token_scope, fetch_readonly_json, load_token


SALES_HOST = "statistics-api.wildberries.ru"
SALES_PATH = "/api/v1/supplier/sales"
STATISTICS_SCOPE = 1 << 5
MOSCOW_TIME = timezone(timedelta(hours=3))
MONEY_FIELDS = ("totalPrice", "finishedPrice", "priceWithDisc", "forPay")


def parse_sales_timestamp(value: object, field: str) -> datetime:
    try:
        if isinstance(value, str) and len(value) > 10:
            timestamp = datetime.fromisoformat(value)
            if timestamp.tzinfo is None:
                return timestamp.replace(tzinfo=MOSCOW_TIME)
            return timestamp.astimezone(MOSCOW_TIME)
    except ValueError:
        pass
    raise CardCheckError(f"HTTP 200. Некорректная дата и время в поле {field}.")


def validate_sale(row: object, sales_day: date | None) -> str:
    if not isinstance(row, dict):
        raise CardCheckError("HTTP 200. Операция продажи или возврата должна быть объектом JSON.")

    sale_id, srid, nm_id = row.get("saleID"), row.get("srid"), row.get("nmId")
    if not isinstance(sale_id, str) or len(sale_id) < 2 or sale_id[0] not in "SR":
        raise CardCheckError("HTTP 200. Некорректный идентификатор saleID.")
    if not isinstance(srid, str) or not srid.strip() or type(nm_id) is not int:
        raise CardCheckError("HTTP 200. Некорректный идентификатор заказа или артикул.")
    operation_at = parse_sales_timestamp(row.get("date"), "date")
    if sales_day is not None and operation_at.date() != sales_day:
        raise CardCheckError("HTTP 200. Дата операции не совпадает с датой запроса по Москве.")
    parse_sales_timestamp(row.get("lastChangeDate"), "lastChangeDate")

    for field in MONEY_FIELDS:
        amount = row.get(field)
        if type(amount) is not int and not (isinstance(amount, Decimal) and amount.is_finite()):
            raise CardCheckError(f"HTTP 200. В поле {field} ожидается число.")
    return sale_id


def _fetch_sales(
    token: str, params: dict[str, str | int], *, sales_day: date | None = None
) -> list[dict]:
    check_token_scope(token, scope=STATISTICS_SCOPE, category="Статистика")
    rows = fetch_readonly_json(
        token,
        method="GET",
        host=SALES_HOST,
        path=f"{SALES_PATH}?{urlencode(params)}",
        category="Статистика",
        parse_float=Decimal,
    )
    if not isinstance(rows, list):
        raise CardCheckError("HTTP 200. Ожидался список продаж и возвратов.")
    sale_ids = {validate_sale(row, sales_day) for row in rows}
    if len(sale_ids) != len(rows):
        raise CardCheckError("HTTP 200. В отчёте повторился saleID.")
    return rows


def request_sales_day(token: str, sales_day: date) -> list[dict]:
    return _fetch_sales(
        token, {"dateFrom": sales_day.isoformat(), "flag": 1}, sales_day=sales_day
    )


def request_sales_changes(token: str, date_from: str) -> list[dict]:
    """Read one page of changes, preserving the source timestamps for pagination."""
    return _fetch_sales(token, {"dateFrom": date_from, "flag": 0})


def main(sales_day: date, env_path: Path = ENV_PATH, *, changes: bool = False) -> int:
    try:
        token = load_token(env_path)
        if changes:
            rows = request_sales_changes(token, sales_day.isoformat())
        else:
            rows = request_sales_day(token, sales_day)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1

    sales = sum(row["saleID"].startswith("S") for row in rows)
    prefix = "HTTP 200. Первая страница изменений." if changes else "HTTP 200."
    print(f"{prefix} Операций: {len(rows)}. Продаж: {sales}. Возвратов: {len(rows) - sales}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Проверить продажи и возвраты Wildberries.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--date", type=date.fromisoformat, help="День операций по Москве, ГГГГ-ММ-ДД.")
    mode.add_argument("--changed-since", type=date.fromisoformat, help="Первая страница изменений с указанной даты.")
    args = parser.parse_args()
    if args.changed_since is not None:
        raise SystemExit(main(args.changed_since, changes=True))
    raise SystemExit(main(args.date))
