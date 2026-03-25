from __future__ import annotations

import math
import threading
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import font as tkfont
from typing import Any

APP_BG = "#07111f"
SURFACE_BG = "#0f1b2d"
SURFACE_ALT = "#13223a"
SURFACE_SOFT = "#182a45"
BORDER = "#223553"
GRID = "#243754"
TEXT_PRIMARY = "#f8fafc"
TEXT_SECONDARY = "#cbd5e1"
TEXT_MUTED = "#8ea3bd"
CHIP_BG = "#13263c"
CHIP_ACTIVE_BG = "#103021"
CHIP_IDLE_BG = "#3a2514"
KEYBOARD_COLOR = "#60a5fa"
MOUSE_COLOR = "#34d399"
LEFT_COLOR = "#60a5fa"
RIGHT_COLOR = "#f59e0b"
MIDDLE_COLOR = "#a78bfa"
TRACK_COLOR = "#1a2b43"
EMPTY_TEXT = "#6b7f99"


def coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: str) -> str:
    dt = parse_iso_datetime(value)
    if dt is None:
        return "暂无"
    return dt.astimezone().strftime("%m-%d %H:%M:%S")


def format_short_timestamp(value: str) -> str:
    dt = parse_iso_datetime(value)
    if dt is None:
        return "暂无"
    return dt.astimezone().strftime("%m-%d %H:%M")


