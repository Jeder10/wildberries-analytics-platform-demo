from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import io
import unittest
from unittest.mock import MagicMock, call, patch

from src import load_wb_stocks
from src.wb_cards import CardCheckError, RateLimitError


class StockSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.execute.return_value = [(501,), (502,), (503,)]
        self.enterContext(patch.object(load_wb_stocks, "read_token_claims", return_value={"acc": 1}))
        self.fetch = self.enterContext(patch.object(load_wb_stocks, "request_product_stocks"))
        self.wait = self.enterContext(patch.object(load_wb_stocks, "wait_for_request"))

    def test_batches_explicit_articles_without_turning_missing_rows_into_zero(self):
        self.fetch.side_effect = [{501: 0}, {503: 2**64 - 1}]

        with patch.object(load_wb_stocks, "MAX_PAGE_SIZE", 2):
            result = load_wb_stocks.collect_stock_snapshot(self.connection, "synthetic")

        self.assertEqual([(item.nm_id, item.quantity) for item in result], [(501, 0), (502, None), (503, 2**64 - 1)])
        self.assertEqual(result[0].captured_at, result[1].captured_at)
        self.assertEqual(self.fetch.call_args_list, [
            call("synthetic", [501, 502], limit=2),
            call("synthetic", [503], limit=1),
        ])
        self.wait.assert_called_once_with(result[0].captured_at + load_wb_stocks.BASIC_INTERVAL)

    def test_empty_or_duplicate_catalog_fails_before_api_call(self):
        for rows in ([], [(501,), (501,)]):
            with self.subTest(rows=rows):
                self.connection.execute.return_value = rows
                with self.assertRaisesRegex(CardCheckError, "непустой каталог с уникальным"):
                    load_wb_stocks.collect_stock_snapshot(self.connection, "synthetic")
        self.fetch.assert_not_called()

    def test_429_pauses_and_retries_the_same_batch(self):
        self.fetch.side_effect = [RateLimitError(60), {501: 0, 502: 0, 503: 15}]

        with redirect_stdout(io.StringIO()):
            result = load_wb_stocks.collect_stock_snapshot(self.connection, "synthetic")

        self.assertEqual(self.fetch.call_args_list, [call("synthetic", [501, 502, 503], limit=3)] * 2)
        self.wait.assert_called_once()
        self.assertEqual(len(result), 3)

    def test_api_failure_does_not_write_partial_snapshot(self):
        ready = datetime(2026, 11, 22, 20, 54, tzinfo=timezone.utc)
        with (
            patch.object(load_wb_stocks, "load_database_config", return_value={}),
            patch.object(load_wb_stocks, "load_token", return_value="synthetic"),
            patch.object(load_wb_stocks.psycopg, "connect"),
            patch.object(load_wb_stocks, "collect_stock_snapshot", side_effect=CardCheckError("HTTP 429.")),
            patch.object(load_wb_stocks, "store_stock_snapshot") as write,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(load_wb_stocks.main(not_before=ready), 1)

        self.wait.assert_called_once_with(ready)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
