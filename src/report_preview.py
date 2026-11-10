"""Build an independent local Power BI report with the selected starting period."""

from datetime import date, timedelta
import json
import os
import re
from pathlib import Path
import shutil
from uuid import uuid4

from .app_paths import APP_ROOT, RESOURCE_ROOT
from .load_wb_cards import load_database_config
from .wb_cards import ENV_PATH

import psycopg


MODEL = "Wildberries.SemanticModel"
REPORT = "Wildberries.Report"
DAILY = "c6640cd614b7a04e3059"
STOCKS = "a27f61cd2dcef7476957"
FINANCE = "c83a69314d8fb21e2f55"
FUNNEL = "3359b07b6149e419e95b"
ANALYSIS = "563308e951cd466c873a"
REPORT_PAGES = {
    "cards": DAILY, "orders": DAILY, "sales": DAILY, "stocks": STOCKS,
    "finance": FINANCE, "funnel": FUNNEL,
}
DATE_SLICERS = {
    DAILY: ("65ea3a288856b25bae62", "Календарь", "День"),
    FINANCE: ("bdcf0e4671774ba7af13", "Финансы", "День операции"),
    STOCKS: ("20fd872f9af747ab84f2", "Календарь", "День"),
    ANALYSIS: ("9043e700e61d4c14bf31", "Календарь", "День"),
}
FUNNEL_SLICER = "1c2229ba9b4e43eb9fc5"
VIEWS = {
    "Товары": "report_products",
    "Активность": "daily_product_activity",
    "Календарь": "report_days",
    "Остатки": "report_stocks",
    "История остатков": "report_stock_history",
    "Финансы": "report_money",
    "Валюты": "report_currencies",
    "Воронка": "report_funnel",
    "Типы отчётов": "report_money_types",
    "Анализ продаж": "report_finance_analysis",
}


