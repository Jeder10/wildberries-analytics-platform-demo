from contextlib import redirect_stderr
import io
import unittest
from unittest.mock import call, patch

from src import load_wb_cards as loader
from src.wb_cards import CardCheckError


def card(nm_id, updated_at, title=None):
    return {"nmID": nm_id, "updatedAt": updated_at, "title": title}


def page(*cards):
    cursor = {"total": len(cards)}
    if cards:
        cursor.update(nmID=cards[-1]["nmID"], updatedAt=cards[-1]["updatedAt"])
    return {"cards": list(cards), "cursor": cursor}


EARLY = "2026-11-21T08:00:00Z"
LATER = "2026-11-21T09:00:00Z"
FIRST = card(700001, EARLY, "Synthetic product")
SECOND = card(700002, EARLY)


class CardCollectionTests(unittest.TestCase):
    def test_full_scan_overlaps_card_and_keeps_newer_version(self):
        changed_card = card(700001, LATER, "New name")
        with (
            patch.object(loader, "MAX_CARD_PAGE_SIZE", 2),
            patch.object(loader, "request_card_sample", side_effect=[page(FIRST, SECOND), page(changed_card)]) as fetch,
            patch.object(loader, "sleep") as wait,
        ):
            products = {product.nm_id: product for product in loader.collect_cards("test-token")}

        self.assertEqual(set(products), {700001, 700002})
        self.assertEqual(products[700001].title, "New name")
        self.assertEqual(fetch.call_args_list, [
            call("test-token", after=None, limit=2),
            call("test-token", after=(EARLY, 700002), limit=2),
        ])
        wait.assert_called_once_with(1)

    def test_full_page_followed_by_empty_page_completes(self):
        with (
            patch.object(loader, "MAX_CARD_PAGE_SIZE", 2),
            patch.object(loader, "request_card_sample", side_effect=[page(FIRST, SECOND), page()]) as fetch,
            patch.object(loader, "sleep"),
        ):
            self.assertEqual(len(loader.collect_cards("test-token")), 2)
        self.assertEqual(fetch.call_count, 2)

    def test_repeated_cursor_stops_the_full_scan(self):
        repeated = page(FIRST, SECOND)
        with (
            patch.object(loader, "MAX_CARD_PAGE_SIZE", 2),
            patch.object(loader, "request_card_sample", side_effect=[repeated, repeated]) as fetch,
            patch.object(loader, "sleep"),
        ):
            with self.assertRaisesRegex(CardCheckError, "повторил курсор"):
                loader.collect_cards("test-token")
        self.assertEqual(fetch.call_count, 2)

    def test_sample_requests_only_one_card(self):
        with patch.object(loader, "request_card_sample", return_value=page(FIRST)) as fetch:
            self.assertEqual(len(loader.collect_cards("test-token", sample=True)), 1)
        fetch.assert_called_once_with("test-token", after=None, limit=1)

    def test_timestamp_requires_timezone(self):
        with self.assertRaisesRegex(CardCheckError, "часовой пояс"):
            loader.parse_card(card(700001, "2026-11-21T08:00:00"))


class LoaderTests(unittest.TestCase):
    def test_template_config_stops_before_api_or_postgres(self):
        template = {
            "PGHOST": "127.0.0.1",
            "PGPORT": "test-fixture-db-credential",
            "PGDATABASE": "wb_analytics_example",
            "PGUSER": "wb_etl_example",
            "PGPASSWORD": "replace_me",
        }
        stderr = io.StringIO()
        with (
            patch.object(loader, "read_settings", return_value=template),
            patch.object(loader, "load_token") as token,
            patch.object(loader.psycopg, "connect") as connect,
            redirect_stderr(stderr),
        ):
            self.assertEqual(loader.main(sample=True), 1)
        token.assert_not_called()
        connect.assert_not_called()
        self.assertNotIn("replace_me", stderr.getvalue())

    def test_remote_postgres_host_rejected(self):
        settings = {
            "PGHOST": "db.example.invalid",
            "PGPORT": "test-fixture-db-credential",
            "PGDATABASE": "test_catalog",
            "PGUSER": "test_role",
            "PGPASSWORD": "synthetic-password",
        }
        with patch.object(loader, "read_settings", return_value=settings):
            with self.assertRaisesRegex(CardCheckError, "локальный адрес"):
                loader.load_database_config(loader.ENV_PATH)

    def test_failed_api_fetch_leaves_postgres_store_untouched(self):
        stderr = io.StringIO()
        with (
            patch.object(loader, "load_database_config", return_value={}),
            patch.object(loader, "load_token", return_value="test-token"),
            patch.object(loader.psycopg, "connect"),
            patch.object(loader, "collect_cards", side_effect=CardCheckError("HTTP 429")),
            patch.object(loader, "store_cards") as write,
            redirect_stderr(stderr),
        ):
            self.assertEqual(loader.main(), 1)
        write.assert_not_called()
        self.assertIn("HTTP 429", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