def format_relative_time(value: str, now: datetime | None = None) -> str:
    dt = parse_iso_datetime(value)
    if dt is None:
        return "暂无"
    active_now = now or datetime.now(timezone.utc)
    seconds = max(0, int((active_now - dt).total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


def format_duration(value: str, now: datetime | None = None) -> str:
    dt = parse_iso_datetime(value)
    if dt is None:
        return "暂无"
    active_now = now or datetime.now(timezone.utc)
    seconds = max(0, int((active_now - dt).total_seconds()))
    if seconds < 60:
        return "刚开始"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours} 小时 {minutes} 分"
    if hours:
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def elapsed_minutes(value: str, now: datetime | None = None) -> float:
    dt = parse_iso_datetime(value)
    if dt is None:
        return 0.0
    active_now = now or datetime.now(timezone.utc)
    return max(0.0, (active_now - dt).total_seconds() / 60.0)


def format_rate(count: int, minutes: float) -> str:
    if count <= 0 or minutes <= 0:
        return "0 次/分钟"
    rate = count / minutes
    if rate >= 10:
        return f"{rate:.1f} 次/分钟"
    return f"{rate:.2f} 次/分钟"


def build_24h_series(snapshot: dict[str, Any], now: datetime | None = None) -> list[dict[str, Any]]:
    active_now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    buckets: dict[str, dict[str, int]] = {}
    for item in snapshot.get("history_minutes", []):
        if not isinstance(item, dict):
            continue
        bucket_start = item.get("bucket_start_utc")
        if not isinstance(bucket_start, str):
            continue
        buckets[bucket_start] = {
            "keyboard_presses": coerce_int(item.get("keyboard_presses")),
            "mouse_clicks_total": coerce_int(item.get("mouse_clicks_total")),
            "mouse_clicks_left": coerce_int(item.get("mouse_clicks_left")),
            "mouse_clicks_right": coerce_int(item.get("mouse_clicks_right")),
            "mouse_clicks_middle": coerce_int(item.get("mouse_clicks_middle")),
        }

    series: list[dict[str, Any]] = []
    start = active_now - timedelta(hours=24) + timedelta(minutes=1)
    for index in range(24 * 60):
        bucket_time = start + timedelta(minutes=index)
        key = bucket_time.isoformat(timespec="seconds")
        values = buckets.get(key, {})
        series.append(
            {
                "bucket_start_utc": key,
                "label": bucket_time.astimezone().strftime("%H:%M"),
                "keyboard_presses": values.get("keyboard_presses", 0),
                "mouse_clicks_total": values.get("mouse_clicks_total", 0),
                "mouse_clicks_left": values.get("mouse_clicks_left", 0),
                "mouse_clicks_right": values.get("mouse_clicks_right", 0),
                "mouse_clicks_middle": values.get("mouse_clicks_middle", 0),
            }
        )
    return series


def series_peak(series: list[dict[str, Any]], field_name: str) -> tuple[str, int]:
    if not series:
        return ("暂无", 0)
    peak_point = max(series, key=lambda point: coerce_int(point.get(field_name)))
    peak_value = coerce_int(peak_point.get(field_name))
    if peak_value <= 0:
        return ("暂无", 0)
    bucket_dt = parse_iso_datetime(peak_point.get("bucket_start_utc"))
    if bucket_dt is None:
        return (peak_point.get("label", "--:--"), peak_value)
    return (bucket_dt.astimezone().strftime("%H:%M"), peak_value)


def dominant_click(snapshot: dict[str, Any]) -> str:
    options = [
        ("左键", coerce_int(snapshot.get("mouse_clicks_left"))),
        ("右键", coerce_int(snapshot.get("mouse_clicks_right"))),
        ("中键", coerce_int(snapshot.get("mouse_clicks_middle"))),
    ]
    total = sum(value for _, value in options)
    if total <= 0:
        return "暂无点击数据"
    label, value = max(options, key=lambda item: item[1])
    share = value / total * 100.0
    return f"{label} · {value} 次 · {share:.0f}%"


def summarize_snapshot(snapshot: dict[str, Any], series: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    active_now = now or datetime.now(timezone.utc)
    last_event_dt = parse_iso_datetime(snapshot.get("last_event_at_utc"))
    is_active = bool(last_event_dt and (active_now - last_event_dt) <= timedelta(minutes=2))

    session_minutes = elapsed_minutes(snapshot.get("session_started_at_utc", ""), active_now)
    session_keyboard = coerce_int(snapshot.get("session_keyboard_presses"))
    session_mouse = coerce_int(snapshot.get("session_mouse_clicks_total"))
    total_mouse = coerce_int(snapshot.get("mouse_clicks_total"))
    left_clicks = coerce_int(snapshot.get("mouse_clicks_left"))
    right_clicks = coerce_int(snapshot.get("mouse_clicks_right"))
    middle_clicks = coerce_int(snapshot.get("mouse_clicks_middle"))

    keyboard_peak_time, keyboard_peak_value = series_peak(series, "keyboard_presses")
    mouse_peak_time, mouse_peak_value = series_peak(series, "mouse_clicks_total")
    latest_keyboard = coerce_int(series[-1].get("keyboard_presses")) if series else 0
    latest_mouse = coerce_int(series[-1].get("mouse_clicks_total")) if series else 0

    total_clicks = max(total_mouse, 1)
    left_share = left_clicks / total_clicks * 100.0 if total_mouse else 0.0
    right_share = right_clicks / total_clicks * 100.0 if total_mouse else 0.0
    middle_share = middle_clicks / total_clicks * 100.0 if total_mouse else 0.0

    return {
        "is_active": is_active,
        "activity_text": "活跃中" if is_active else "暂时空闲",
        "activity_detail": format_relative_time(snapshot.get("last_event_at_utc", ""), active_now),
        "session_duration": format_duration(snapshot.get("session_started_at_utc", ""), active_now),
        "started_at_text": format_short_timestamp(snapshot.get("started_at_utc", "")),
        "session_started_text": format_short_timestamp(snapshot.get("session_started_at_utc", "")),
        "last_event_text": format_timestamp(snapshot.get("last_event_at_utc", "")),
        "keyboard_rate": format_rate(session_keyboard, session_minutes),
        "mouse_rate": format_rate(session_mouse, session_minutes),
        "keyboard_peak": f"{keyboard_peak_time} · {keyboard_peak_value}/分钟" if keyboard_peak_value else "暂无明显峰值",
        "mouse_peak": f"{mouse_peak_time} · {mouse_peak_value}/分钟" if mouse_peak_value else "暂无明显峰值",
        "keyboard_chart_stat": f"峰值 {keyboard_peak_value}/分钟 · 最新 {latest_keyboard}" if keyboard_peak_value else f"最新 {latest_keyboard}",
        "mouse_chart_stat": f"峰值 {mouse_peak_value}/分钟 · 最新 {latest_mouse}" if mouse_peak_value else f"最新 {latest_mouse}",
        "dominant_click": dominant_click(snapshot),
        "click_mix": f"左 {left_clicks} · 右 {right_clicks} · 中 {middle_clicks}",
        "click_share": f"左 {left_share:.0f}% · 右 {right_share:.0f}% · 中 {middle_share:.0f}%",
        "distribution_summary": f"总点击 {total_mouse} · {dominant_click(snapshot)}" if total_mouse else "等待新的鼠标点击数据",
    }


class CounterDashboard:
    def __init__(self, counter: Any, stop_event: threading.Event) -> None:
        self.counter = counter
        self.stop_event = stop_event
        self.root = tk.Tk()
        self.root.title("键鼠活动仪表盘")
        self.root.geometry("1320x860")
        self.root.minsize(1080, 760)
        self.root.configure(bg=APP_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)

        base_family = tkfont.nametofont("TkDefaultFont").cget("family")
        self.fonts = {
            "hero": tkfont.Font(family=base_family, size=26, weight="bold"),
            "title": tkfont.Font(family=base_family, size=15, weight="bold"),
            "card_value": tkfont.Font(family=base_family, size=24, weight="bold"),
            "insight_value": tkfont.Font(family=base_family, size=14, weight="bold"),
            "body": tkfont.Font(family=base_family, size=11),
            "body_bold": tkfont.Font(family=base_family, size=11, weight="bold"),
            "caption": tkfont.Font(family=base_family, size=10),
            "tiny": tkfont.Font(family=base_family, size=9),
        }

        self.card_vars: dict[str, dict[str, tk.StringVar]] = {}
        self.status_vars: dict[str, tk.StringVar] = {}
        self.status_widgets: dict[str, dict[str, Any]] = {}
        self.chart_stat_vars: dict[str, tk.StringVar] = {}
        self.insight_vars: dict[str, tk.StringVar] = {}
        self.distribution_summary_var = tk.StringVar(value="正在加载分布数据…")
        self.keyboard_canvas: tk.Canvas | None = None
        self.mouse_canvas: tk.Canvas | None = None
        self.distribution_canvas: tk.Canvas | None = None

        self._build_layout()
        self.root.after(120, self.refresh)

    def _surface(self, parent: tk.Widget, bg: str = SURFACE_BG) -> tk.Frame:
        return tk.Frame(parent, bg=bg, bd=0, highlightthickness=1, highlightbackground=BORDER)

    def _build_layout(self) -> None:
        container = tk.Frame(self.root, bg=APP_BG)
        container.pack(fill="both", expand=True, padx=24, pady=24)
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(2, weight=1)
        container.grid_rowconfigure(3, weight=1)

        header = tk.Frame(container, bg=APP_BG)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        header_left = tk.Frame(header, bg=APP_BG)
        header_left.grid(row=0, column=0, sticky="w")
        tk.Label(
            header_left,
            text="键鼠活动仪表盘",
            bg=APP_BG,
            fg=TEXT_PRIMARY,
            font=self.fonts["hero"],
        ).pack(anchor="w")
        tk.Label(
            header_left,
            text="实时会话、累计统计与最近 24 小时趋势 · 仅统计次数，不记录具体按键内容",
            bg=APP_BG,
            fg=TEXT_MUTED,
            font=self.fonts["body"],
        ).pack(anchor="w", pady=(6, 0))

        chips = tk.Frame(header, bg=APP_BG)
        chips.grid(row=0, column=1, sticky="e")
        self._add_status_chip(chips, "activity", "状态", "活跃中", CHIP_ACTIVE_BG, KEYBOARD_COLOR, 0)
        self._add_status_chip(chips, "recent", "最近活动", "刚刚", CHIP_BG, TEXT_SECONDARY, 1)
        self._add_status_chip(chips, "session", "本次会话", "刚开始", CHIP_BG, TEXT_SECONDARY, 2)

        cards = tk.Frame(container, bg=APP_BG)
        cards.grid(row=1, column=0, sticky="ew", pady=(22, 20))
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1)

        self._add_metric_card(cards, 0, "累计键盘", "keyboard_presses", KEYBOARD_COLOR)
        self._add_metric_card(cards, 1, "累计鼠标", "mouse_clicks_total", MOUSE_COLOR)
        self._add_metric_card(cards, 2, "本次键盘", "session_keyboard_presses", KEYBOARD_COLOR)
        self._add_metric_card(cards, 3, "本次鼠标", "session_mouse_clicks_total", MOUSE_COLOR)

        charts = tk.Frame(container, bg=APP_BG)
        charts.grid(row=2, column=0, sticky="nsew")
        charts.grid_columnconfigure(0, weight=1)
        charts.grid_columnconfigure(1, weight=1)
        charts.grid_rowconfigure(0, weight=1)

        keyboard_card = self._chart_card(charts, 0, "24 小时键盘趋势", "每分钟按键次数", "keyboard_chart", KEYBOARD_COLOR)
        keyboard_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.keyboard_canvas = tk.Canvas(keyboard_card, bg=SURFACE_ALT, highlightthickness=0)
        self.keyboard_canvas.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        mouse_card = self._chart_card(charts, 1, "24 小时鼠标趋势", "每分钟点击次数", "mouse_chart", MOUSE_COLOR)
        mouse_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.mouse_canvas = tk.Canvas(mouse_card, bg=SURFACE_ALT, highlightthickness=0)
        self.mouse_canvas.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        bottom = tk.Frame(container, bg=APP_BG)
        bottom.grid(row=3, column=0, sticky="nsew", pady=(20, 0))
        bottom.grid_columnconfigure(0, weight=7)
        bottom.grid_columnconfigure(1, weight=5)
        bottom.grid_rowconfigure(0, weight=1)

        distribution_card = self._surface(bottom)
        distribution_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._card_heading(distribution_card, "点击分布", "左 / 右 / 中键占比", self.distribution_summary_var)
        self.distribution_canvas = tk.Canvas(distribution_card, bg=SURFACE_ALT, highlightthickness=0, height=260)
        self.distribution_canvas.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        insights_card = self._surface(bottom)
        insights_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        heading = tk.Frame(insights_card, bg=SURFACE_BG)
        heading.pack(fill="x", padx=16, pady=(16, 12))
        tk.Label(heading, text="快速洞察", bg=SURFACE_BG, fg=TEXT_PRIMARY, font=self.fonts["title"]).pack(anchor="w")
        tk.Label(heading, text="基于当前快照自动计算", bg=SURFACE_BG, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(anchor="w", pady=(4, 0))

        for key, title, accent in [
            ("session_duration", "会话时长", KEYBOARD_COLOR),
            ("keyboard_rate", "键盘速率", KEYBOARD_COLOR),
            ("mouse_rate", "鼠标速率", MOUSE_COLOR),
            ("keyboard_peak", "键盘峰值", KEYBOARD_COLOR),
            ("mouse_peak", "鼠标峰值", MOUSE_COLOR),
            ("dominant_click", "主力点击", RIGHT_COLOR),
        ]:
            self._add_insight_row(insights_card, key, title, accent)

    def _add_status_chip(
        self,
        parent: tk.Widget,
        key: str,
        title: str,
        initial_value: str,
        bg: str,
        accent: str,
        column: int,
    ) -> None:
        chip = tk.Frame(parent, bg=bg, bd=0, highlightthickness=1, highlightbackground=BORDER)
        chip.grid(row=0, column=column, padx=(0 if column == 0 else 10, 0), sticky="e")
        inner = tk.Frame(chip, bg=bg)
        inner.pack(padx=12, pady=8)

        title_row = tk.Frame(inner, bg=bg)
        title_row.pack(anchor="w")
        tk.Label(title_row, text="●", bg=bg, fg=accent, font=self.fonts["tiny"]).pack(side="left")
        tk.Label(title_row, text=title, bg=bg, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(side="left", padx=(4, 0))

        value_var = tk.StringVar(value=initial_value)
        tk.Label(inner, textvariable=value_var, bg=bg, fg=TEXT_PRIMARY, font=self.fonts["body_bold"]).pack(anchor="w", pady=(4, 0))

        self.status_vars[key] = value_var
        self.status_widgets[key] = {"frame": chip, "inner": inner, "accent": accent, "title": title_row}

    def _add_metric_card(self, parent: tk.Widget, column: int, title: str, key: str, accent: str) -> None:
        card = self._surface(parent)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0))

        top = tk.Frame(card, bg=SURFACE_BG)
        top.pack(fill="x", padx=16, pady=(16, 10))
        tk.Label(top, text="●", bg=SURFACE_BG, fg=accent, font=self.fonts["tiny"]).pack(side="left")
        tk.Label(top, text=title, bg=SURFACE_BG, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(side="left", padx=(6, 0))

        value_var = tk.StringVar(value="0")
        tk.Label(card, textvariable=value_var, bg=SURFACE_BG, fg=TEXT_PRIMARY, font=self.fonts["card_value"]).pack(anchor="w", padx=16)

        subtitle_var = tk.StringVar(value="")
        tk.Label(card, textvariable=subtitle_var, bg=SURFACE_BG, fg=TEXT_SECONDARY, font=self.fonts["body"]).pack(anchor="w", padx=16, pady=(8, 0))

        footer_var = tk.StringVar(value="")
        tk.Label(card, textvariable=footer_var, bg=SURFACE_BG, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(anchor="w", padx=16, pady=(8, 16))

        self.card_vars[key] = {"value": value_var, "subtitle": subtitle_var, "footer": footer_var}

    def _chart_card(self, parent: tk.Widget, column: int, title: str, subtitle: str, stat_key: str, accent: str) -> tk.Frame:
        card = self._surface(parent)
        header = tk.Frame(card, bg=SURFACE_BG)
        header.pack(fill="x", padx=16, pady=(16, 12))

        left = tk.Frame(header, bg=SURFACE_BG)
        left.pack(side="left", fill="x", expand=True)
        top = tk.Frame(left, bg=SURFACE_BG)
        top.pack(anchor="w")
        tk.Label(top, text="●", bg=SURFACE_BG, fg=accent, font=self.fonts["tiny"]).pack(side="left")
        tk.Label(top, text=title, bg=SURFACE_BG, fg=TEXT_PRIMARY, font=self.fonts["title"]).pack(side="left", padx=(6, 0))
        tk.Label(left, text=subtitle, bg=SURFACE_BG, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(anchor="w", pady=(4, 0))

        stat_var = tk.StringVar(value="等待数据")
        tk.Label(header, textvariable=stat_var, bg=SURFACE_BG, fg=TEXT_SECONDARY, font=self.fonts["caption"]).pack(side="right")
        self.chart_stat_vars[stat_key] = stat_var
        return card

    def _card_heading(self, parent: tk.Widget, title: str, subtitle: str, stat_var: tk.StringVar) -> None:
        header = tk.Frame(parent, bg=SURFACE_BG)
        header.pack(fill="x", padx=16, pady=(16, 12))

        left = tk.Frame(header, bg=SURFACE_BG)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title, bg=SURFACE_BG, fg=TEXT_PRIMARY, font=self.fonts["title"]).pack(anchor="w")
        tk.Label(left, text=subtitle, bg=SURFACE_BG, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(anchor="w", pady=(4, 0))
        tk.Label(header, textvariable=stat_var, bg=SURFACE_BG, fg=TEXT_SECONDARY, font=self.fonts["caption"]).pack(side="right")

    def _add_insight_row(self, parent: tk.Widget, key: str, title: str, accent: str) -> None:
        row = tk.Frame(parent, bg=SURFACE_ALT, bd=0, highlightthickness=1, highlightbackground=BORDER)
        row.pack(fill="x", padx=16, pady=(0, 10))

        content = tk.Frame(row, bg=SURFACE_ALT)
        content.pack(fill="x", padx=12, pady=10)
        label_row = tk.Frame(content, bg=SURFACE_ALT)
        label_row.pack(fill="x")
        tk.Label(label_row, text="●", bg=SURFACE_ALT, fg=accent, font=self.fonts["tiny"]).pack(side="left")
        tk.Label(label_row, text=title, bg=SURFACE_ALT, fg=TEXT_MUTED, font=self.fonts["caption"]).pack(side="left", padx=(6, 0))

        value_var = tk.StringVar(value="--")
        tk.Label(content, textvariable=value_var, bg=SURFACE_ALT, fg=TEXT_PRIMARY, font=self.fonts["insight_value"], anchor="w").pack(fill="x", pady=(6, 0))
        self.insight_vars[key] = value_var

    def _handle_close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    def refresh(self) -> None:
        if self.stop_event.is_set():
            if self.root.winfo_exists():
                self.root.destroy()
            return

        snapshot = self.counter.snapshot()
        now = datetime.now(timezone.utc)
        series = build_24h_series(snapshot, now)
        summary = summarize_snapshot(snapshot, series, now)

        self._update_status_chips(summary)
        self._update_cards(snapshot, summary)
        self.chart_stat_vars["keyboard_chart"].set(summary["keyboard_chart_stat"])
        self.chart_stat_vars["mouse_chart"].set(summary["mouse_chart_stat"])
        self.distribution_summary_var.set(summary["distribution_summary"])

        self.insight_vars["session_duration"].set(summary["session_duration"])
        self.insight_vars["keyboard_rate"].set(summary["keyboard_rate"])
        self.insight_vars["mouse_rate"].set(summary["mouse_rate"])
        self.insight_vars["keyboard_peak"].set(summary["keyboard_peak"])
        self.insight_vars["mouse_peak"].set(summary["mouse_peak"])
        self.insight_vars["dominant_click"].set(summary["dominant_click"])

        if self.keyboard_canvas is not None:
            self._draw_line_chart(self.keyboard_canvas, series, "keyboard_presses", KEYBOARD_COLOR)
        if self.mouse_canvas is not None:
            self._draw_line_chart(self.mouse_canvas, series, "mouse_clicks_total", MOUSE_COLOR)
        if self.distribution_canvas is not None:
            self._draw_distribution(snapshot)

        self.root.after(500, self.refresh)

    def _update_status_chips(self, summary: dict[str, Any]) -> None:
        self.status_vars["activity"].set(summary["activity_text"])
        self.status_vars["recent"].set(summary["activity_detail"])
        self.status_vars["session"].set(summary["session_duration"])

        activity_bg = CHIP_ACTIVE_BG if summary["is_active"] else CHIP_IDLE_BG
        chip = self.status_widgets["activity"]["frame"]
        chip.configure(bg=activity_bg)
        for child in chip.winfo_children():
            child.configure(bg=activity_bg)
            for nested in child.winfo_children():
                nested.configure(bg=activity_bg)

    def _update_cards(self, snapshot: dict[str, Any], summary: dict[str, Any]) -> None:
        self.card_vars["keyboard_presses"]["value"].set(f"{coerce_int(snapshot.get('keyboard_presses')):,}")
        self.card_vars["keyboard_presses"]["subtitle"].set(f"自 {summary['started_at_text']} 起累计")
        self.card_vars["keyboard_presses"]["footer"].set(f"24 小时峰值：{summary['keyboard_peak']}")

        self.card_vars["mouse_clicks_total"]["value"].set(f"{coerce_int(snapshot.get('mouse_clicks_total')):,}")
        self.card_vars["mouse_clicks_total"]["subtitle"].set(f"自 {summary['started_at_text']} 起累计")
        self.card_vars["mouse_clicks_total"]["footer"].set(summary["click_mix"])

        self.card_vars["session_keyboard_presses"]["value"].set(f"{coerce_int(snapshot.get('session_keyboard_presses')):,}")
        self.card_vars["session_keyboard_presses"]["subtitle"].set(f"会话开始：{summary['session_started_text']}")
        self.card_vars["session_keyboard_presses"]["footer"].set(f"平均速率：{summary['keyboard_rate']}")

        self.card_vars["session_mouse_clicks_total"]["value"].set(f"{coerce_int(snapshot.get('session_mouse_clicks_total')):,}")
        self.card_vars["session_mouse_clicks_total"]["subtitle"].set(f"最近活动：{summary['last_event_text']}")
        self.card_vars["session_mouse_clicks_total"]["footer"].set(summary["click_share"])

    def _draw_line_chart(self, canvas: tk.Canvas, series: list[dict[str, Any]], field_name: str, color: str) -> None:
        canvas.delete("all")
        width = max(canvas.winfo_width(), 360)
        height = max(canvas.winfo_height(), 240)
        padding_left = 48
        padding_right = 18
        padding_top = 20
        padding_bottom = 32
        plot_width = max(1, width - padding_left - padding_right)
        plot_height = max(1, height - padding_top - padding_bottom)

        values = [coerce_int(point.get(field_name)) for point in series]
        max_value = max(values) if values else 0

        if max_value <= 0:
            self._draw_empty_state(canvas, width, height, "最近 24 小时暂无活动", "开始使用后，这里会显示分钟级趋势")
            return

        top_value = self._nice_axis_ceiling(max_value)

        for ratio in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = padding_top + plot_height * ratio
            canvas.create_line(padding_left, y, width - padding_right, y, fill=GRID, width=1)
            label = round(top_value * (1 - ratio))
            canvas.create_text(
                padding_left - 10,
                y,
                text=str(label),
                fill=TEXT_MUTED,
                anchor="e",
                font=self.fonts["tiny"],
            )

        for fraction in (0.0, 0.333, 0.666, 1.0):
            x = padding_left + plot_width * fraction
            canvas.create_line(x, padding_top, x, height - padding_bottom, fill=GRID, width=1, dash=(2, 4))

        step_x = plot_width / max(1, len(values) - 1)
        points: list[float] = []
        for index, value in enumerate(values):
            x = padding_left + index * step_x
            y = padding_top + plot_height - (value / top_value) * plot_height
            points.extend([x, y])

        if len(points) >= 4:
            canvas.create_line(*points, fill=color, width=3, smooth=True, splinesteps=32)

        peak_index = max(range(len(values)), key=values.__getitem__)
        peak_value = values[peak_index]
        peak_x = padding_left + peak_index * step_x
        peak_y = padding_top + plot_height - (peak_value / top_value) * plot_height
        canvas.create_oval(peak_x - 4, peak_y - 4, peak_x + 4, peak_y + 4, fill=color, outline="")
        canvas.create_text(
            peak_x,
            max(padding_top + 10, peak_y - 12),
            text=str(peak_value),
            fill=TEXT_SECONDARY,
            anchor="s",
            font=self.fonts["tiny"],
        )

        last_x = padding_left + (len(values) - 1) * step_x
        last_y = padding_top + plot_height - (values[-1] / top_value) * plot_height
        canvas.create_oval(last_x - 6, last_y - 6, last_x + 6, last_y + 6, fill=APP_BG, outline=color, width=2)
        canvas.create_oval(last_x - 3, last_y - 3, last_x + 3, last_y + 3, fill=color, outline="")

        label_indices = [0, len(series) // 3, (len(series) * 2) // 3, len(series) - 1]
        seen: set[int] = set()
        for index in label_indices:
            if index in seen or index >= len(series):
                continue
            seen.add(index)
            x = padding_left + index * step_x
            canvas.create_text(
                x,
                height - 12,
                text=series[index]["label"],
                fill=TEXT_MUTED,
                anchor="center",
                font=self.fonts["tiny"],
            )

    def _draw_distribution(self, snapshot: dict[str, Any]) -> None:
        canvas = self.distribution_canvas
        if canvas is None:
            return

        canvas.delete("all")
        width = max(canvas.winfo_width(), 360)
        height = max(canvas.winfo_height(), 220)
        values = [
            ("左键", coerce_int(snapshot.get("mouse_clicks_left")), LEFT_COLOR),
            ("右键", coerce_int(snapshot.get("mouse_clicks_right")), RIGHT_COLOR),
            ("中键", coerce_int(snapshot.get("mouse_clicks_middle")), MIDDLE_COLOR),
        ]
        total = sum(value for _, value, _ in values)

        if total <= 0:
            self._draw_empty_state(canvas, width, height, "暂无点击分布", "开始点击鼠标后，这里会显示左 / 右 / 中键占比")
            return

        label_x = 24
        bar_start_x = 140
        bar_end_x = width - 110
        top_y = 56
        row_gap = 68
        bar_height = 16

        for index, (label, value, color) in enumerate(values):
            share = value / total
            y = top_y + index * row_gap
            canvas.create_text(label_x, y - 10, text=label, fill=TEXT_PRIMARY, anchor="w", font=self.fonts["body_bold"])
            canvas.create_text(label_x, y + 12, text=f"{share * 100:.0f}%", fill=TEXT_MUTED, anchor="w", font=self.fonts["caption"])
            canvas.create_rectangle(
                bar_start_x,
                y - bar_height / 2,
                bar_end_x,
                y + bar_height / 2,
                fill=TRACK_COLOR,
                outline=TRACK_COLOR,
            )
            fill_width = max(4.0 if value > 0 else 0.0, (bar_end_x - bar_start_x) * share)
            canvas.create_rectangle(
                bar_start_x,
                y - bar_height / 2,
                bar_start_x + fill_width,
                y + bar_height / 2,
                fill=color,
                outline=color,
            )
            canvas.create_text(width - 24, y, text=f"{value} 次", fill=TEXT_SECONDARY, anchor="e", font=self.fonts["body"])

    def _draw_empty_state(self, canvas: tk.Canvas, width: int, height: int, title: str, subtitle: str) -> None:
        canvas.create_rectangle(28, 28, width - 28, height - 28, outline=GRID, width=1)
        canvas.create_text(width / 2, height / 2 - 8, text=title, fill=TEXT_SECONDARY, font=self.fonts["body_bold"])
        canvas.create_text(width / 2, height / 2 + 16, text=subtitle, fill=EMPTY_TEXT, font=self.fonts["caption"])

    def _nice_axis_ceiling(self, value: int) -> int:
        if value <= 10:
            return 10
        if value <= 50:
            return int(math.ceil(value / 5.0) * 5)
        if value <= 200:
            return int(math.ceil(value / 10.0) * 10)
        return int(math.ceil(value / 25.0) * 25)


def run_dashboard(counter: Any, stop_event: threading.Event) -> None:
    dashboard = CounterDashboard(counter, stop_event)
    dashboard.root.mainloop()
