"""Read and classify weekly Wildberries financial report lines."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from .wb_cards import CardCheckError, NO_CONTENT, check_token_scope, fetch_readonly_json


FINANCE_SCOPE = 1 << 13
REPORT_HOST = "finance-api.wildberries.ru"
REPORT_PATH = "/api/finance/v1/sales-reports/detailed"
REPORT_TIMEOUT = 60
REPORT_FIELDS = (
    "reportId", "reportType", "dateFrom", "dateTo", "rrdId", "nmId", "currency",
    "docTypeName", "sellerOperName", "rrDate", "retailAmount", "forPay", "orderUid",
)
ORDINARY_OPERATIONS = {"Продажа": "sale", "Возврат": "return"}


@dataclass(frozen=True)
class FinancePage:
    last_rrd_id: int
    row_count: int
    operations: list[tuple]


def parse_amount(value: object, field: str) -> Decimal:
    if isinstance(value, Decimal) or type(value) in (str, int):
        try:
            amount = Decimal(value)
            if amount.is_finite():
                return amount
        except InvalidOperation:
            pass
    raise CardCheckError(f"В финансовом отчёте поле {field} не содержит денежное число.")


def parse_report_date(row: dict, field: str) -> date:
    try:
        return date.fromisoformat(row[field])
    except (KeyError, TypeError, ValueError):
        raise CardCheckError(f"В финансовом отчёте некорректна дата {field}.") from None


def parse_finance_page(rows: object, cursor: int) -> FinancePage:
    if not isinstance(rows, list) or not rows:
        raise CardCheckError("HTTP 200. Ожидались строки финансового отчёта и курсор.")

    operations = []
    for row in rows:
        rrd_id = row.get("rrdId") if isinstance(row, dict) else None
        if type(rrd_id) is not int or not cursor < rrd_id < 2**63:
            raise CardCheckError("Строки финансового отчёта не продвигают rrdId.")
        cursor = rrd_id
        reason = row.get("sellerOperName")
        if not isinstance(reason, str):
            raise CardCheckError("В финансовом отчёте отсутствует sellerOperName.")
        operation = ORDINARY_OPERATIONS.get(reason)
        if operation is None:
            continue

        report_id, report_type, nm_id = (row.get(field) for field in ("reportId", "reportType", "nmId"))
        currency = row.get("currency")
        order_uid = row.get("orderUid")
        if (
            row.get("docTypeName") != reason
            or any(type(value) is not int for value in (report_id, report_type, nm_id))
            or not isinstance(currency, str)
            or not currency.strip()
        ):
            raise CardCheckError("В продаже или возврате не заполнены ключи, тип или валюта отчёта.")
        if order_uid is not None and not isinstance(order_uid, str):
            raise CardCheckError("В финансовом отчёте некорректен идентификатор корзины orderUid.")
        operations.append((
            rrd_id, report_id, report_type, parse_report_date(row, "dateFrom"),
            parse_report_date(row, "dateTo"), parse_report_date(row, "rrDate"),
            nm_id, currency, operation, parse_amount(row.get("retailAmount"), "retailAmount"),
            parse_amount(row.get("forPay"), "forPay"), (order_uid or "").strip() or None,
        ))
    return FinancePage(cursor, len(rows), operations)


def request_finance_page(
    token: str, start_date: date, end_date: date, cursor: int
) -> FinancePage | None:
    check_token_scope(token, scope=FINANCE_SCOPE, category="Финансы")
    response = fetch_readonly_json(
        token, method="POST", host=REPORT_HOST, path=REPORT_PATH, category="Финансы",
        payload={
            "dateFrom": start_date.isoformat(),
            "dateTo": end_date.isoformat(),
            "period": "weekly",
            "limit": 100_000,
            "rrdId": cursor,
            "fields": list(REPORT_FIELDS),
        },
        parse_float=Decimal,
        allow_no_content=True,
        timeout=REPORT_TIMEOUT,
    )
    if response is NO_CONTENT:
        return None
    return parse_finance_page(response, cursor)
