import base64
from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPResponse
import io
import json
from pathlib import Path
from tempfile import mkdtemp
import unittest
from unittest.mock import Mock, patch

from src import wb_cards


def synthetic_token(scope: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"s": scope}).encode()).decode().rstrip("=")
    return f"synthetic.{payload}.signature"


READ_TOKEN = synthetic_token(wb_cards.CONTENT_SCOPE | wb_cards.READ_ONLY_SCOPE)
CARD = {"nmID": 700001, "title": "Synthetic product"}
CURSOR = {"nmID": 700001, "updatedAt": "2026-11-19T10:00:00Z", "total": 1}
FIRST_PAGE = {"cards": [CARD], "cursor": CURSOR}
SECOND_PAGE = {"cards": [{"nmID": 700002}], "cursor": {**CURSOR, "nmID": 700002}}
EMPTY_PAGE = {"cards": [], "cursor": {"total": 0}}


class LocalConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = Path(mkdtemp(prefix="wb-test-", dir=Path(__file__).resolve().parent))
        self.path = temporary / "synthetic-settings.txt"
        self.addCleanup(temporary.rmdir)
        self.addCleanup(self.path.unlink, missing_ok=True)

    def test_reads_utf8_bom_and_quoted_value_without_changing_config(self):
        original = f'WB_API_TOKEN="{READ_TOKEN}"\nPGPORT=test-fixture-db-credential\n'.encode("utf-8-sig")
        self.path.write_bytes(original)

        self.assertEqual(wb_cards.load_token(self.path), READ_TOKEN)
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_config_stops_before_request(self):
        stderr = io.StringIO()
        with patch.object(wb_cards, "HTTPSConnection") as connection, redirect_stderr(stderr):
            self.assertEqual(wb_cards.main(self.path), 1)

        connection.assert_not_called()
        self.assertIn("Создайте локальный .env", stderr.getvalue())

    def test_empty_and_duplicate_settings_are_rejected(self):
        for content, message in (
            ("PGPORT=test-fixture-db-credential\nWB_API_TOKEN=\n", "не задан"),
            ("WB_API_TOKEN=one\nWB_API_TOKEN=two\n", "одну настройку"),
        ):
            with self.subTest(message=message):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_cards.load_token(self.path)


