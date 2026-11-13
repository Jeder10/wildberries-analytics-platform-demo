"""Windows control panel for local Wildberries refresh jobs."""

import ctypes
from datetime import date, datetime, time
from queue import Empty, SimpleQueue
import re
import subprocess
from threading import Thread
import tkinter as tk
from tkinter import ttk

import psycopg

from .app_paths import APP_ROOT, RESOURCE_ROOT, worker_command, worker_environment
from .export_jobs import (
    PERIODS, SNAPSHOTS, SOURCES, Request, job_lock, log_file, period_dates, planned_request, read_object, state_file,
)
from .panel_icon import apply_window_icons, release_window_icons
from .panel_settings import Schedule, get_schedule, save_schedule, source_periods
from .report_preview import open_report
from .wb_cards import CardCheckError
from .wb_sales import MOSCOW_TIME
from .windows_schedule import FREQUENCIES, WEEKDAYS, next_occurrence


CANVAS = "#EDF1F7"
SURFACE = "#FFFFFF"
INK = "#18203B"
MUTED = "#5A6580"
DIVIDER = "#DDE4F0"
HEADER = "#191A35"
ACCENT = "#4935A0"
TINT = "#EEEAFE"
MINT = "#CBF4E6"
FONT = "Segoe UI"
ICON = RESOURCE_ROOT / "assets" / "wildberries-analytics.ico"
CUSTOM = "custom"
SOURCE_ORDER = ("cards", "orders", "sales", "stocks", "funnel", "finance")
GLYPHS = {"cards": "▦", "orders": "≡", "sales": "↗", "stocks": "▤", "funnel": "◫", "finance": "₽"}
DESCRIPTIONS = {
    "cards": "Каталог карточек и артикулов на момент запуска.",
    "orders": "Изменения заказов с начальной даты. В Power BI применится выбранный диапазон.",
    "sales": "Изменения продаж и возвратов с начальной даты. В Power BI применится выбранный диапазон.",
    "stocks": "Снимок остатков по товарам на момент запуска.",
    "funnel": "Заказы, выкупы и отмены по месяцу оформления.",
    "finance": "Детализация реализации по завершённым неделям.",
}
STATE_APPEARANCE = {
    "running": ("Обновляется", "#8B5021", "#FFF0D9"),
    "waiting": ("Ожидает окна API", "#8B5021", "#FFF0D9"),
    "done": ("Завершено", "#145D50", "#DDF4EB"),
    "failed": ("Ошибка", "#9A3D42", "#FBE9E9"),
    "report_error": ("Данные готовы", "#8B5021", "#FFF0D9"),
    "interrupted": ("Прервано", "#8B5021", "#FFF0D9"),
    "ready": ("Готов к запуску", MUTED, CANVAS),
}


def label(parent, text="", *, size=10, weight="normal", fg=INK, bg=SURFACE, **options):
    return tk.Label(parent, text=text, font=(FONT, size, weight), fg=fg, bg=bg, anchor="w", **options)


def button(parent, text, command, *, kind="secondary"):
    colors = {
        "primary": (ACCENT, SURFACE, "#3C2A8A"),
        "secondary": (CANVAS, INK, "#E2E8F5"),
        "selected": (TINT, ACCENT, "#E4DDFE"),
        "link": (SURFACE, ACCENT, TINT),
    }
    bg, fg, highlight = colors[kind]
    return tk.Button(
        parent, text=text, command=command, font=(FONT, 10, "bold"), relief="flat", bd=0,
        bg=bg, fg=fg, activebackground=highlight, activeforeground=fg, disabledforeground="#7E879D",
        cursor="hand2", padx=13, pady=8, takefocus=True,
    )


def read_tail(source):
    try:
        with log_file(source).open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 8500))
            return stream.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def retry_message(log):
    lines = [line for line in log.splitlines() if line.strip()]
    if lines and re.match(r"Следующ(?:ий|ая) (?:запрос|попытка) ", lines[-1]):
        return lines[-1]
    return ""


def date_edit_allowed(proposed, action, change):
    if action == "1" and re.fullmatch(r"[0-9]+|[0-9]{4}-[0-9]{2}-[0-9]{2}", change) is None:
        return False
    return re.fullmatch(r"[0-9-]{0,10}", proposed) is not None and len(proposed.replace("-", "")) <= 8


