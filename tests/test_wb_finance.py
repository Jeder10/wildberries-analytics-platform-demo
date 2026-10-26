import base64
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from decimal import Decimal
from http.client import HTTPResponse
import io
import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from src import load_wb_finance, wb_cards, wb_finance


START = date(2026, 11, 9)
END = date(2026, 11, 15)
SCOPE = wb_finance.FINANCE_SCOPE | wb_cards.READ_ONLY_SCOPE
CLAIMS = base64.urlsafe_b64encode(json.dumps({"s": SCOPE, "acc": 1}).encode()).decode().rstrip("=")
TOKEN = f"synthetic.{CLAIMS}.signature"
SALE = {
    "rrdId": 100, "reportId": 41, "reportType": 1, "nmId": 501, "currency": "RUB",
    "docTypeName": "Продажа", "sellerOperName": "Продажа", "dateFrom": "2026-11-09",
    "dateTo": "2026-11-15", "rrDate": "2026-11-14", "retailAmount": "812.05", "forPay": "700.25",
}


class FinanceReportTests(unittest.TestCase):
    def setUp(self):
        self.connect = self.enterContext(patch.object(wb_cards, "HTTPSConnection", autospec=True))
        self.connection = self.connect.return_value
        self.response = Mock(spec=HTTPResponse, status=200)
        self.connection.getresponse.return_value = self.response

    def test_requests_selected_fields_and_moves_cursor_past_other_operations(self):
        self.response.read.return_value = json.dumps(
            [SALE, {"rrdId": 101, "sellerOperName": "Логистика"}]
        ).encode()

        result = wb_finance.request_finance_page(TOKEN, START, END, 99)

        self.assertEqual(result.last_rrd_id, 101)
        self.assertEqual(result.row_count, 2)
        self.assertEqual(len(result.operations), 1)
        self.assertEqual(result.operations[0][-3:], ("sale", Decimal("812.05"), Decimal("700.25")))
        request = self.connection.request.call_args
        self.assertEqual(request.args, ("POST", "/api/finance/v1/sales-reports/detailed"))
        self.assertEqual(json.loads(request.kwargs["body"]), {
            "dateFrom": START.isoformat(), "dateTo": END.isoformat(), "period": "weekly",
            "rrdId": 99, "limit": 100000, "fields": list(wb_finance.REPORT_FIELDS),
        })

    def test_finance_204_terminates_without_reading_body(self):
        self.response.status = 204
        self.assertIsNone(wb_finance.request_finance_page(TOKEN, START, END, 100))
        self.response.read.assert_not_called()

    def test_finance_429_keeps_the_server_retry_interval(self):
        self.response.status = 429
        self.response.getheader.return_value = "90"
        with self.assertRaises(wb_cards.RateLimitError) as error:
            wb_finance.request_finance_page(TOKEN, START, END, 100)
        self.assertEqual(error.exception.retry_after, 90)
        self.response.read.assert_not_called()

    def test_invalid_page_never_advances_cursor(self):
        for rows, message in (
            ([], "Ожидались строки"),
            ([SALE, SALE], "не продвигают"),
            ([{**SALE, "forPay": "NaN"}], "поле forPay"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_finance.parse_finance_page(rows, 99)

    def test_return_retains_the_source_amount_sign_for_the_sql_view(self):
        refund = {**SALE, "docTypeName": "Возврат", "sellerOperName": "Возврат", "retailAmount": "-10.25"}
        result = wb_finance.parse_finance_page([refund], 0)
        self.assertEqual(result.operations[0][-3:], ("return", Decimal("-10.25"), Decimal("700.25")))


class FinanceReportResumeTests(unittest.TestCase):
    def test_resumes_saved_cursor_and_records_completion_only_after_204(self):
        conn = MagicMock()
        ready = datetime(2026, 11, 23, 9, 15, tzinfo=timezone.utc)
        conn.execute.return_value.fetchone.side_effect = [(True,), (101, False, ready)]
        with (
            patch.object(load_wb_finance, "initialize_load"),
            patch.object(load_wb_finance, "wait_for_request") as wait,
            patch.object(load_wb_finance, "request_finance_page", return_value=None) as fetch,
            patch.object(load_wb_finance, "record_page") as save,
            redirect_stdout(io.StringIO()),
        ):
            load_wb_finance.load_reports(conn, TOKEN, START, END)

        wait.assert_called_once_with(ready)
        fetch.assert_called_once_with(TOKEN, START, END, 101)
        self.assertEqual(save.call_args.args[:4], (conn, START, END, None))


if __name__ == "__main__":
    unittest.main()