class CardSampleTests(unittest.TestCase):
    def setUp(self):
        self.connect = self.enterContext(patch.object(wb_cards, "HTTPSConnection", autospec=True))
        self.connection = self.connect.return_value
        self.response = Mock(spec=HTTPResponse)
        self.response.status = 200
        self.connection.getresponse.return_value = self.response

    def respond_with(self, payload):
        self.response.read.return_value = json.dumps(payload).encode()

    def test_sends_one_post_for_one_card(self):
        self.respond_with(FIRST_PAGE)

        self.assertEqual(wb_cards.request_card_sample(READ_TOKEN), FIRST_PAGE)

        self.connect.assert_called_once_with("content-api.wildberries.ru", timeout=10)
        self.connection.request.assert_called_once()
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("POST", "/content/v2/get/cards/list"))
        self.assertEqual(kwargs["headers"]["Authorization"], READ_TOKEN)
        self.assertEqual(json.loads(kwargs["body"])["settings"], {
            "sort": {"ascending": True},
            "cursor": {"limit": 1},
            "filter": {"withPhoto": -1},
        })
        self.response.close.assert_called_once()
        self.connection.close.assert_called_once()

    def test_accepts_empty_result(self):
        self.respond_with(EMPTY_PAGE)

        self.assertEqual(wb_cards.request_card_sample(READ_TOKEN), EMPTY_PAGE)

    def test_rejects_bad_token_or_missing_permissions_before_connection(self):
        for token in (
            "bad-token",
            "test.____.signature",
            synthetic_token(wb_cards.CONTENT_SCOPE),
            synthetic_token(wb_cards.READ_ONLY_SCOPE),
            synthetic_token(-1),
        ):
            with self.subTest():
                with self.assertRaises(wb_cards.CardCheckError):
                    wb_cards.request_card_sample(token)

        self.connect.assert_not_called()

    def test_rejects_incomplete_or_oversized_response(self):
        for payload, message in (
            ([], "объект JSON"),
            ({"cursor": CURSOR}, "список максимум"),
            ({"cards": [CARD, CARD], "cursor": CURSOR}, "список максимум"),
            ({"cards": [CARD], "cursor": {}}, "cursor.total"),
            ({"cards": [CARD], "cursor": {"total": 0}}, "отличается"),
            ({"cards": [{}], "cursor": CURSOR}, "артикул"),
            ({"cards": [CARD], "cursor": {"total": 1}}, "В курсоре"),
        ):
            with self.subTest(message=message):
                self.respond_with(payload)
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_cards.request_card_sample(READ_TOKEN)

    def test_invalid_json_is_reported_without_showing_the_body(self):
        self.response.read.return_value = b"synthetic-invalid-body"
        with self.assertRaisesRegex(wb_cards.CardCheckError, "некорректный JSON") as error:
            wb_cards.request_card_sample(READ_TOKEN)
        self.assertNotIn("synthetic-invalid-body", str(error.exception))

    def test_timeout_closes_connection(self):
        self.connection.getresponse.side_effect = TimeoutError("synthetic-network-detail")

        with self.assertRaisesRegex(wb_cards.CardCheckError, "Истекло время ожидания") as error:
            wb_cards.request_card_sample(READ_TOKEN)

        self.assertNotIn("synthetic-network-detail", str(error.exception))
        self.connection.request.assert_called_once()
        self.connection.close.assert_called_once()

    def test_connection_error_uses_safe_message(self):
        self.connection.request.side_effect = OSError("synthetic-network-detail")
        with self.assertRaisesRegex(wb_cards.CardCheckError, "Ошибка HTTPS-соединения") as error:
            wb_cards.request_card_sample(READ_TOKEN)
        self.assertNotIn("synthetic-network-detail", str(error.exception))

    def test_errors_and_redirects_stop_without_reading_the_body(self):
        for status in (302, 401, 403, 500):
            with self.subTest(status=status):
                self.connection.request.reset_mock()
                self.response.status = status
                with self.assertRaisesRegex(wb_cards.CardCheckError, f"HTTP {status}"):
                    wb_cards.request_card_sample(READ_TOKEN)
                self.connection.request.assert_called_once()

        self.response.read.assert_not_called()

    def test_rate_limit_reports_seconds_without_retrying(self):
        self.response.status = 429
        self.response.getheader.return_value = "12"

        with self.assertRaisesRegex(wb_cards.CardCheckError, "HTTP 429.*12 с"):
            wb_cards.request_card_sample(READ_TOKEN)

        self.response.getheader.assert_called_once_with("X-Ratelimit-Retry", "")
        self.connection.request.assert_called_once()
        self.response.read.assert_not_called()

    def test_rate_limit_handles_missing_or_malformed_retry_header(self):
        self.response.status = 429
        for retry_header in ("", "synthetic-header"):
            with self.subTest():
                self.response.getheader.return_value = retry_header
                with self.assertRaisesRegex(wb_cards.CardCheckError, "Достигнут лимит запросов") as error:
                    wb_cards.request_card_sample(READ_TOKEN)
                self.assertNotIn("synthetic-header", str(error.exception))

    def test_cli_prints_only_http_status_and_count(self):
        self.respond_with(FIRST_PAGE)
        stdout = io.StringIO()

        with patch.object(wb_cards, "load_token", return_value=READ_TOKEN), redirect_stdout(stdout):
            self.assertEqual(wb_cards.main(), 0)

        self.assertEqual(stdout.getvalue(), "HTTP 200. Карточек в ответе: 1.\n")

    def test_second_request_uses_both_cursor_fields_and_waits(self):
        self.response.read.side_effect = [json.dumps(page).encode() for page in (FIRST_PAGE, SECOND_PAGE)]

        with patch.object(wb_cards, "sleep") as wait:
            summary = wb_cards.check_card_pagination(READ_TOKEN)

        wait.assert_called_once_with(1)
        self.assertEqual(self.connection.request.call_count, 2)
        cursors = [
            json.loads(call.kwargs["body"])["settings"]["cursor"]
            for call in self.connection.request.call_args_list
        ]
        self.assertEqual(cursors, [
            {"limit": 1},
            {"limit": 1, "updatedAt": CURSOR["updatedAt"], "nmID": CURSOR["nmID"]},
        ])
        self.assertIn("Артикулы карточек различаются", summary)

    def test_pagination_stops_after_an_empty_first_page(self):
        self.respond_with(EMPTY_PAGE)
        with patch.object(wb_cards, "sleep") as wait:
            summary = wb_cards.check_card_pagination(READ_TOKEN)

        wait.assert_not_called()
        self.connection.request.assert_called_once()
        self.assertEqual(summary, "Страница 1: 0. Конец выгрузки.")

    def test_pagination_accepts_an_empty_second_page(self):
        self.response.read.side_effect = [json.dumps(page).encode() for page in (FIRST_PAGE, EMPTY_PAGE)]
        with patch.object(wb_cards, "sleep"):
            summary = wb_cards.check_card_pagination(READ_TOKEN)

        self.assertEqual(self.connection.request.call_count, 2)
        self.assertEqual(summary, "Страница 1: 1. Страница 2: 0. Конец выгрузки.")

    def test_pagination_rejects_a_repeated_cursor_or_card(self):
        for second_page, message in (
            ({"cards": SECOND_PAGE["cards"], "cursor": CURSOR}, "тот же курсор"),
            ({"cards": [CARD], "cursor": SECOND_PAGE["cursor"]}, "карточку с первой страницы"),
        ):
            with self.subTest(message=message), patch.object(wb_cards, "sleep"):
                self.response.read.side_effect = [json.dumps(page).encode() for page in (FIRST_PAGE, second_page)]
                with self.assertRaisesRegex(wb_cards.CardCheckError, message):
                    wb_cards.check_card_pagination(READ_TOKEN)

    def test_pagination_stops_on_second_page_rate_limit(self):
        self.respond_with(FIRST_PAGE)
        limited = Mock(spec=HTTPResponse, status=429)
        limited.getheader.return_value = "12"
        self.connection.getresponse.side_effect = [self.response, limited]

        with patch.object(wb_cards, "sleep"):
            with self.assertRaisesRegex(wb_cards.CardCheckError, "Страница 2.*HTTP 429.*12 с"):
                wb_cards.check_card_pagination(READ_TOKEN)

        self.assertEqual(self.connection.request.call_count, 2)
        limited.read.assert_not_called()

    def test_pagination_cli_prints_only_summary(self):
        self.response.read.side_effect = [json.dumps(page).encode() for page in (FIRST_PAGE, SECOND_PAGE)]
        stdout = io.StringIO()
        with (
            patch.object(wb_cards, "load_token", return_value=READ_TOKEN),
            patch.object(wb_cards, "sleep"),
            redirect_stdout(stdout),
        ):
            self.assertEqual(wb_cards.main(check_pagination=True), 0)

        self.assertEqual(
            stdout.getvalue(),
            "HTTP 200. Страница 1: 1. Страница 2: 1. Артикулы карточек различаются.\n",
        )


if __name__ == "__main__":
    unittest.main()
