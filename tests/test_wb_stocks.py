from datetime import date
import unittest
from unittest.mock import patch

from src import wb_stocks
from src.wb_cards import CardCheckError
from src.wb_order_feed import completed_moscow_period


PERIOD = completed_moscow_period(date(2026, 11, 22))


def stock_row(nm_id, quantity):
    return {"nmID": nm_id, "metrics": {"stockCount": quantity}}


class ProductStockTests(unittest.TestCase):
    def setUp(self):
        self.scope = self.enterContext(patch.object(wb_stocks, "check_token_scope"))
        self.fetch = self.enterContext(patch.object(wb_stocks, "request_readonly_json"))
        self.enterContext(patch.object(wb_stocks, "completed_moscow_period", return_value=PERIOD))

    def test_sends_stock_report_filters_and_preserves_zero_and_full_uint64(self):
        self.fetch.return_value = {"data": {"items": [stock_row(501, 0), stock_row(502, 2**64 - 1)]}}

        result = wb_stocks.request_product_stocks("synthetic", [501, 502], limit=2)

        self.assertEqual(result, {501: 0, 502: 2**64 - 1})
        self.scope.assert_called_once_with("synthetic", scope=1 << 2, category="Аналитика")
        self.fetch.assert_called_once_with(
            "synthetic", host="seller-analytics-api.wildberries.ru", path="/api/v2/stocks-report/products/products",
            category="Аналитика",
            payload={
                "nmIDs": [501, 502],
                "currentPeriod": {"start": "2026-11-15", "end": "2026-11-21"},
                "stockType": "",
                "skipDeletedNm": True,
                "orderBy": {"field": "stockCount", "mode": "desc"},
                "availabilityFilters": ["deficient", "actual", "balanced", "nonActual", "nonLiquid", "invalidData"],
                "limit": 2,
                "offset": 0,
            },
        )

    def test_missing_article_remains_absent(self):
        self.fetch.return_value = {"data": {"items": []}}
        self.assertEqual(wb_stocks.request_product_stocks("synthetic", [501]), {})

    def test_empty_input_does_not_request_all_stocks(self):
        self.assertEqual(wb_stocks.request_product_stocks("synthetic", []), {})
        self.fetch.assert_not_called()

    def test_rejects_duplicate_or_unrequested_articles(self):
        for items, message in (
            ([stock_row(501, 1), stock_row(501, 2)], "повторился артикул"),
            ([stock_row(502, 0)], "вне фильтра"),
        ):
            with self.subTest(message=message):
                self.fetch.return_value = {"data": {"items": items}}
                with self.assertRaisesRegex(CardCheckError, message):
                    wb_stocks.request_product_stocks("synthetic", [501])

    def test_rejects_invalid_quantities_or_response_shape(self):
        for response, message in (
            ({"data": {"items": [stock_row(501, True)]}}, "stockCount"),
            ({"data": {"items": [stock_row(501, -1)]}}, "stockCount"),
            ({"data": {"items": [stock_row(501, 2**64)]}}, "stockCount"),
            ({"data": {}}, "список"),
        ):
            with self.subTest(message=message):
                self.fetch.return_value = response
                with self.assertRaisesRegex(CardCheckError, message):
                    wb_stocks.request_product_stocks("synthetic", [501])


if __name__ == "__main__":
    unittest.main()
