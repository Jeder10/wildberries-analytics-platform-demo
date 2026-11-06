from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from src.load_wb_stock_history import stock_history_rows
from src.wb_cards import CardCheckError


class StockHistoryImportTests(unittest.TestCase):
    def read_csv(self, content):
        with TemporaryDirectory(dir=Path(__file__).parent) as directory:
            archive = Path(directory) / "stocks.zip"
            with ZipFile(archive, "w") as report:
                report.writestr("stocks.csv", content)
            return stock_history_rows(archive)

    def test_aggregates_sizes_and_locations_while_preserving_zero_and_missing(self):
        header = "NmID,ChrtID,OfficeName,25.08.2026,26.08.2026,27.08.2026\n"
        data = "1,10,WB,2,0,\n1,10,FBS,3,,\n1,20,WB,4,,\n2,10,WB,0,,6\n"

        self.assertEqual(self.read_csv(header + data), [
            (date(2026, 8, 25), 1, 9),
            (date(2026, 8, 25), 2, 0),
            (date(2026, 8, 26), 1, 0),
            (date(2026, 8, 27), 2, 6),
        ])

    def test_rejects_duplicate_source_grain_before_aggregation(self):
        header = "NmID,ChrtID,OfficeName,25.08.2026\n"
        with self.assertRaisesRegex(CardCheckError, "повторяется"):
            self.read_csv(header + "1,10,WB,2\n1,10,WB,3\n")

    def test_rejects_non_quantity_cell(self):
        with self.assertRaisesRegex(CardCheckError, "некорректный остаток"):
            self.read_csv("NmID,ChrtID,OfficeName,25.08.2026\n1,10,WB,-1\n")
