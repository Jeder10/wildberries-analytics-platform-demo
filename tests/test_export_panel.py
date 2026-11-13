from datetime import date, datetime, time
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from src import app_paths, export_jobs, export_panel, panel_settings, report_demo, report_preview, windows_schedule
from src.wb_sales import MOSCOW_TIME


class PackagedLauncherTests(unittest.TestCase):
    def test_detached_worker_and_scheduled_task_use_the_packaged_executable(self):
        directory = Path("C:/Installed apps/Wildberries")
        executable = str(directory / "Wildberries Analytics.exe")
        now = datetime(2026, 11, 24, 8, tzinfo=MOSCOW_TIME)
        with (
            patch.object(app_paths, "FROZEN", True),
            patch.object(app_paths.sys, "executable", executable),
            patch.object(windows_schedule, "APP_ROOT", directory),
        ):
            self.assertEqual(app_paths.worker_command("run", "cards"), [executable, "--worker", "run", "cards"])
            self.assertEqual(app_paths.worker_environment()["PYINSTALLER_RESET_ENVIRONMENT"], "1")
            xml = windows_schedule.task_xml("sales", "week", "daily", time(9), now, "S-1-5-21-10")

        task = ET.fromstring(xml)
        ns = {"t": windows_schedule.TASK_NAMESPACE}
        self.assertEqual(task.findtext(".//t:Command", namespaces=ns), executable)
        self.assertEqual(task.findtext(".//t:WorkingDirectory", namespaces=ns), str(directory))
        self.assertTrue(task.findtext(".//t:Arguments", namespaces=ns).startswith("--worker scheduled sales week "))


class DateInputTests(unittest.TestCase):
    def test_accepts_digit_input_formatted_paste_and_deletion(self):
        accepted = (
            ("2026", "1", "6"),
            ("20260101", "1", "20260101"),
            ("2026-01-01", "1", "2026-01-01"),
            ("202-01-01", "0", "6"),
            ("", "0", "2026-01-01"),
        )
        for proposed, action, change in accepted:
            with self.subTest(proposed=proposed):
                self.assertTrue(export_panel.date_edit_allowed(proposed, action, change))

    def test_rejects_letters_manual_separators_and_extra_digits(self):
        rejected = (
            ("2026янв", "янв"),
            ("2026-", "-"),
            ("2026 01", " 01"),
            ("202611241", "1"),
        )
        for proposed, change in rejected:
            with self.subTest(proposed=proposed):
                self.assertFalse(export_panel.date_edit_allowed(proposed, "1", change))


