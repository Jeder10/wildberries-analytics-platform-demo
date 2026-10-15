"""Read and validate pages from the Wildberries order feed."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import sys

from .wb_cards import CardCheckError, ENV_PATH, check_token_scope, read_settings, request_readonly_json


ORDER_FEED_HOST = "seller-analytics-api.wildberries.ru"
ORDER_FEED_PATH = "/api/analytics/v1/order-feed"
ORDER_TOKEN_KEY = "WB_ANALYTICS_TOKEN"
SHARED_TOKEN_KEY = "WB_API_TOKEN"
ANALYTICS_SCOPE = 1 << 2
MOSCOW_TIME = timezone(timedelta(hours=3))
SAMPLE_DAYS = 7
SAMPLE_LIMIT = 1
MAX_ORDER_PAGE_SIZE = 10_000
ORDER_STATUSES = ("created", "buyout", "cancel", "return", "returnDefective")


@dataclass(frozen=True)
class Order:
    srid: str
    nm_id: int
    chrt_id: int
    created_at: datetime
    status_updated_at: datetime
    status: str


def parse_timestamp(value: object, key: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.utcoffset() is None:
        raise CardCheckError(f"HTTP 200. Поле {key} должно содержать дату и время с часовым поясом.")
    return parsed


def parse_order(value: object) -> Order:
    if not isinstance(value, dict):
        raise CardCheckError("HTTP 200. Строка заказа должна быть объектом JSON.")
    srid = value.get("srid")
    if not isinstance(srid, str) or not srid.strip():
        raise CardCheckError("HTTP 200. В строке заказа отсутствует идентификатор srid.")
    nm_id, chrt_id = value.get("nmId"), value.get("chrtId")
    if type(nm_id) is not int or type(chrt_id) is not int:
        raise CardCheckError("HTTP 200. В строке заказа ожидаются числовые nmId и chrtId.")
    if not -(2**63) <= nm_id < 2**63 or not 0 <= chrt_id < 2**64:
        raise CardCheckError("HTTP 200. nmId или chrtId выходит за документированный диапазон.")
    status = value.get("status")
    if status not in ORDER_STATUSES:
        raise CardCheckError("HTTP 200. В строке заказа получен неизвестный статус.")
    return Order(
        srid=srid,
        nm_id=nm_id,
        chrt_id=chrt_id,
        created_at=parse_timestamp(value.get("createdAt"), "createdAt"),
        status_updated_at=parse_timestamp(value.get("updatedAt"), "updatedAt"),
        status=status,
    )


def load_order_token(path: Path) -> tuple[str, str]:
    settings = read_settings(path, ORDER_TOKEN_KEY, SHARED_TOKEN_KEY)
    for key in (ORDER_TOKEN_KEY, SHARED_TOKEN_KEY):
        token = settings.get(key)
        if token:
            return key, token
    raise CardCheckError(f"Задайте {ORDER_TOKEN_KEY} или {SHARED_TOKEN_KEY} в локальном .env.")


def completed_moscow_period(today: date | None = None) -> dict[str, str]:
    if today is None:
        today = datetime.now(MOSCOW_TIME).date()
    start = datetime.combine(today - timedelta(days=SAMPLE_DAYS), time.min, MOSCOW_TIME)
    end = datetime.combine(today - timedelta(days=1), time.max, MOSCOW_TIME)
    return {"start": start.isoformat(), "end": end.isoformat()}


def request_order_page(
    token: str,
    period: dict[str, str],
    *,
    limit: int,
    offset: int = 0,
    snapshot_time: str | None = None,
    token_key: str = ORDER_TOKEN_KEY,
) -> dict:
    check_token_scope(token, scope=ANALYTICS_SCOPE, category="Аналитика", key=token_key)
    pagination: dict[str, int | str] = {"limit": limit, "offset": offset}
    if snapshot_time is not None:
        pagination["snapshotTime"] = snapshot_time
    result = request_readonly_json(
        token,
        host=ORDER_FEED_HOST,
        path=ORDER_FEED_PATH,
        category="Аналитика",
        payload={"selectedPeriod": period, "pagination": pagination},
    )
    data = result.get("data")
    if not isinstance(data, dict):
        raise CardCheckError("HTTP 200. В ответе «Ленты заказов» отсутствует объект data.")
    orders = data.get("orders")
    if not isinstance(orders, list) or len(orders) > limit:
        raise CardCheckError(f"HTTP 200. Ожидался список максимум на {limit} записей.")
    return data


def request_order_sample(
    token: str, *, today: date | None = None, token_key: str = ORDER_TOKEN_KEY
) -> dict:
    data = request_order_page(
        token, completed_moscow_period(today), limit=SAMPLE_LIMIT, token_key=token_key
    )
    orders = data["orders"]
    for order in orders:
        parse_order(order)
    return data


def main(env_path: Path = ENV_PATH) -> int:
    try:
        key, token = load_order_token(env_path)
        data = request_order_sample(token, token_key=key)
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"HTTP 200. Строк «Ленты заказов» в ответе: {len(data['orders'])}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
