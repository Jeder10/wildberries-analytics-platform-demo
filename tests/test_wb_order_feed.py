import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from http.client import HTTPResponse
import io
import json
import unittest
from unittest.mock import Mock, patch

from src import wb_cards, wb_order_feed


def synthetic_token(scope: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"s": scope}).encode()).decode().rstrip("=")
    return f"synthetic.{payload}.signature"


TODAY = date(2026, 11, 21)
READ_TOKEN = synthetic_token(wb_order_feed.ANALYTICS_SCOPE | wb_cards.READ_ONLY_SCOPE)
SHARED_READ_TOKEN = synthetic_token(
    wb_order_feed.ANALYTICS_SCOPE | wb_cards.CONTENT_SCOPE | wb_cards.READ_ONLY_SCOPE
)
SAMPLE_ORDER = {
    "srid": "synthetic-order-1",
    "nmId": 700001,
    "chrtId": 800001,
    "createdAt": "2026-11-14T16:00:00+03:00",
    "updatedAt": "2026-11-19T12:00:00+03:00",
    "status": "buyout",
    "destinationCity": "Synthetic city",
}
SNAPSHOT = "2026-11-21T10:00:00Z"


class OrderFeedTests(unittest.TestCase):
    def setUp(self):
        self.connect = self.enterContext(patch.object(wb_cards, "HTTPSConnection", autospec=True))
        self.connection = self.connect.return_value
        self.response = Mock(spec=HTTPResponse, status=200)
        self.connection.getresponse.return_value = self.response

    def respond(self, payload):
        self.response.read.return_value = json.dumps(payload).encode()

    def test_requests_one_order_from_the_last_seven_completed_moscow_days(self):
        data = {"snapshotTime": SNAPSHOT, "orders": [SAMPLE_ORDER]}
        self.respond({"data": data})

        self.assertEqual(wb_order_feed.request_order_sample(READ_TOKEN, today=TODAY), data)

        self.connect.assert_called_once_with("seller-analytics-api.wildberries.ru", timeout=10)
        self.connection.request.assert_called_once()
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("POST", "/api/analytics/v1/order-feed"))
        self.assertEqual(kwargs["headers"]["Authorization"], READ_TOKEN)
        self.assertEqual(json.loads(kwargs["body"]), {
            "selectedPeriod": {
                "start": "2026-11-14T00:00:00+03:00",
                "end": "2026-11-20T23:59:59.999999+03:00",
            },
            "pagination": {"limit": 1, "offset": 0},
        })

    def test_accepts_empty_orders(self):
        data = {"snapshotTime": SNAPSHOT, "orders": []}
        self.respond({"data": data})

        self.assertEqual(wb_order_feed.request_order_sample(READ_TOKEN, today=TODAY), data)

    def test_wrong_category_or_write_scope_stops_before_connection(self):
        for scope in (
            wb_cards.CONTENT_SCOPE | wb_cards.READ_ONLY_SCOPE,
            wb_order_feed.ANALYTICS_SCOPE,
        ):
            with self.subTest(scope=scope):
                with self.assertRaisesRegex(wb_cards.CardCheckError, "Аналитика.*Только чтение"):
                    wb_order_feed.request_order_sample(synthetic_token(scope), today=TODAY)
        self.connect.assert_not_called()

    def test_requires_response_data_and_single_row_limit(self):
        for response, message in (
            ({}, "объект data"),
            ({"data": {"snapshotTime": SNAPSHOT}}, "список максимум"),
            ({"data": {"snapshotTime": SNAPSHOT, "orders": [SAMPLE_ORDER, SAMPLE_ORDER]}}, "список максимум"),
        ):
            with self.subTest(message=message):
                self.respond(response)
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_order_feed.request_order_sample(READ_TOKEN, today=TODAY)

    def test_sample_can_read_order_without_snapshot(self):
        data = {"orders": [SAMPLE_ORDER]}
        self.respond({"data": data})

        self.assertEqual(wb_order_feed.request_order_sample(READ_TOKEN, today=TODAY), data)

    def test_rejects_invalid_order_fields(self):
        for patch_values, message in (
            ({"srid": ""}, "srid"),
            ({"nmId": True}, "nmId и chrtId"),
            ({"chrtId": "123"}, "nmId и chrtId"),
            ({"status": "synthetic-unknown-status"}, "неизвестный статус"),
            ({"createdAt": "2026-11-14T16:00:00"}, "createdAt"),
            ({"updatedAt": None}, "updatedAt"),
        ):
            with self.subTest(message=message):
                self.respond({"data": {"snapshotTime": SNAPSHOT, "orders": [{**SAMPLE_ORDER, **patch_values}]}})
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_order_feed.request_order_sample(READ_TOKEN, today=TODAY)

    def test_rate_limit_and_forbidden_do_not_retry_or_read_response_body(self):
        for status, message in ((403, "HTTP 403.*Аналитика"), (429, "HTTP 429.*60 с")):
            with self.subTest(status=status):
                self.connection.request.reset_mock()
                self.response.status = status
                self.response.getheader.return_value = "60"
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_order_feed.request_order_sample(READ_TOKEN, today=TODAY)
                self.connection.request.assert_called_once()
        self.response.read.assert_not_called()

    def test_missing_analytics_settings_make_no_request(self):
        stderr = io.StringIO()
        with patch.object(wb_order_feed, "read_settings", return_value={}), redirect_stderr(stderr):
            self.assertEqual(wb_order_feed.main(), 1)
        self.assertIn("WB_ANALYTICS_TOKEN", stderr.getvalue())
        self.connect.assert_not_called()

    def test_cli_prints_only_http_status_and_number_of_orders(self):
        self.respond({"data": {"snapshotTime": SNAPSHOT, "orders": [SAMPLE_ORDER]}})
        stdout = io.StringIO()
        settings = {"WB_ANALYTICS_TOKEN": READ_TOKEN, "WB_API_TOKEN": SHARED_READ_TOKEN}
        with patch.object(wb_order_feed, "read_settings", return_value=settings) as load, redirect_stdout(stdout):
            self.assertEqual(wb_order_feed.main(), 0)
        load.assert_called_once_with(wb_order_feed.ENV_PATH, "WB_ANALYTICS_TOKEN", "WB_API_TOKEN")
        self.assertEqual(self.connection.request.call_args.kwargs["headers"]["Authorization"], READ_TOKEN)
        self.assertEqual(stdout.getvalue(), "HTTP 200. Строк «Ленты заказов» в ответе: 1.\n")

    def test_cli_reuses_shared_read_only_analytics_token(self):
        self.respond({"data": {"snapshotTime": SNAPSHOT, "orders": []}})
        settings = {"WB_API_TOKEN": SHARED_READ_TOKEN}
        with patch.object(wb_order_feed, "read_settings", return_value=settings), redirect_stdout(io.StringIO()):
            self.assertEqual(wb_order_feed.main(), 0)
        self.connection.request.assert_called_once()
        self.assertEqual(self.connection.request.call_args.kwargs["headers"]["Authorization"], SHARED_READ_TOKEN)

    def test_shared_token_without_analytics_scope_makes_no_request(self):
        content_only = synthetic_token(wb_cards.CONTENT_SCOPE | wb_cards.READ_ONLY_SCOPE)
        with (
            patch.object(wb_order_feed, "read_settings", return_value={"WB_API_TOKEN": content_only}),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(wb_order_feed.main(), 1)
        self.connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