def format_date_field(value):
    entered = value.get()
    try:
        formatted = date.fromisoformat(entered).isoformat()
    except ValueError:
        return
    if formatted != entered:
        value.set(formatted)


class Panel(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Выгрузки Wildberries")
        self.iconbitmap(str(ICON))
        self.iconbitmap(default=str(ICON))
        self.native_icons = ()
        self.geometry("1120x732")
        self.minsize(1000, 680)
        self.configure(bg=CANVAS)
        self.active_source = "sales"
        self.tab = "manual"
        self.manual_period = "week"
        self.checked = {source: tk.BooleanVar(value=False) for source in SOURCE_ORDER}
        self.schedules = {source: get_schedule(source) for source in SOURCE_ORDER}
        self.processes = {}
        self.preview_messages = SimpleQueue()
        self.preview_running = False
        self.first, self.last = tk.StringVar(), tk.StringVar()
        self.frequency, self.clock = tk.StringVar(), tk.StringVar()
        self.weekday, self.monthday, self.auto_period = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.enabled = tk.BooleanVar()
        self.message, self.schedule_count = tk.StringVar(), tk.StringVar()
        self.source_status = {source: tk.StringVar() for source in SOURCE_ORDER}
        self.status_title, self.status_detail = tk.StringVar(), tk.StringVar()
        self.source_widgets = {}
        self.last_journal = None

        self.configure_controls()
        self.build_shell()
        self.choose_source("sales")
        self.update_idletasks()
        self.native_icons = apply_window_icons(self.winfo_id(), ICON)
        self.poll()

    def destroy(self):
        super().destroy()
        release_window_icons(self.native_icons)
        self.native_icons = ()

    def configure_controls(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Panel.TCombobox", padding=(9, 8), font=(FONT, 11), fieldbackground=SURFACE,
            background=SURFACE, bordercolor=DIVIDER, lightcolor=SURFACE, darkcolor=SURFACE,
            foreground=INK, arrowcolor=ACCENT,
        )
        style.map("Panel.TCombobox", fieldbackground=[("readonly", SURFACE)], foreground=[("readonly", INK)])
        self.option_add("*TCombobox*Listbox.font", (FONT, 11))

    def build_shell(self):
        header = tk.Frame(self, bg=HEADER, padx=27, pady=19)
        header.pack(fill="x")
        label(header, "Wildberries", size=10, weight="bold", fg=MINT, bg=HEADER).pack(anchor="w")
        title_area = tk.Frame(header, bg=HEADER)
        title_area.pack(fill="x", pady=(3, 0))
        label(title_area, "Центр выгрузок", size=23, weight="bold", fg=SURFACE, bg=HEADER).pack(side="left")
        label(title_area, size=10, fg="#E2DDFB", bg=HEADER, textvariable=self.schedule_count).pack(side="right")

        main = tk.Frame(self, bg=CANVAS, padx=21, pady=18)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(main, width=308, bg=SURFACE, highlightbackground=DIVIDER, highlightthickness=1)
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        self.sidebar.pack_propagate(False)
        self.build_sources()
        self.workspace = tk.Frame(main, bg=SURFACE, highlightbackground=DIVIDER, highlightthickness=1)
        self.workspace.grid(row=0, column=1, sticky="nsew")

    def build_sources(self):
        title = tk.Frame(self.sidebar, bg=SURFACE, padx=15)
        title.pack(fill="x", pady=(13, 7))
        label(title, "Источники", size=13, weight="bold").pack(side="left")
        button(title, "Выбрать все", self.select_all, kind="link").pack(side="right")
        for source in SOURCE_ORDER:
            row = tk.Frame(self.sidebar, bg=SURFACE, padx=8, pady=6, highlightthickness=1)
            row.pack(fill="x", padx=10, pady=1)
            checkbox = tk.Checkbutton(
                row, variable=self.checked[source], command=self.update_bulk_button,
                bg=SURFACE, activebackground=SURFACE, selectcolor=SURFACE, highlightthickness=0,
                cursor="hand2", bd=0,
            )
            checkbox.pack(side="left", padx=(0, 4))
            glyph = tk.Label(row, text=GLYPHS[source], width=3, height=1, bg=TINT, fg=ACCENT,
                             font=(FONT, 13, "bold"))
            glyph.pack(side="left", padx=(1, 8))
            detail = tk.Frame(row, bg=SURFACE, cursor="hand2")
            detail.pack(side="left", fill="x", expand=True)
            name = label(detail, SOURCES[source], size=10, weight="bold")
            name.pack(fill="x")
            status = label(detail, textvariable=self.source_status[source], fg=MUTED, size=9)
            status.pack(fill="x", pady=(1, 0))
            for widget in (row, glyph, detail, name, status):
                widget.bind("<Button-1>", lambda _event, selected=source: self.choose_source(selected))
            self.source_widgets[source] = (row, detail, name, status, checkbox)
        self.bulk_button = button(self.sidebar, "Обновить выбранные", self.run_selected, kind="primary")
        self.bulk_button.pack(side="bottom", fill="x", padx=14, pady=13)
        self.update_bulk_button()

    def selected_sources(self):
        return [source for source in SOURCE_ORDER if self.checked[source].get()]

    def select_all(self):
        value = len(self.selected_sources()) != len(SOURCE_ORDER)
        for choice in self.checked.values():
            choice.set(value)
        self.update_bulk_button()

    def update_bulk_button(self):
        count = len(self.selected_sources())
        self.bulk_button.configure(
            text=f"Обновить выбранные ({count})" if count else "Обновить выбранные",
            state="normal" if count else "disabled",
        )

    def choose_source(self, source):
        self.active_source = source
        self.manual_period = "current_month" if source == "funnel" else "week"
        self.set_period_dates()
        self.fill_schedule(self.schedules[source])
        self.message.set("")
        for key, (row, detail, name, status, checkbox) in self.source_widgets.items():
            background = TINT if key == source else SURFACE
            row.configure(bg=background, highlightbackground="#DED5FA" if key == source else SURFACE)
            for widget in (detail, name, status):
                widget.configure(bg=background)
            checkbox.configure(bg=background, activebackground=background, selectcolor=background)
        self.render()

    def render(self):
        for widget in self.workspace.winfo_children():
            widget.destroy()
        source = self.active_source
        top = tk.Frame(self.workspace, bg=SURFACE, padx=23, pady=18)
        top.pack(fill="x")
        label(top, SOURCES[source], size=20, weight="bold").pack(fill="x")
        label(top, DESCRIPTIONS[source], size=10, fg=MUTED, wraplength=560, justify="left").pack(
            fill="x", pady=(3, 0),
        )
        navigation = tk.Frame(self.workspace, bg=SURFACE, padx=22)
        navigation.pack(fill="x")
        tabs = (("manual", "Ручной запуск"), ("schedule", "Расписание"), ("journal", "Журнал"))
        for key, name in tabs:
            kind = "selected" if key == self.tab else "link"
            button(navigation, name, lambda mode=key: self.open_tab(mode), kind=kind).pack(side="left", padx=(0, 7))
        tk.Frame(self.workspace, bg=DIVIDER, height=1).pack(fill="x", padx=22, pady=(10, 0))

        content = tk.Frame(self.workspace, bg=SURFACE, padx=23, pady=16)
        content.pack(fill="both", expand=True)
        self.last_journal = None
        if self.tab == "manual":
            self.build_manual(content)
        elif self.tab == "schedule":
            self.build_schedule(content)
        else:
            self.build_journal(content)

        footer = tk.Frame(self.workspace, bg=CANVAS, padx=14, pady=9)
        footer.pack(fill="x", padx=22, pady=(0, 13))
        self.badge = label(footer, textvariable=self.status_title, size=10, weight="bold", padx=8, pady=4)
        self.badge.pack(anchor="w")
        label(footer, textvariable=self.status_detail, bg=CANVAS, fg=MUTED, wraplength=570, justify="left").pack(
            fill="x", pady=(5, 0),
        )
        label(self.workspace, textvariable=self.message, fg=ACCENT, padx=22, wraplength=600, justify="left").pack(
            fill="x", pady=(0, 10),
        )
        self.refresh_status()

    def open_tab(self, mode):
        self.tab = mode
        self.message.set("")
        self.render()

    def build_manual(self, content):
        source = self.active_source
        self.run_button = None
        if source not in SNAPSHOTS:
            label(content, "Период в отчёте", size=12, weight="bold").pack(fill="x", pady=(0, 8))
            chips = tk.Frame(content, bg=SURFACE)
            chips.pack(fill="x")
            choices = [(key, PERIODS[key]) for key in source_periods(source)]
            choices.append((CUSTOM, "Свои даты"))
            for key, title in choices:
                kind = "selected" if key == self.manual_period else "secondary"
                button(chips, title, lambda period=key: self.change_period(period), kind=kind).pack(
                    side="left", padx=(0, 6),
                )
            dates = tk.Frame(content, bg=SURFACE)
            dates.pack(fill="x", pady=(15, 13))
            custom_dates = self.manual_period == CUSTOM
            for caption, value in (("Начало", self.first), ("Конец", self.last)):
                section = tk.Frame(dates, bg=SURFACE)
                section.pack(side="left", padx=(0, 12))
                label(section, caption, fg=MUTED, size=9).pack(fill="x", pady=(0, 4))
                field = self.entry(section, value, width=16, manual_date=custom_dates)
                if not custom_dates:
                    field.configure(state="readonly", readonlybackground=CANVAS)
                field.pack()

        actions = tk.Frame(content, bg=SURFACE)
        actions.pack(fill="x", pady=(11, 4))
        self.run_button = button(actions, "Выгрузить и открыть отчёт", self.run_single, kind="primary")
        self.run_button.pack(side="left", padx=(0, 9))
        self.report_button = button(actions, "Открыть сохранённые данные", self.preview)
        self.report_button.pack(side="left")

    def entry(self, parent, value, *, width, manual_date=False):
        field = tk.Entry(
            parent, textvariable=value, width=width, font=(FONT, 11), fg=INK, bg=SURFACE,
            insertbackground=INK, relief="flat", bd=7, highlightbackground=DIVIDER,
            highlightcolor=ACCENT, highlightthickness=1,
        )
        if manual_date:
            field.configure(validate="key", validatecommand=(field.register(date_edit_allowed), "%P", "%d", "%S"))
            field.bind("<FocusOut>", lambda _event: format_date_field(value))
        return field

    def set_period_dates(self):
        if self.active_source in SNAPSHOTS or self.manual_period == CUSTOM:
            return
        first, last = period_dates(self.manual_period, datetime.now(MOSCOW_TIME).date())
        self.first.set(first.isoformat())
        self.last.set(last.isoformat())

    def change_period(self, period):
        self.manual_period = period
        self.set_period_dates()
        self.render()

    def active_request(self, *, loading):
        source = self.active_source
        if source in SNAPSHOTS:
            return Request(source)
        try:
            request = Request(source, date.fromisoformat(self.first.get()), date.fromisoformat(self.last.get()))
        except ValueError:
            raise ValueError("Введите корректные даты в формате ГГГГММДД или ГГГГ-ММ-ДД.") from None
        today = datetime.now(MOSCOW_TIME).date()
        if loading:
            request.check_for_loading(today)
        else:
            request.check(today)
        return request

    def start_job(self, request, *, report=False):
        source = request.source
        if source in self.processes:
            return False
        with job_lock(source) as idle:
            if not idle:
                return False
        args = worker_command("run", source)
        if request.first is not None:
            args.extend(("--from", request.first.isoformat(), "--to", request.last.isoformat()))
        if report:
            args.append("--open-report")
        self.processes[source] = subprocess.Popen(
            args, cwd=APP_ROOT, env=worker_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return True

    def run_single(self):
        try:
            if self.start_job(self.active_request(loading=True), report=True):
                self.message.set("Выгрузка запущена. Power BI откроется после её завершения.")
            else:
                self.message.set("Этот источник уже обновляется.")
        except (OSError, ValueError) as error:
            self.message.set(str(error))
        self.refresh_status()

    def run_selected(self):
        started = 0
        pending = 0
        invalid = []
        today = datetime.now(MOSCOW_TIME).date()
        for source in self.selected_sources():
            try:
                request = planned_request(source, self.schedules[source].period, today)
                request.check_for_loading(today)
                if self.start_job(request):
                    started += 1
                else:
                    pending += 1
            except (ValueError, OSError):
                invalid.append(SOURCES[source])
        note = f"Запущено источников: {started}."
        if pending:
            note += f" Уже в работе: {pending}."
        if invalid:
            note += f" Проверьте настройки: {', '.join(invalid)}."
        self.message.set(note)
        self.refresh_status()

    def preview(self):
        try:
            request = self.active_request(loading=False)
        except ValueError as error:
            self.message.set(str(error))
            return
        self.preview_running = True
        self.message.set("Открываю локальную копию отчёта.")

        def launch():
            try:
                open_report(request.source, request.first, request.last)
                outcome = "Power BI запускается с выбранным периодом."
            except (CardCheckError, KeyError, OSError, ValueError, psycopg.Error):
                outcome = "Power BI не открылся. Проверьте подключение к локальному PostgreSQL."
            self.preview_messages.put(outcome)

        Thread(target=launch, daemon=True).start()
        self.refresh_status()

    def fill_schedule(self, schedule):
        self.enabled.set(schedule.enabled)
        self.frequency.set(FREQUENCIES[schedule.frequency])
        self.clock.set(schedule.clock.isoformat(timespec="minutes"))
        self.weekday.set(tuple(WEEKDAYS.values())[schedule.weekday])
        self.monthday.set(str(schedule.day_of_month))
        self.auto_period.set(PERIODS[schedule.period])

    def combo(self, parent, caption, value, choices, *, width=17):
        section = tk.Frame(parent, bg=SURFACE)
        label(section, caption, fg=MUTED, size=9).pack(fill="x", pady=(0, 4))
        widget = ttk.Combobox(
            section, textvariable=value, values=choices, state="readonly", width=width, style="Panel.TCombobox",
        )
        widget.pack(fill="x")
        return section, widget

    def build_schedule(self, content):
        source = self.active_source
        tk.Checkbutton(
            content, text="Обновлять автоматически", variable=self.enabled, bg=SURFACE, activebackground=SURFACE,
            selectcolor=SURFACE, fg=INK, font=(FONT, 11, "bold"), bd=0, cursor="hand2",
        ).pack(anchor="w", pady=(0, 10))
        settings = tk.Frame(content, bg=SURFACE)
        settings.pack(fill="x")
        group, choice = self.combo(settings, "Частота", self.frequency, list(FREQUENCIES.values()))
        group.grid(row=0, column=0, sticky="w", padx=(0, 13))
        choice.bind("<<ComboboxSelected>>", lambda _event: self.render())
        frequency = self.selected_frequency()
        time_field = tk.Frame(settings, bg=SURFACE)
        time_field.grid(row=0, column=1, sticky="w", padx=(0, 13))
        label(time_field, "Время · МСК", size=9, fg=MUTED).pack(fill="x", pady=(0, 4))
        self.entry(time_field, self.clock, width=9).pack()
        if frequency == "weekly":
            weekday, _ = self.combo(settings, "День недели", self.weekday, list(WEEKDAYS.values()))
            weekday.grid(row=0, column=2, sticky="w")
        elif frequency == "monthly":
            day = tk.Frame(settings, bg=SURFACE)
            day.grid(row=0, column=2, sticky="w")
            label(day, "Число месяца", size=9, fg=MUTED).pack(fill="x", pady=(0, 4))
            self.entry(day, self.monthday, width=9).pack()
        if source not in SNAPSHOTS:
            period, _ = self.combo(
                settings, "Период данных", self.auto_period, [PERIODS[key] for key in source_periods(source)], width=23,
            )
            period.grid(row=1, column=0, columnspan=3, sticky="w", pady=(11, 0))
        actions = tk.Frame(content, bg=SURFACE)
        actions.pack(fill="x", pady=(15, 0))
        button(actions, "Сохранить расписание", self.update_schedule, kind="primary").pack(side="left")

    def selected_frequency(self):
        return next(key for key, text in FREQUENCIES.items() if self.frequency.get() == text)

    def update_schedule(self):
        source = self.active_source
        period = "day" if source in SNAPSHOTS else next(key for key, text in PERIODS.items() if text == self.auto_period.get())
        frequency = self.selected_frequency()
        try:
            schedule = Schedule(
                frequency, time.fromisoformat(self.clock.get()), period,
                tuple(WEEKDAYS.values()).index(self.weekday.get()),
                int(self.monthday.get()) if frequency == "monthly" else self.schedules[source].day_of_month,
                self.enabled.get(),
            )
            save_schedule(source, schedule)
        except ValueError as error:
            self.message.set(str(error))
            return
        except (OSError, subprocess.CalledProcessError):
            self.message.set("Планировщик Windows не сохранил настройки.")
            return
        self.schedules[source] = schedule
        self.message.set("Расписание сохранено." if schedule.enabled else "Автоматическое обновление отключено.")
        self.refresh_status()

    def build_journal(self, content):
        self.journal = tk.Text(
            content, height=9, bg="#F5F7FB", fg=INK, bd=0, padx=11, pady=9, wrap="word",
            font=("Consolas", 10), state="disabled", highlightbackground=DIVIDER, highlightthickness=1,
        )
        scroll = ttk.Scrollbar(content, command=self.journal.yview)
        self.journal.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.journal.pack(fill="both", expand=True)

    def source_state(self, source, log):
        status = read_object(state_file(source))
        with job_lock(source) as idle:
            running = not idle or source in self.processes
        retry = retry_message(log) if running else ""
        state = status.get("state", "ready")
        if running:
            state = "waiting" if retry else "running"
        elif state == "running":
            state = "interrupted"
        return state, status, retry

    def refresh_status(self):
        total = sum(schedule.enabled for schedule in self.schedules.values())
        self.schedule_count.set(f"По расписанию: {total} из {len(SOURCE_ORDER)}")
        active = self.active_source
        active_log = read_tail(active)
        for source, schedule in self.schedules.items():
            state, status, retry = self.source_state(source, active_log if source == active else read_tail(source))
            description, fg, bg = STATE_APPEARANCE[state]
            if state == "ready":
                short = "Автообновление включено" if schedule.enabled else "Ручной запуск"
            else:
                short = description
            self.source_status[source].set(short)
            if source != active:
                continue
            self.status_title.set(description)
            self.badge.configure(fg=fg, bg=bg)
            if retry:
                self.status_detail.set(retry)
            else:
                lines = []
                if status.get("finished"):
                    end = datetime.fromisoformat(status["finished"])
                    lines.append(f"Последний запуск: {end:%d.%m в %H:%M} МСК")
                if schedule.enabled:
                    when = next_occurrence(
                        schedule.frequency, schedule.clock, datetime.now(MOSCOW_TIME),
                        weekday=schedule.weekday, day_of_month=schedule.day_of_month,
                    )
                    lines.append(f"По расписанию: {when:%d.%m в %H:%M} МСК")
                self.status_detail.set("  ·  ".join(lines) or "История запусков из панели появится здесь.")
            if self.tab == "manual":
                self.run_button.configure(state="disabled" if state in {"running", "waiting"} else "normal")
                self.report_button.configure(state="disabled" if self.preview_running else "normal")
        if self.tab == "journal" and active_log != self.last_journal:
            self.journal.configure(state="normal")
            self.journal.delete("1.0", "end")
            self.journal.insert("1.0", active_log or "У этого источника пока нет запусков из панели.")
            self.journal.see("end")
            self.journal.configure(state="disabled")
            self.last_journal = active_log

    def poll(self):
        for source, process in tuple(self.processes.items()):
            code = process.poll()
            if code is not None:
                del self.processes[source]
                if code == 2:
                    self.message.set(f"{SOURCES[source]} уже обновляется.")
                elif code == 0 and not self.processes:
                    self.message.set("Обновление завершено. Результаты доступны в журнале.")
                elif code != 0:
                    self.message.set(f"{SOURCES[source]} · проверьте журнал загрузки.")
        try:
            self.message.set(self.preview_messages.get_nowait())
            self.preview_running = False
        except Empty:
            pass
        self.refresh_status()
        self.after(1400, self.poll)


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Wildberries.Analytics.LocalPanel")
    Panel().mainloop()


if __name__ == "__main__":
    main()