class ScheduleTests(unittest.TestCase):
    def test_periods_use_previous_completed_calendar_interval(self):
        today = date(2026, 1, 1)
        self.assertEqual(export_jobs.period_dates("day", today), (date(2025, 12, 31), date(2025, 12, 31)))
        self.assertEqual(export_jobs.period_dates("week", today), (date(2025, 12, 22), date(2025, 12, 28)))
        self.assertEqual(export_jobs.period_dates("month", today), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_schedule_includes_logon_recovery_and_a_local_working_directory(self):
        now = datetime(2026, 11, 24, 8, tzinfo=MOSCOW_TIME)
        xml = windows_schedule.task_xml("sales", "week", "weekly", time(9), now, "S-1-5-21-10")
        task = ET.fromstring(xml)
        ns = {"t": windows_schedule.TASK_NAMESPACE}
        self.assertIsNotNone(task.find(".//t:DaysOfWeek/t:Monday", ns))
        self.assertEqual(task.findtext(".//t:LogonTrigger/t:UserId", namespaces=ns), "S-1-5-21-10")
        self.assertEqual(task.findtext(".//t:StartWhenAvailable", namespaces=ns), "true")
        self.assertIn("scheduled sales week weekly 09:00", task.findtext(".//t:Arguments", namespaces=ns))

    def test_financial_month_runs_after_the_final_week_has_closed(self):
        now = datetime(2026, 12, 1, 8, tzinfo=MOSCOW_TIME)
        due = windows_schedule.latest_occurrence("monthly", time(9), now, day_of_month=8)
        self.assertEqual(due.date(), date(2026, 11, 8))
        xml = windows_schedule.task_xml("finance", "month", "monthly", time(9), now, "S-1-5-21-10")
        self.assertIn("<Day>8</Day>", xml.decode("utf-16"))

    def test_custom_weekday_and_month_day_use_the_same_calendar_as_windows(self):
        now = datetime(2026, 11, 24, 18, tzinfo=MOSCOW_TIME)
        next_tuesday = windows_schedule.next_occurrence("weekly", time(17), now, weekday=1)
        self.assertEqual(next_tuesday.date(), date(2026, 12, 1))
        xml = windows_schedule.task_xml("finance", "week", "weekly", time(17), now, "S-1-5-21-10", weekday=1)
        task = ET.fromstring(xml)
        ns = {"t": windows_schedule.TASK_NAMESPACE}
        self.assertIsNotNone(task.find(".//t:DaysOfWeek/t:Tuesday", ns))
        self.assertEqual(task.findtext(".//t:StartBoundary", namespaces=ns), next_tuesday.isoformat())
        self.assertEqual(
            windows_schedule.next_occurrence("monthly", time(9), now, day_of_month=12).date(),
            date(2026, 12, 12),
        )

    def test_disabling_a_source_changes_its_task_and_preserves_its_preferred_period(self):
        with (
            TemporaryDirectory(dir=Path(__file__).parent) as directory,
            patch.object(panel_settings, "STATE_DIR", Path(directory)),
            patch.object(panel_settings, "schedule_exists", return_value=True),
            patch.object(panel_settings, "remove_schedule") as remove,
            patch.object(panel_settings, "register_schedule") as register,
        ):
            selection = panel_settings.Schedule("weekly", time(10), "week", 2, 1, False)
            panel_settings.save_schedule("sales", selection)
            remove.assert_called_once_with("sales")
            register.assert_not_called()
            self.assertEqual(panel_settings.get_schedule("sales"), selection)

    def test_current_month_keeps_only_completed_days(self):
        self.assertEqual(
            export_jobs.period_dates("current_month", date(2026, 11, 24)),
            (date(2026, 11, 1), date(2026, 11, 23)),
        )
        self.assertEqual(
            export_jobs.period_dates("current_month", date(2026, 12, 1)),
            (date(2026, 11, 1), date(2026, 11, 30)),
        )

    def test_reboot_resume_runs_the_saved_period_only_once(self):
        now = datetime(2026, 11, 24, 10, tzinfo=MOSCOW_TIME)
        yesterday = date(2026, 11, 23)
        with TemporaryDirectory(dir=Path(__file__).parent) as folder, patch.object(export_jobs, "STATE_DIR", Path(folder)), patch.object(
            export_jobs, "datetime", wraps=datetime,
        ) as clock, patch.object(export_jobs, "run_loader", return_value=0) as loader:
            clock.now.return_value = now
            requested = export_jobs.Request("sales", yesterday, yesterday)
            export_jobs.write_object(export_jobs.state_file("sales"), {
                **requested.fields(), "state": "running", "started": now.isoformat(), "due": None,
            })
            installed = datetime(2026, 11, 23, 8, tzinfo=MOSCOW_TIME)
            for _ in range(2):
                self.assertEqual(export_jobs.scheduled("sales", "day", "daily", time(9), installed), 0)
            loader.assert_called_once_with(requested, resume=True)
            self.assertEqual(export_jobs.read_object(export_jobs.state_file("sales"))["state"], "done")

    def test_manual_report_follows_success_and_keeps_the_requested_dates(self):
        now = datetime(2026, 11, 24, 10, tzinfo=MOSCOW_TIME)
        request = export_jobs.Request("sales", date(2026, 11, 16), date(2026, 11, 22))
        with (
            TemporaryDirectory(dir=Path(__file__).parent) as folder,
            patch.object(export_jobs, "STATE_DIR", Path(folder)),
            patch.object(export_jobs, "datetime", wraps=datetime) as clock,
            patch.object(export_jobs, "run_loader", side_effect=[1, 0]),
            patch.object(export_jobs, "open_report") as report,
        ):
            clock.now.return_value = now
            self.assertEqual(export_jobs.execute(request, show_report=True), 1)
            report.assert_not_called()
            self.assertEqual(export_jobs.execute(request, show_report=True), 0)
            report.assert_called_once_with("sales", request.first, request.last)


class ReportPreviewTests(unittest.TestCase):
    def test_daily_overview_has_a_separate_cancellation_card(self):
        visuals = (
            report_preview.RESOURCE_ROOT / "powerbi" / report_preview.REPORT / "definition" / "pages"
            / report_preview.DAILY / "visuals"
        )
        cards = []
        for path in visuals.glob("*/visual.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            visual = document["visual"]
            position = document["position"]
            if visual["visualType"] == "cardVisual" and position["y"] == 322:
                field = visual["query"]["queryState"]["Data"]["projections"][0]["nativeQueryRef"]
                cards.append((position["x"], position["x"] + position["width"], field))

        cards.sort()
        self.assertEqual(
            [field for _, _, field in cards],
            ["Заказы, шт.", "Отмены заказов, шт.", "Продажи, шт.", "Возвраты, шт."],
        )
        for (_, right, _), (next_left, _, _) in zip(cards, cards[1:]):
            self.assertLess(right, next_left)

    def test_funnel_table_fits_product_labels_and_buyout_rate(self):
        path = (
            report_preview.RESOURCE_ROOT / "powerbi" / report_preview.REPORT / "definition" / "pages"
            / report_preview.FUNNEL / "visuals" / "bfa12d0e35614ef491ad" / "visual.json"
        )
        table = json.loads(path.read_text(encoding="utf-8"))
        visual = table["visual"]
        fields = [column["queryRef"] for column in visual["query"]["queryState"]["Values"]["projections"]]
        widths = {
            column["selector"]["metadata"]: int(column["properties"]["value"]["expr"]["Literal"]["Value"][:-1])
            for column in visual["objects"]["columnWidth"]
        }
        self.assertIn("Воронка.Товар", fields)
        self.assertIn("Воронка.Доля выкупа", fields)
        self.assertEqual(list(widths), fields)
        self.assertLessEqual(sum(widths.values()), table["position"]["width"] - 24)

    def test_actual_coverage_controls_each_page_and_skips_empty_presets(self):
        first, last = date(2026, 1, 1), date(2026, 11, 30)
        with TemporaryDirectory(dir=Path(__file__).parent) as root:
            output = report_preview.prepare_report(
                "orders", first, last, output_root=Path(root), all_pages=True,
                date_ranges={
                    report_preview.DAILY: (date(2026, 7, 29), date(2026, 11, 25)),
                    report_preview.FINANCE: (date(2026, 1, 2), date(2026, 11, 22)),
                },
                funnel_months=["2026-01", "2026-03"],
            )
            pages = output.parent / report_preview.REPORT / "definition" / "pages"

            def visual(page, name):
                return json.loads((pages / page / "visuals" / name / "visual.json").read_text(encoding="utf-8"))

            for page, expected in (
                (report_preview.DAILY, ("2026-07-29", "2026-11-25")),
                (report_preview.FINANCE, ("2026-01-02", "2026-11-22")),
            ):
                slicer, _, _ = report_preview.DATE_SLICERS[page]
                dates = visual(page, slicer)["visual"]["objects"]["data"][0]["properties"]
                self.assertEqual(dates["startDate"]["expr"]["Literal"]["Value"], f"datetime'{expected[0]}T00:00:00'")
                self.assertEqual(dates["endDate"]["expr"]["Literal"]["Value"], f"datetime'{expected[1]}T00:00:00'")
            stocks = visual(report_preview.STOCKS, report_preview.DATE_SLICERS[report_preview.STOCKS][0])
            self.assertNotIn("startDate", stocks["visual"]["objects"]["data"][0]["properties"])
            self.assertTrue(stocks["filterConfig"]["filters"][1]["isLockedInViewMode"])
            funnel = visual(report_preview.FUNNEL, report_preview.FUNNEL_SLICER)
            month_filter = funnel["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]
            values = month_filter["Where"][0]["Condition"]["In"]["Values"]
            self.assertEqual([item[0]["Literal"]["Value"] for item in values], ["'2026-01'", "'2026-03'"])

    def test_copied_report_opens_with_both_date_bounds_and_a_live_model(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as root:
            output = report_preview.prepare_report("sales", date(2026, 11, 16), date(2026, 11, 22), output_root=Path(root))
            pages = output.parent / report_preview.REPORT / "definition" / "pages"
            manifest = json.loads((pages / "pages.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["activePageName"], report_preview.DAILY)
            slicer_name, _, _ = report_preview.DATE_SLICERS[report_preview.DAILY]
            slicer = (pages / report_preview.DAILY / "visuals" / slicer_name / "visual.json").read_text(encoding="utf-8")
            self.assertIn("datetime'2026-11-16T00:00:00'", slicer)
            self.assertIn("datetime'2026-11-22T00:00:00'", slicer)
            model = output.parent / report_preview.MODEL
            activity = (model / "definition" / "tables" / "Активность.tmdl").read_text(encoding="utf-8")
            self.assertIn("mode: directQuery", activity)
            self.assertFalse((model / ".pbi").exists())

    def test_report_connection_uses_only_local_server_and_database(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as root:
            output = report_preview.prepare_report("sales", None, None, output_root=Path(root))
            params = output.parent / report_preview.MODEL / "definition" / "expressions.tmdl"
            original = params.read_text(encoding="utf-8")
            self.assertIn('expression PgServer = "" meta', original)
            self.assertIn('expression PgDatabase = "" meta', original)
            report_preview._set_database_parameters(output, {
                "host": "::1", "port": 6543, "dbname": 'demo "#(db)',
                "user": "fixture_user", "password": "fixture_password",
            })
            updated = params.read_text(encoding="utf-8")
            self.assertIn('expression PgServer = "localhost:6543" meta', updated)
            self.assertIn('expression PgDatabase = "demo ""#(#)(db)" meta', updated)
            self.assertNotIn("fixture_user", updated)
            self.assertNotIn("fixture_password", updated)

    def test_demo_copies_only_definitions_and_uses_the_synthetic_period(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as root:
            output = report_demo.prepare_demo(output_root=Path(root))
            model = output.parent / report_preview.MODEL
            self.assertFalse((model / ".pbi").exists())
            expressions = (model / "definition" / "expressions.tmdl").read_text(encoding="utf-8")
            self.assertIn("expression DemoMode = true", expressions)
            page = report_preview.FINANCE
            slicer_name, _, _ = report_preview.DATE_SLICERS[page]
            slicer = output.parent / report_preview.REPORT / "definition" / "pages" / page / "visuals" / slicer_name / "visual.json"
            self.assertIn("datetime'2025-02-10T00:00:00'", slicer.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
