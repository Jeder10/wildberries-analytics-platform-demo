from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import io
import unittest
from unittest.mock import call, patch

from src import load_wb_orders as loader
from src import wb_order_feed as feed
from src.wb_cards import CardCheckError


PERIOD = feed.completed_moscow_period(date(2026, 11, 21))
SNAPSHOT = "2026-11-21T12:00:00Z"
FIRST = {
    "srid": "synthetic-order-a",
    "nmId": 700001,
    "chrtId": 800001,
    "createdAt": "2026-11-16T09:00:00+03:00",
    "updatedAt": "2026-11-19T11:00:00+03:00",
    "status": "buyout",
}
SECOND = {**FIRST, "srid": "synthetic-order-b"}


def page(*rows, snapshot=SNAPSHOT):
    return {"snapshotTime": snapshot, "orders": list(rows)}


class OrderCollectionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(loader, "MAX_ORDER_PAGE_SIZE", 2))
        self.enterContext(patch.object(loader, "completed_moscow_period", return_value=PERIOD))
        self.claims = self.enterContext(patch.object(loader, "read_token_claims", return_value={"acc": 1}))
        self.fetch = self.enterContext(patch.object(loader, "request_order_page"))
        self.wait = self.enterContext(patch.object(loader, "sleep"))
        self.enterContext(redirect_stdout(io.StringIO()))

    def test_short_first_page_completes_in_one_request(self):
        self.fetch.return_value = page(FIRST)

        result = loader.collect_orders("synthetic", "WB_API_TOKEN")

        self.assertEqual(result, [feed.parse_order(FIRST)])
        self.fetch.assert_called_once_with(
            "synthetic", PERIOD, limit=2, offset=0, snapshot_time=None, token_key="WB_API_TOKEN"
        )
        self.wait.assert_not_called()

    def test_short_first_page_needs_no_usable_snapshot(self):
        for response in ({"orders": [FIRST]}, page(FIRST, snapshot="2026-11-21T12:00:00")):
            with self.subTest(response=response):
                self.fetch.reset_mock()
                self.fetch.return_value = response

                self.assertEqual(loader.collect_orders("synthetic", "WB_API_TOKEN"), [feed.parse_order(FIRST)])
                self.fetch.assert_called_once()
        self.wait.assert_not_called()

    def test_full_first_page_requires_usable_snapshot_before_continuing(self):
        self.fetch.return_value = page(FIRST, SECOND, snapshot=None)

        with self.assertRaisesRegex(CardCheckError, "snapshotTime"):
            loader.collect_orders("synthetic", "WB_API_TOKEN")

        self.fetch.assert_called_once()
        self.wait.assert_not_called()

    def test_basic_pagination_reuses_first_snapshot_and_server_interval(self):
        self.fetch.side_effect = [page(FIRST, SECOND), page()]

        result = loader.collect_orders("synthetic", "WB_API_TOKEN")

        self.assertEqual([order.srid for order in result], [FIRST["srid"], SECOND["srid"]])
        self.assertEqual(self.fetch.call_args_list, [
            call("synthetic", PERIOD, limit=2, offset=0, snapshot_time=None, token_key="WB_API_TOKEN"),
            call("synthetic", PERIOD, limit=2, offset=2, snapshot_time=SNAPSHOT, token_key="WB_API_TOKEN"),
        ])
        self.wait.assert_called_once_with(10_800)

    def test_personal_pagination_waits_one_minute(self):
        self.claims.return_value = {"acc": 3}
        self.fetch.side_effect = [page(FIRST, SECOND), page()]

        loader.collect_orders("synthetic", "WB_API_TOKEN")

        self.wait.assert_called_once_with(60)

    def test_repeated_srid_stops_before_returning_partial_batch(self):
        self.fetch.side_effect = [page(FIRST, SECOND), page(FIRST)]

        with self.assertRaisesRegex(CardCheckError, "повторился srid"):
            loader.collect_orders("synthetic", "WB_API_TOKEN")

    def test_changed_or_missing_snapshot_stops_before_returning_partial_batch(self):
        for next_page, message in (
            (page(snapshot="2026-11-21T12:01:00Z"), "Снимок.*изменился"),
            ({"orders": []}, "snapshotTime"),
        ):
            with self.subTest(next_page=next_page):
                self.fetch.side_effect = [page(FIRST, SECOND), next_page]
                with self.assertRaisesRegex(CardCheckError, message):
                    loader.collect_orders("synthetic", "WB_API_TOKEN")

    def test_parses_the_full_unsigned_size_identifier_range(self):
        self.assertEqual(feed.parse_order({**FIRST, "chrtId": 2**64 - 1}).chrt_id, 2**64 - 1)
        with self.assertRaisesRegex(CardCheckError, "диапазон"):
            feed.parse_order({**FIRST, "chrtId": 2**64})


class OrderLoaderTests(unittest.TestCase):
    def test_api_failure_skips_postgres_writes(self):
        stderr = io.StringIO()
        with (
            patch.object(loader, "load_database_config", return_value={}),
            patch.object(loader, "load_order_token", return_value=("WB_API_TOKEN", "synthetic")),
            patch.object(loader.psycopg, "connect"),
            patch.object(loader, "collect_orders", side_effect=CardCheckError("HTTP 429")),
            patch.object(loader, "store_orders") as write,
            redirect_stderr(stderr),
        ):
            self.assertEqual(loader.main(), 1)
        write.assert_not_called()
        self.assertIn("HTTP 429", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
