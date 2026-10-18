import base64
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal
from http.client import HTTPResponse
import io
import json
import unittest
from unittest.mock import Mock, patch

from src import wb_cards, wb_sales


def synthetic_token(scope: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"s": scope}).encode()).decode().rstrip("=")
    return f"synthetic.{payload}.signature"


REPORT_DAY = date(2026, 11, 14)
READ_TOKEN = synthetic_token(wb_sales.STATISTICS_SCOPE | wb_cards.READ_ONLY_SCOPE)
SALE = {
    "saleID": "S00001",
    "srid": "synthetic-order",
    "nmId": 700001,
    "date": "2026-11-14T01:00:00",
    "lastChangeDate": "2026-11-14T02:00:00",
    "totalPrice": 1000,
    "finishedPrice": 812.05,
    "priceWithDisc": 900,
    "forPay": 0,
}


class SalesCheckTests(unittest.TestCase):
    def setUp(self):
        self.connect = self.enterContext(patch.object(wb_cards, "HTTPSConnection", autospec=True))
        self.connection = self.connect.return_value
        self.response = Mock(spec=HTTPResponse, status=200)
        self.connection.getresponse.return_value = self.response

    def respond(self, payload):
        self.response.read.return_value = json.dumps(payload).encode()

    def test_uses_get_for_one_day_and_parses_fractional_money_exactly(self):
        self.respond([SALE])

        rows = wb_sales.request_sales_day(READ_TOKEN, REPORT_DAY)

        self.assertEqual(rows[0]["finishedPrice"], Decimal("812.05"))
        self.connect.assert_called_once_with("statistics-api.wildberries.ru", timeout=10)
        self.connection.request.assert_called_once_with(
            "GET", "/api/v1/supplier/sales?dateFrom=2026-11-14&flag=1",
            body=None, headers={"Authorization": READ_TOKEN},
        )

    def test_accepts_empty_day(self):
        self.respond([])
        self.assertEqual(wb_sales.request_sales_day(READ_TOKEN, REPORT_DAY), [])

    def test_changes_preserve_the_documented_cursor_and_original_operation_date(self):
        cursor = "2026-11-14T02:00:00.123456789+03:00"
        operation = {
            **SALE,
            "date": "2026-11-12T01:00:00",
            "lastChangeDate": "2026-11-14T03:00:00.123456789",
        }
        self.respond([operation])

        rows = wb_sales.request_sales_changes(READ_TOKEN, cursor)

        self.assertEqual(rows[0]["lastChangeDate"], operation["lastChangeDate"])
        self.assertEqual(rows[0]["date"], operation["date"])
        self.connection.request.assert_called_once_with(
            "GET", "/api/v1/supplier/sales?dateFrom=2026-11-14T02%3A00%3A00.123456789%2B03%3A00&flag=0",
            body=None, headers={"Authorization": READ_TOKEN},
        )

    def test_changes_cli_labels_one_page_and_reports_only_counts(self):
        self.respond([{**SALE, "saleID": "R00002"}])
        stdout = io.StringIO()

        with patch.object(wb_sales, "load_token", return_value=READ_TOKEN), redirect_stdout(stdout):
            self.assertEqual(wb_sales.main(REPORT_DAY, changes=True), 0)

        self.assertEqual(
            stdout.getvalue(),
            "HTTP 200. Первая страница изменений. Операций: 1. Продаж: 0. Возвратов: 1.\n",
        )

    def test_accepts_return_and_reports_counts_without_order_or_price_details(self):
        self.respond([
            SALE,
            {**SALE, "saleID": "R00002", "date": "2026-11-13T22:00:00+00:00", "finishedPrice": -812.05},
        ])
        stdout = io.StringIO()

        with patch.object(wb_sales, "load_token", return_value=READ_TOKEN), redirect_stdout(stdout):
            self.assertEqual(wb_sales.main(REPORT_DAY), 0)

        self.assertEqual(stdout.getvalue(), "HTTP 200. Операций: 2. Продаж: 1. Возвратов: 1.\n")

    def test_requires_statistics_and_read_only_scope_before_connecting(self):
        for scope in (wb_sales.STATISTICS_SCOPE, wb_cards.CONTENT_SCOPE | wb_cards.READ_ONLY_SCOPE):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(wb_cards.CardCheckError, "Статистика.*Только чтение"):
                    wb_sales.request_sales_day(synthetic_token(scope), REPORT_DAY)
        self.connect.assert_not_called()

    def test_rejects_duplicate_operations_and_wrong_day(self):
        cases = (
            ([SALE, SALE], "повторился saleID"),
            ([{**SALE, "date": "2026-11-13T01:00:00"}], "датой запроса"),
        )
        for rows, error in cases:
            with self.subTest(error=error):
                self.respond(rows)
                with self.assertRaisesRegex(wb_cards.CardCheckError, error):
                    wb_sales.request_sales_day(READ_TOKEN, REPORT_DAY)

    def test_rejects_non_list_and_invalid_money(self):
        for body, error in (({}, "список продаж"), ([{**SALE, "forPay": True}], "поле forPay")):
            with self.subTest(error=error):
                self.respond(body)
                with self.assertRaisesRegex(wb_cards.CardCheckError, error):
                    wb_sales.request_sales_day(READ_TOKEN, REPORT_DAY)

    def test_rate_limit_is_reported_without_retrying(self):
        self.response.status = 429
        self.response.getheader.return_value = "120"

        with self.assertRaisesRegex(wb_cards.CardCheckError, "HTTP 429.*120 с"):
            wb_sales.request_sales_day(READ_TOKEN, REPORT_DAY)

        self.connection.request.assert_called_once()
        self.response.read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
