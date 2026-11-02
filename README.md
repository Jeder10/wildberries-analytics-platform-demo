# Wildberries Analytics Platform

Локальная аналитика для продавца на Wildberries. Загрузчики читают данные по API, PostgreSQL хранит операции и историю загрузок, SQL-представления подготавливают данные для Power BI.

- [Архитектура](docs/architecture.md)
- [Источники API](docs/wb-api.md)
- [Показатели](docs/metrics.md)
- [Power BI](docs/power-bi.md)
- [Демонстрация](docs/power-bi-demo.md)

## Локальная разработка

Из корня проекта в PowerShell:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
& '.\.venv\Scripts\python.exe' -X utf8 -B -m unittest discover -s tests
```

Для подключения своего кабинета скопируйте [шаблон настроек](.env.example) в локальный `.env`, укажите тестовую или рабочую базу PostgreSQL и токен Wildberries с правами чтения. SQL-файлы содержат схемы и представления. Для новой базы их применяют в порядке нумерации.

## Демонстрация

Откройте `powerbi/Wildberries.pbip` в Power BI Desktop и нажмите «Обновить». По умолчанию модель использует вымышленные данные.
