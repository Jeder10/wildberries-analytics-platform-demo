import unittest
from unittest.mock import patch

from src import load_wb_order_history as history
from src.wb_cards import CardCheckError


CURSOR = "2026-08-25T15:17:39.123456789"
ORDER = {
    "srid": "synthetic-unit",
    "nmId": 1234,
    "date": "2026-08-25T12:30:00",
    "lastChangeDate": CURSOR,
    "isCancel": False,
}


class OrderHistoryTests(unittest.TestCase):
    def test_read_preserves_full_cursor_and_accepts_order_without_size(self):
        with patch.object(history, "check_token_scope"), patch.object(
            history, "fetch_readonly_json", return_value=[ORDER]
        ) as fetch:
            self.assertEqual(history.request_order_changes("synthetic", CURSOR), [ORDER])
            args = fetch.call_args.kwargs
            self.assertEqual(args["method"], "GET")
            self.assertEqual(args["host"], "statistics-api.wildberries.ru")
            self.assertEqual(
                args["path"], "/api/v1/supplier/orders?dateFrom=2026-08-25T15%3A17%3A39.123456789&flag=0"
            )

    def test_invalid_cancel_flag_rejects_page(self):
        with self.assertRaisesRegex(CardCheckError, "признак отмены"):
            history.validate_order(dict(ORDER, isCancel=0))

    def test_rejects_duplicate_order_before_writing_page(self):
        with patch.object(history, "check_token_scope"), patch.object(
            history, "fetch_readonly_json", return_value=[ORDER, ORDER]
        ):
            with self.assertRaisesRegex(CardCheckError, "повторился srid"):
                history.request_order_changes("synthetic", CURSOR)


if __name__ == "__main__":
    unittest.main()
