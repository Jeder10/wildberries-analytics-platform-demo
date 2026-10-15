"""Check access to Wildberries product cards and their page cursor."""

import argparse
import base64
from collections.abc import Callable
from contextlib import closing
from http.client import HTTPException, HTTPSConnection
import json
from pathlib import Path
import re
import sys
from time import sleep
from typing import Literal


ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
CARDS_HOST = "content-api.wildberries.ru"
CARDS_PATH = "/content/v2/get/cards/list"
TIMEOUT_SECONDS = 10
CARD_LIMIT = 1
MAX_CARD_PAGE_SIZE = 100
PAGE_DELAY_SECONDS = 1  # WB documents a 750 ms interval.

# Bit positions documented by Wildberries for the JWT field "s".
CONTENT_SCOPE = 1 << 1
READ_ONLY_SCOPE = 1 << 30
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
NO_CONTENT = object()


class CardCheckError(Exception):
    """An error whose message is safe to display locally."""


class RateLimitError(CardCheckError):
    def __init__(self, retry_after: int | None):
        self.retry_after = retry_after
        if retry_after is None:
            super().__init__("HTTP 429. Достигнут лимит запросов Wildberries.")
        else:
            super().__init__(f"HTTP 429. Повторите запуск через {retry_after} с или позже.")


class TransportError(CardCheckError):
    def __init__(self, message: str, *, request_started: bool):
        super().__init__(message)
        self.request_started = request_started


def read_settings(path: Path, *names: str) -> dict[str, str]:
    values = {}
    try:
        with path.open(encoding="utf-8-sig") as env_file:
            for line in env_file:
                key, _, value = line.partition("=")
                key = key.strip()
                if key not in names:
                    continue
                if key in values:
                    raise CardCheckError(f"Оставьте в .env одну настройку {key}.")
                value = value.strip()
                if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
                    value = value[1:-1]
                values[key] = value
    except FileNotFoundError:
        raise CardCheckError("Создайте локальный .env и задайте настройки подключения.") from None
    except (OSError, UnicodeError):
        raise CardCheckError("Не удалось прочитать локальный .env в UTF-8.") from None
    return values


def load_token(path: Path, *, key: str = "WB_API_TOKEN") -> str:
    token = read_settings(path, key).get(key)
    if not token:
        raise CardCheckError(f"{key} не задан в .env.")
    return token


def read_token_claims(token: str, key: str) -> dict:
    if not TOKEN_PATTERN.fullmatch(token):
        raise CardCheckError(f"{key} должен содержать токен Wildberries в формате JWT.")

    # This check only reads permissions from the token. WB validates its signature.
    payload = token.split(".")[1]
    padded_payload = payload + "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.b64decode(padded_payload, altchars=b"-_", validate=True))
    except (ValueError, RecursionError):
        raise CardCheckError(f"Не удалось прочитать права {key}.") from None
    if not isinstance(claims, dict):
        raise CardCheckError(f"Не удалось прочитать права {key}.")
    return claims


def check_token_scope(
    token: str,
    *,
    scope: int = CONTENT_SCOPE,
    category: str = "Контент",
    key: str = "WB_API_TOKEN",
) -> None:
    scopes = read_token_claims(token, key).get("s")
    if type(scopes) is not int or scopes < 0:
        raise CardCheckError(f"Не удалось прочитать права {key}.")
    if not (scopes & scope and scopes & READ_ONLY_SCOPE):
        raise CardCheckError(f"Нужен токен категории «{category}» с правами «Только чтение».")


def request_readonly_json(
    token: str, *, host: str, path: str, payload: dict, category: str
) -> dict:
    data = fetch_readonly_json(
        token, method="POST", host=host, path=path, category=category, payload=payload
    )
    if not isinstance(data, dict):
        raise CardCheckError("HTTP 200. Ожидался объект JSON.")
    return data


