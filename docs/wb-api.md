# Источники Wildberries API

Каждый загрузчик проверяет категорию токена и права чтения перед запросом. Транспорт обрабатывает HTTP 401, 403, 429 и сетевые ошибки. Интервалы и правила остановки зависят от метода.

| Источник | Метод | Продолжение |
|---|---|---|
| Карточки | `POST /content/v2/get/cards/list` | Курсор `updatedAt` и `nmID` |
| Лента заказов | `POST /api/analytics/v1/order-feed` | `snapshotTime` и `offset` |
| Статистика заказов | `GET /api/v1/supplier/orders` | `lastChangeDate`, конец подтверждает пустой ответ |
| Статистика продаж и возвратов | `GET /api/v1/supplier/sales` | `lastChangeDate`, конец подтверждает пустой ответ |
| Снимок по товарам | `POST /api/v2/stocks-report/products/products` | Пакеты артикулов из каталога |
| Воронка | `POST /api/analytics/v3/sales-funnel/products` | Период и `offset` |
| Финансовые отчёты | `POST /api/finance/v1/sales-reports/detailed` | `rrdId`, конец подтверждает HTTP 204 |

Точные параметры каждого запроса находятся в соответствующих модулях загрузки и проверяются тестами. Оперативная статистика и воронка имеют разную глубину и гранулярность. Агрегаты воронки не содержат `srid`.

[Отчёты](https://dev.wildberries.ru/openapi/reports) · [Аналитика](https://dev.wildberries.ru/openapi/analytics) · [Финансы](https://dev.wildberries.kg/docs/openapi/documents-and-accounting)
