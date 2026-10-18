from contextlib import redirect_stderr, redirect_stdout
from datetime import date
import io
import unittest
from unittest.mock import patch

from src import load_wb_sales
from src.wb_cards import CardCheckError


REPORT_DAY = date(2026, 11, 14)


class SalesLoaderTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(load_wb_sales, "load_database_config", return_value={}))
        self.enterContext(patch.object(load_wb_sales, "load_token", return_value="synthetic"))
        connect = self.enterContext(patch.object(load_wb_sales.psycopg, "connect"))
        self.connection = connect.return_value.__enter__.return_value
        self.fetch = self.enterContext(patch.object(load_wb_sales, "request_sales_day"))
        self.write = self.enterContext(patch.object(load_wb_sales, "store_sales", return_value=2))

    def test_loads_validated_operations_and_reports_counts(self):
        operations = [{"saleID": "S00001"}, {"saleID": "R00002"}]
        self.fetch.return_value = operations
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            self.assertEqual(load_wb_sales.main(REPORT_DAY), 0)

        self.fetch.assert_called_once_with("synthetic", REPORT_DAY)
        self.write.assert_called_once_with(self.connection, operations)
        self.assertEqual(
            stdout.getvalue(), "Операций за 2026-11-14: 2. Вставлено или обновлено строк: 2.\n"
        )

    def test_api_failure_does_not_write_to_postgres(self):
        self.fetch.side_effect = CardCheckError("HTTP 429.")
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            self.assertEqual(load_wb_sales.main(REPORT_DAY), 1)

        self.write.assert_not_called()
        self.assertEqual(stderr.getvalue(), "HTTP 429.\n")


if __name__ == "__main__":
    unittest.main()