def fetch_readonly_json(
    token: str,
    *,
    method: Literal["GET", "POST"],
    host: str,
    path: str,
    category: str,
    payload: dict | None = None,
    parse_float: Callable[[str], object] = float,
    allow_no_content: bool = False,
) -> object:
    """Send one request to a documented read-only method without following redirects."""
    headers = {"Authorization": token}
    body = None
    if payload is not None:
        body = json.dumps(payload)
        headers["Content-Type"] = "application/json"

    request_started = False
    try:
        with closing(HTTPSConnection(host, timeout=TIMEOUT_SECONDS)) as connection:
            connection.connect()
            request_started = True
            connection.request(method, path, body=body, headers=headers)
            with closing(connection.getresponse()) as response:
                if response.status == 204 and allow_no_content:
                    return NO_CONTENT
                if response.status == 429:
                    retry = response.getheader("X-Ratelimit-Retry", "").strip()
                    retry_after = int(retry) if re.fullmatch(r"[0-9]{1,9}", retry) else None
                    raise RateLimitError(retry_after)
                if response.status == 401:
                    raise CardCheckError("HTTP 401. Проверьте действительность токена Wildberries.")
                if response.status == 403:
                    raise CardCheckError(f"HTTP 403. Проверьте доступ токена к категории «{category}».")
                if response.status != 200:
                    raise CardCheckError(f"HTTP {response.status}. Запрос Wildberries завершился ошибкой.")
                raw_body = response.read()
    except TimeoutError:
        if request_started:
            message = "Истекло время ожидания ответа Wildberries."
        else:
            message = "Истекло время ожидания соединения с Wildberries."
        raise TransportError(message, request_started=request_started) from None
    except (OSError, HTTPException):
        raise TransportError(
            "Ошибка HTTPS-соединения с Wildberries.", request_started=request_started
        ) from None

    try:
        return json.loads(raw_body, parse_float=parse_float)
    except (ValueError, RecursionError):
        raise CardCheckError("HTTP 200. Wildberries вернул некорректный JSON.") from None


def request_card_sample(
    token: str, after: tuple[str, int] | None = None, *, limit: int = CARD_LIMIT
) -> dict:
    """Request one page of cards and validate the response envelope."""
    if type(limit) is not int or not 1 <= limit <= MAX_CARD_PAGE_SIZE:
        raise CardCheckError(f"Размер страницы должен быть от 1 до {MAX_CARD_PAGE_SIZE}.")
    check_token_scope(token)
    page_cursor: dict[str, int | str] = {"limit": limit}
    if after is not None:
        page_cursor["updatedAt"], page_cursor["nmID"] = after
    data = request_readonly_json(
        token,
        host=CARDS_HOST,
        path=CARDS_PATH,
        category="Контент",
        payload={
            "settings": {
                "sort": {"ascending": True},
                "cursor": page_cursor,
                "filter": {"withPhoto": -1},
            }
        },
    )
    cards = data.get("cards")
    cursor = data.get("cursor")
    if not isinstance(cards, list) or len(cards) > limit:
        raise CardCheckError(f"HTTP 200. Ожидался список максимум на {limit} карточек.")
    if not isinstance(cursor, dict) or type(cursor.get("total")) is not int:
        raise CardCheckError("HTTP 200. В ответе отсутствует счётчик cursor.total.")
    if cursor["total"] != len(cards):
        raise CardCheckError("HTTP 200. Счётчик cursor.total отличается от числа карточек.")
    for card in cards:
        if not isinstance(card, dict) or type(card.get("nmID")) is not int:
            raise CardCheckError("HTTP 200. В карточке отсутствует числовой артикул nmID.")
    if cards:
        updated_at = cursor.get("updatedAt")
        if type(cursor.get("nmID")) is not int or not isinstance(updated_at, str) or not updated_at.strip():
            raise CardCheckError("HTTP 200. В курсоре отсутствует nmID или updatedAt.")
    return data


def check_card_pagination(token: str) -> str:
    first = request_card_sample(token)
    if not first["cards"]:
        return "Страница 1: 0. Конец выгрузки."

    first_cursor = (first["cursor"]["updatedAt"], first["cursor"]["nmID"])
    sleep(PAGE_DELAY_SECONDS)
    try:
        second = request_card_sample(token, after=first_cursor)
    except CardCheckError as error:
        raise CardCheckError(f"Страница 2. {error}") from None
    if not second["cards"]:
        return "Страница 1: 1. Страница 2: 0. Конец выгрузки."

    second_cursor = (second["cursor"]["updatedAt"], second["cursor"]["nmID"])
    if second_cursor == first_cursor:
        raise CardCheckError("Страница 2. Wildberries вернул тот же курсор.")
    if first["cards"][0]["nmID"] == second["cards"][0]["nmID"]:
        raise CardCheckError("Страница 2. Wildberries вернул карточку с первой страницы.")
    return "Страница 1: 1. Страница 2: 1. Артикулы карточек различаются."


def main(env_path: Path = ENV_PATH, *, check_pagination: bool = False) -> int:
    try:
        token = load_token(env_path)
        if check_pagination:
            summary = check_card_pagination(token)
        else:
            data = request_card_sample(token)
            summary = f"Карточек в ответе: {len(data['cards'])}."
    except CardCheckError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"HTTP 200. {summary}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Пробное чтение карточек Wildberries.")
    parser.add_argument(
        "--check-pagination",
        action="store_true",
        help="Проверить переход между двумя страницами по одной карточке.",
    )
    arguments = parser.parse_args()
    raise SystemExit(main(check_pagination=arguments.check_pagination))
