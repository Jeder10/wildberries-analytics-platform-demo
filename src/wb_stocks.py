"""Read the current product-level stock report from Wildberries."""

import argparse
from pathlib import Path
import sys

from .wb_cards import CardCheckError, ENV_PATH, check_token_scope, load_token, request_readonly_json
from .wb_order_feed import ANALYTICS_SCOPE, completed_moscow_period


REPORT_HOST = "seller-analytics-api.wildberries.ru"
REPORT_PATH = "/api/v2/stocks-report/products/products"
MAX_PAGE_SIZE = 1000
AVAILABILITY_OPTIONS = ("deficient", "actual", "balanced", "nonActual", "nonLiquid", "invalidData")


def request_product_stocks(
    token: str, nm_ids: list[int], *, limit: int = MAX_PAGE_SIZE, offset: int = 0
) -> dict[int, int]:
    if not nm_ids:
        return {}

    check_token_scope(token, scope=ANALYTICS_SCOPE, category="Аналитика")
    period = completed_moscow_period()
    payload = {
        "nmIDs": nm_ids,
        "currentPeriod": {field: value[:10] for field, value in period.items()},
        "stockType": "",
        "skipDeletedNm": True,
        "orderBy": {"field": "stockCount", "mode": "desc"},
        "availabilityFilters": list(AVAILABILITY_OPTIONS),
        "limit": limit,
        "offset": offset,
    }
    response = request_readonly_json(
        token, host=REPORT_HOST, path=REPORT_PATH, payload=payload, category="Аналитика"
    )
    data = response.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) > limit:
        raise CardCheckError(f"HTTP 200. Ожидался список не длиннее {limit} строк остатков.")

    requested = set(nm_ids)
    stocks = {}
    for item in items:
        if not isinstance(item, dict):
            raise CardCheckError("HTTP 200. Строка остатка должна быть объектом JSON.")
        nm_id = item.get("nmID")
        if type(nm_id) is not int or nm_id not in requested:
            raise CardCheckError("HTTP 200. Получен артикул вне фильтра остатков.")
        if nm_id in stocks:
            raise CardCheckError("HTTP 200. В отчёте по остаткам повторился артикул.")
        metrics = item.get("metrics")
        stock = metrics.get("stockCount") if isinstance(metrics, dict) else None
        if type(stock) is not int or not 0 <= stock < 2**64:
            raise CardCheckError("HTTP 200. stockCount должен быть целым неотрицательным числом в пределах uint64.")
        stocks[nm_id] = stock
    return stocks


def main(nm_id: int, env_path: Path = ENV_PATH) -> int:
    try:
        stocks = request_product_stocks(load_token(env_path), [nm_id], limit=1)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"HTTP 200. Строк отчёта по остаткам: {len(stocks)}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Проверить остаток одного артикула Wildberries.")
    parser.add_argument("--article", type=int, required=True, help="Артикул Wildberries.")
    raise SystemExit(main(parser.parse_args().article))