def save_json(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def slicer_filter(objects: dict, entity: str, filters: list[dict]) -> None:
    general = objects.setdefault("general", [{"properties": {}}])
    general[0]["properties"]["filter"] = {"filter": {
        "Version": 2, "From": [{"Name": "d", "Entity": entity, "Type": 0}], "Where": filters,
    }}


def set_date_slicer(path: Path, entity: str, field: str, first: date, last: date) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    objects = document["visual"]["objects"]
    start = f"datetime'{first.isoformat()}T00:00:00'"
    end = f"datetime'{last.isoformat()}T00:00:00'"
    properties = objects["data"][0]["properties"]
    properties["startDate"] = {"expr": {"Literal": {"Value": start}}}
    properties["endDate"] = {"expr": {"Literal": {"Value": end}}}
    column = {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": field}}
    filters = [
        {"Condition": {"Comparison": {"ComparisonKind": kind, "Left": column, "Right": {
            "Literal": {"Value": value},
        }}}}
        for kind, value in ((2, start), (4, end))
    ]
    slicer_filter(objects, entity, filters)
    save_json(path, document)


def set_month_slicer(path: Path, months: list[str]) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    values = [[{"Literal": {"Value": f"'{month}'"}}] for month in months]
    field = {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": "Месяц"}}
    slicer_filter(document["visual"]["objects"], "Воронка", [
        {"Condition": {"In": {"Expressions": [field], "Values": values}}},
    ])
    save_json(path, document)


def make_live_partition(path: Path, table: str, view: str) -> None:
    definition = path.read_text(encoding="utf-8")
    partition = f"'{table}'" if " " in table else table
    marker = f"\tpartition {partition} = m\n"
    head, partition = definition.split(marker, 1)
    _, annotation, tail = partition.partition("\n\tannotation ")
    source = (
        f'{head}{marker}\t\tmode: directQuery\n\t\tsource =\n'
        f'\t\t\t\tPostgreSQL.Database(PgServer, PgDatabase){{[Schema="analytics", Item="{view}"]}}[Data]\n'
    )
    path.write_text(source + (annotation + tail if annotation else ""), encoding="utf-8")


def copy_report(template: Path, output: Path, filename: str) -> Path:
    output.mkdir(parents=True)
    for name in (REPORT, MODEL):
        shutil.copytree(template / name, output / name, ignore=shutil.ignore_patterns(".pbi"))
    target = output / filename
    shutil.copy2(template / "Wildberries.pbip", target)
    return target


def available_periods(connection: psycopg.Connection, first: date, last: date) -> tuple[dict, list[str]]:
    activity_first, activity_last, stocks_first, stocks_last, finance_first, finance_last = connection.execute(
        """
        SELECT MIN(day) FILTER (WHERE has_activity), MAX(day) FILTER (WHERE has_activity),
               MIN(day) FILTER (WHERE has_stock_history), MAX(day) FILTER (WHERE has_stock_history),
               MIN(day) FILTER (WHERE has_finance), MAX(day) FILTER (WHERE has_finance)
        FROM analytics.report_days
        WHERE day BETWEEN %s AND %s
        """,
        (first, last),
    ).fetchone()
    dates = {
        DAILY: (activity_first, activity_last), STOCKS: (stocks_first, stocks_last),
        FINANCE: (finance_first, finance_last), ANALYSIS: (finance_first, finance_last),
    }
    months = [month for (month,) in connection.execute(
        "SELECT DISTINCT month_label FROM analytics.report_funnel WHERE month_label BETWEEN %s AND %s ORDER BY 1",
        (first.strftime("%Y-%m"), last.strftime("%Y-%m")),
    )]
    return {page: bounds for page, bounds in dates.items() if bounds[0] is not None}, months


def prepare_report(
    source: str, first: date | None, last: date | None, *, template: Path = RESOURCE_ROOT / "powerbi",
    output_root: Path = APP_ROOT / "data" / "report-previews",
    all_pages: bool = False,
    date_ranges: dict[str, tuple[date, date]] | None = None,
    funnel_months: list[str] | None = None,
) -> Path:
    if (first is None) != (last is None) or (first is not None and first > last):
        raise ValueError("Укажите корректные начальную и конечную даты.")
    page = REPORT_PAGES[source]
    output = output_root / uuid4().hex
    period = f" {first:%Y-%m-%d} — {last:%Y-%m-%d}" if first is not None else " — текущие данные"
    target = copy_report(template, output, f"Wildberries{period}.pbip")

    for table, view in VIEWS.items():
        path = output / MODEL / "definition" / "tables" / f"{table}.tmdl"
        make_live_partition(path, table, view)

    pages = output / REPORT / "definition" / "pages"
    manifest = pages / "pages.json"
    settings = json.loads(manifest.read_text(encoding="utf-8"))
    settings["activePageName"] = page
    save_json(manifest, settings)
    if first is not None:
        for page_id, (slicer, entity, field) in DATE_SLICERS.items():
            bounds = date_ranges.get(page_id) if date_ranges is not None else (first, last)
            if bounds is not None and (all_pages or page == page_id or (page == FINANCE and page_id == ANALYSIS)):
                set_date_slicer(pages / page_id / "visuals" / slicer / "visual.json", entity, field, *bounds)
        if all_pages or page == FUNNEL:
            if funnel_months is None:
                current = first.replace(day=1)
                funnel_months = []
                while current <= last:
                    funnel_months.append(f"{current:%Y-%m}")
                    current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
            if funnel_months:
                set_month_slicer(pages / FUNNEL / "visuals" / FUNNEL_SLICER / "visual.json", funnel_months)
    return target



def _set_database_parameters(report: Path, config: dict[str, str | int]) -> None:
    path = report.parent / MODEL / "definition" / "expressions.tmdl"
    host = str(config["host"])
    if host == "::1":
        host = "localhost"
    values = {"PgServer": f"{host}:{config['port']}", "PgDatabase": str(config["dbname"])}
    expressions = path.read_text(encoding="utf-8")
    for name, value in values.items():
        quoted = value.replace("#(", "#(#)(").replace('"', '""')
        quoted = quoted.replace("\r", "#(cr)").replace("\n", "#(lf)").replace("\t", "#(tab)")
        pattern = rf'(?m)^(expression {name} = )""(?= meta \[)'
        expressions, count = re.subn(pattern, lambda match: f'{match[1]}"{quoted}"', expressions)
        if count != 1:
            raise ValueError(f"В шаблоне отчёта не найден параметр {name}.")
    path.write_text(expressions, encoding="utf-8")


def open_report(source: str, first: date | None, last: date | None, *, all_pages: bool = False) -> Path:
    date_ranges = funnel_months = None
    config = load_database_config(ENV_PATH)
    with psycopg.connect(**config) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('analytics.report_preview'))")
        for filename in (
            "006_stock_snapshots.sql", "009_finance_reports.sql", "011_daily_product_activity.sql", "013_live_report.sql",
        ):
            connection.execute((RESOURCE_ROOT / "sql" / filename).read_text(encoding="utf-8"), prepare=False)
        if first is not None and last is not None:
            date_ranges, funnel_months = available_periods(connection, first, last)
    report = prepare_report(source, first, last, all_pages=all_pages, date_ranges=date_ranges, funnel_months=funnel_months)
    _set_database_parameters(report, config)
    os.startfile(report)
    return report
