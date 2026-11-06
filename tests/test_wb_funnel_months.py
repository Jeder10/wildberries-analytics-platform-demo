from datetime import date
import unittest

from src.load_wb_funnel_months import decode_page, month_windows
from src.wb_cards import CardCheckError


START = date(2024, 1, 1)
END = date(2024, 1, 31)
PRODUCT = {
    "product": {"nmId": 90001},
    "statistic": {"selected": {
        "period": {"start": START.isoformat(), "end": END.isoformat()},
        "orderCount": 5, "buyoutCount": 3, "cancelCount": 0,
    }},
}


class FunnelMonthTests(unittest.TestCase):
    def test_months_cover_leap_february_and_partial_last_month(self):
        self.assertEqual(list(month_windows(START, date(2024, 3, 9))), [
            (START, END),
            (date(2024, 2, 1), date(2024, 2, 29)),
            (date(2024, 3, 1), date(2024, 3, 9)),
        ])

    def test_zero_counts_are_retained_with_the_exact_requested_period(self):
        self.assertEqual(decode_page({"products": [PRODUCT]}, START, END), [(90001, 5, 3, 0)])
        with self.assertRaisesRegex(CardCheckError, "отличается от запроса"):
            decode_page({"products": [PRODUCT]}, START, date(2024, 1, 30))

    def test_repeated_product_is_rejected_before_writing(self):
        with self.assertRaisesRegex(CardCheckError, "Дубликат артикула"):
            decode_page({"products": [PRODUCT, PRODUCT]}, START, END)


if __name__ == "__main__":
    unittest.main()
