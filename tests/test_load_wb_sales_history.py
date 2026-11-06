from contextlib import redirect_stdout
from datetime import date, datetime, timezone
import io
import unittest
from unittest.mock import MagicMock, call, patch

from psycopg import sql

from src import load_wb_sales_history as history
from src.wb_cards import CardCheckError, RateLimitError, TransportError


DAY = date(2026, 11, 14)
START = DAY.isoformat()
CURSOR = "2026-11-14T10:11:12.123456789"
READY = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)


class SalesHistoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.results = self.connection.execute.return_value.fetchone
        self.fetch = self.enterContext(patch.object(history, "request_sales_changes"))
        self.store = self.enterContext(patch.object(history, "store_sales", return_value=1))
        self.wait = self.enterContext(patch.object(history, "wait_for_request"))
        self.enterContext(patch.object(history, "read_token_claims", return_value={"acc": 1}))
        self.stdout = self.enterContext(redirect_stdout(io.StringIO()))

    def test_follows_last_raw_timestamp_then_finishes_only_after_empty_page(self):
        self.results.side_effect = [
            (True,), (START, False, None), (CURSOR, False, READY), (CURSOR, True, READY)
        ]
        operations = [{"saleID": "R001", "lastChangeDate": CURSOR}]
        self.fetch.side_effect = [operations, []]

        history.load_history(self.connection, "synthetic", DAY)

        self.assertEqual(self.fetch.call_args_list, [call("synthetic", START), call("synthetic", CURSOR)])
        self.store.assert_called_once_with(self.connection, operations)
        self.wait.assert_called_once_with(READY)
        self.assertIn("Последняя страница пуста", self.stdout.getvalue())

    def test_429_waits_and_retries_same_cursor_without_writing(self):
        self.results.side_effect = [
            (True,), (CURSOR, False, None), (CURSOR, False, READY), (CURSOR, True, READY)
        ]
        self.fetch.side_effect = [RateLimitError(120), []]

        history.load_history(self.connection, "synthetic", DAY)

        self.assertEqual(self.fetch.call_args_list, [call("synthetic", CURSOR)] * 2)
        self.wait.assert_called_once_with(READY)
        self.store.assert_not_called()
        self.assertIn("HTTP 429", self.stdout.getvalue())

    def test_stalled_cursor_fails_without_writing_the_page(self):
        self.results.side_effect = [(True,), (CURSOR, False, None)]
        self.fetch.return_value = [{"saleID": "S001", "lastChangeDate": CURSOR}]

        with self.assertRaisesRegex(CardCheckError, "без продвижения курсора"):
            history.load_history(self.connection, "synthetic", DAY)

        self.store.assert_not_called()
        self.fetch.assert_called_once()

    def test_transport_retry_uses_request_phase_to_select_the_pause(self):
        for started, pause in (
            (False, history.CONNECT_RETRY_INTERVAL),
            (True, history.BASIC_INTERVAL),
        ):
            with self.subTest(request_started=started):
                self.results.side_effect = [
                    (True,), (CURSOR, False, None), (CURSOR, False, READY), (CURSOR, True, READY)
                ]
                self.fetch.side_effect = [TransportError("Network error.", request_started=started), []]
                with patch.object(history, "schedule_request") as schedule:
                    history.load_history(self.connection, "synthetic", DAY)
                    self.assertEqual(
                        schedule.call_args_list[1],
                        call(self.connection, DAY, pause, sql.Identifier("wb", "sales_history_progress")),
                    )
                self.fetch.assert_called_with("synthetic", CURSOR)
        self.store.assert_not_called()

    def test_does_not_make_request_if_another_loader_holds_lock(self):
        self.results.return_value = (False,)

        with self.assertRaisesRegex(CardCheckError, "уже запущена"):
            history.load_history(self.connection, "synthetic", DAY)

        self.fetch.assert_not_called()

    def test_cursor_comparison_is_timezone_aware_and_keeps_submicrosecond_order(self):
        equivalent = "2026-11-14T07:11:12.123456789Z"
        newer = "2026-11-14T10:11:12.123456790"

        self.assertEqual(history.cursor_sort_key(CURSOR), history.cursor_sort_key(equivalent))
        self.assertLess(history.cursor_sort_key(CURSOR), history.cursor_sort_key(newer))
        self.assertLess(history.cursor_sort_key(START), history.cursor_sort_key(CURSOR))


if __name__ == "__main__":
    unittest.main()
