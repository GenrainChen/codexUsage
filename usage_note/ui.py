"""A local, resizable Windows sticky note for Codex usage."""

from __future__ import annotations

import json
import math
import queue
import re
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk

from usage_note.branding import ASSETS, style_window
from usage_note.account_store import AccountStore
from usage_note.charts import UsageCharts
from usage_note.periods import MODES, move_period, period_bounds, period_buckets, period_models
from usage_note.output import period_output
from usage_note.summary_card import SummaryCard
from usage_note.quotas import HIDDEN_QUOTA_IDS, quota_groups
from usage_note.timebase import BEIJING_TZ, now as note_now, today as note_today


PAPER = "#F7F4EC"
INK = "#292D2A"
MUTED = "#797C72"
ORANGE = "#CA602E"
LINE = "#E2DED3"
CARD = "#EEEADF"
GREEN = "#4E775E"
FONT = "Microsoft YaHei UI"


def summarize_day(models, pricebook):
    """Keep known-price subtotals useful without pricing unknown tokens at zero."""
    categories = {key: {"tokens": 0, "usd": 0.0, "partial": False}
                  for key in ("input", "cached", "output")}
    rows, unknown, subtotal, cache_write = [], [], 0.0, 0
    complete = True
    for model, tokens in models.items():
        quote = pricebook.quote(model, tokens)
        parts = {key: quote.get(key + "_usd") for key in categories}
        known = quote.get("known_usd")
        if known is None:
            known = sum(value for value in parts.values() if value is not None)
        subtotal += known
        partial = quote.get("total_usd") is None
        complete = complete and not partial
        if partial:
            unknown.append(model)
        cache_write += tokens.get("cache_write", 0)
        for key, item in categories.items():
            count = tokens.get(key, 0)
            item["tokens"] += count
            value = parts[key]
            if value is not None:
                item["usd"] += value
            elif count:
                item["partial"] = True
                if key == "input":
                    known_input = quote.get("input_known_usd")
                    if known_input is None:
                        known_input = max(0, known - sum(parts[k] or 0 for k in ("cached", "output")))
                    item["usd"] += known_input
        rows.append({"model": model, "tokens": tokens, "quote": quote,
                     "known_usd": known, "partial": partial,
                     "total_tokens": sum(tokens.get(key, 0) for key in categories)})
    rows.sort(key=lambda row: (-row["total_tokens"], row["model"]))
    return {"categories": categories, "rows": rows, "known_usd": subtotal,
            "fully_priced": complete, "unknown_models": unknown,
            "tokens": sum(item["tokens"] for item in categories.values()),
            "cache_write": cache_write}


def window_label(window):
    minutes = window.get("window_minutes", window.get("windowDurationMins"))
    if minutes is None:
        minutes = window.get("window_duration_mins")
    try:
        minutes = float(minutes)
    except (ValueError, TypeError):
        return "额度窗口"
    if minutes > 0 and minutes % 1440 == 0:
        return f"{minutes / 1440:g} 天"
    if minutes > 0 and minutes % 60 == 0:
        return f"{minutes / 60:g} 小时"
    return f"{minutes:g} 分钟"


def money(value):
    if value is None:
        return "待定价"
    if value == 0:
        return "$0.00"
    if 0 < value < .000001:
        return "<$0.000001"
    if value >= .01:
        return f"${value:,.2f}"
    return f"${value:.6f}".rstrip("0")


def local_time(value, fmt="%m-%d %H:%M"):
    """Format snapshot and reset instants using the note's fixed UTC+8 clock."""
    try:
        parsed = (datetime.fromtimestamp(value, BEIJING_TZ) if isinstance(value, (int, float))
                  else datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BEIJING_TZ))
        return parsed.strftime(fmt)
    except (AttributeError, ValueError, TypeError, OSError, OverflowError):
        return "未知时间"


class UsageNote:
    """Own UI state; only the worker thread calls the read-only usage scanner."""

    def __init__(self, root: tk.Tk, reader, pricebook, settings_path: Path):
        self.root, self.reader, self.pricebook = root, reader, pricebook
        self.settings_path = Path(settings_path)
        self.data = {"days": {}, "hours": {}, "warnings": []}
        self.selected_date = note_today()
        self.follow_today = True
        self._results = queue.Queue()
        self._busy = False
        self._closed = False
        self._after_ids = set()
        self._geometry_save_id = None
        self._settings_error = None
        self._scan_error = None
        self._last_scan = None
        self._selected_model = None
        self._model_rows = []
        self.account_store = AccountStore(self.settings_path.with_name("account-quotas.json"))
        self._selected_account = None
        self._last_current_account = None
        self._account_pages = []
        self._quota_error = None
        settings = self._load_settings()
        self.period = settings.get("period", "day")
        if self.period not in MODES:
            self.period = "day"
        root.title("Codex 用量便签")
        root.configure(bg=PAPER)
        root.minsize(390, 650)
        geometry = settings.get("geometry", "460x800")
        if not isinstance(geometry, str) or not re.fullmatch(r"\d{3,5}x\d{3,5}[+-]\d+[+-]\d+|\d{3,5}x\d{3,5}", geometry):
            geometry = "460x800"
        self._normal_geometry = geometry
        root.geometry(geometry)
        self.topmost = tk.BooleanVar(root, value=bool(settings.get("topmost", True)))
        root.attributes("-topmost", self.topmost.get())
        root.protocol("WM_DELETE_WINDOW", self.close)
        style_window(root, PAPER, INK, LINE)
        self._build()
        self._render()
        root.bind("<Configure>", self._window_configured, add="+")
        self._schedule(80, self._poll)
        self._schedule_auto_refresh()
        self.refresh()

    def _load_settings(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return settings if isinstance(settings, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            self._settings_error = f"设置未能读取：{exc}"
            return {}

    def _window_configured(self, event):
        if (event.widget is not self.root or self._closed
                or self.root.state() != "normal" or not self.root.winfo_ismapped()
                or self.root.winfo_width() < 390 or self.root.winfo_height() < 650):
            return
        geometry = self.root.geometry()
        if geometry == self._normal_geometry:
            return
        self._normal_geometry = geometry
        if self._geometry_save_id is not None:
            self.root.after_cancel(self._geometry_save_id)
            self._after_ids.discard(self._geometry_save_id)
        self._geometry_save_id = self._schedule(400, self._save_window_position)

    def _save_window_position(self):
        self._geometry_save_id = None
        self._save_settings(show_error=False)

    def _save_settings(self, *, show_error=True):
        # Keep normal placement when closing a minimized/maximized window.
        if (self.root.state() == "normal" and self.root.winfo_ismapped()
                and self.root.winfo_width() >= 390 and self.root.winfo_height() >= 650):
            self._normal_geometry = self.root.geometry()
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
            temporary.write_text(json.dumps({"geometry": self._normal_geometry,
                                            "topmost": self.topmost.get(),
                                            "period": self.period}, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.settings_path)
            return True
        except OSError as exc:
            self._settings_error = f"窗口设置未保存：{exc}"
            if show_error:
                messagebox.showwarning("设置未保存", str(exc), parent=self.root)
            else:
                self.warning_label.configure(text="窗口位置未能保存（点击查看）")
            return False

    def _schedule(self, milliseconds, callback):
        def run():
            self._after_ids.discard(identifier)
            if not self._closed:
                callback()
        identifier = self.root.after(milliseconds, run)
        self._after_ids.add(identifier)
        return identifier

    def _label(self, parent, text="", size=10, color=INK, bold=False, bg=None, **kwargs):
        return tk.Label(parent, text=text, bg=bg or parent.cget("bg"), fg=color,
                        font=(FONT, size, "bold" if bold else "normal"), **kwargs)

    def _button(self, parent, text, command, accent=False, **kwargs):
        return tk.Button(parent, text=text, command=command, bg=ORANGE if accent else parent.cget("bg"),
                         fg="#FFFFFF" if accent else INK, activebackground="#B75327" if accent else LINE,
                         activeforeground="#FFFFFF" if accent else INK, font=(FONT, 9), relief="flat",
                         bd=0, padx=8, pady=4, cursor="hand2", highlightthickness=0, **kwargs)

    def _build(self):
        tk.Frame(self.root, bg=ORANGE, height=4).pack(fill="x")
        page = tk.Frame(self.root, bg=PAPER)
        page.pack(fill="both", expand=True, padx=21, pady=(16, 0))
        header = tk.Frame(page, bg=PAPER)
        header.pack(fill="x")
        self.brand_icon = tk.PhotoImage(master=self.root, file=str(ASSETS / "codex-usage-32.png"))
        tk.Label(header, image=self.brand_icon, bg=PAPER, bd=0).pack(side="left", padx=(0, 9))
        self._label(header, "Codex", 21, bold=True).pack(side="left")
        self._label(header, " / 用量便签", 11, MUTED).pack(side="left", pady=(7, 0))
        tk.Checkbutton(header, text="置顶", variable=self.topmost, command=self._toggle_topmost,
                       bg=PAPER, fg=MUTED, activebackground=PAPER, selectcolor=PAPER,
                       font=(FONT, 9), bd=0, highlightthickness=0).pack(side="right")

        switcher = tk.Frame(page, bg=CARD)
        switcher.pack(fill="x", pady=(12, 0))
        self.period_buttons = {}
        for index, (mode, title) in enumerate((("day", "日"), ("week", "周"), ("month", "月"))):
            switcher.grid_columnconfigure(index, weight=1, uniform="period")
            button = self._button(switcher, title, lambda m=mode: self._set_period(m))
            button.grid(row=0, column=index, sticky="ew", padx=2, pady=2)
            self.period_buttons[mode] = button
        navigation = tk.Frame(page, bg=PAPER)
        navigation.pack(fill="x", pady=(7, 9))
        self._button(navigation, "‹", lambda: self._move_date(-1), width=1).pack(side="left")
        self.date_button = self._button(navigation, "", self._choose_date)
        self.date_button.pack(side="left")
        self.next_button = self._button(navigation, "›", lambda: self._move_date(1), width=1)
        self.next_button.pack(side="left")
        self.today_button = self._button(navigation, "今日", self._today)
        self.today_button.pack(side="right")

        self.summary_card = SummaryCard(page, self._label, self._button, money)
        self.summary_card.pack(fill="x")
        self.total_caption = self.summary_card.total_caption
        self.total_tokens = self.summary_card.total_tokens
        self.timezone_label = self.summary_card.timezone_label

        scroll_area = tk.Frame(page, bg=PAPER)
        scroll_area.pack(fill="both", expand=True)
        scroll_area.grid_rowconfigure(0, weight=1)
        scroll_area.grid_columnconfigure(0, weight=1)
        scroll_area.grid_propagate(False)
        self.canvas = tk.Canvas(scroll_area, bg=PAPER, highlightthickness=0, bd=0, height=1)
        self.scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=self.canvas.yview)
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self._update_scrollbar)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.content = tk.Frame(self.canvas, bg=PAPER)
        self._canvas_window = self.canvas.create_window(0, 0, anchor="nw", window=self.content)
        self.content.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self._canvas_window, width=event.width))
        self.root.bind("<MouseWheel>", self._mousewheel)
        self.charts = UsageCharts(self.content, money)
        self.quota_separator = tk.Frame(self.content, bg=LINE, height=1)
        self.quota_separator.pack(fill="x", pady=(7, 8))

        self.quota = tk.Frame(self.content, bg=PAPER)
        self.quota.pack(fill="x")
        self._quota_labels = []

        model_header = tk.Frame(self.content, bg=PAPER)
        model_header.pack(fill="x", pady=(8, 6))
        self.models_caption = self._label(model_header, "模型明细", 10, bold=True)
        self.models_caption.pack(side="left")
        self._button(model_header, "编辑价格 ↗", self._edit_prices).pack(side="right")
        model_navigation = tk.Frame(model_header, bg=PAPER)
        model_navigation.pack(side="right", padx=(4, 6))
        self.model_prev_button = self._button(model_navigation, "‹", lambda: self._move_model(-1), width=1)
        self.model_prev_button.pack(side="left")
        self.model_page_label = self._label(model_navigation, "0 / 0", 8, MUTED)
        self.model_page_label.pack(side="left", padx=2)
        self.model_next_button = self._button(model_navigation, "›", lambda: self._move_model(1), width=1)
        self.model_next_button.pack(side="left")
        self.models_frame = tk.Frame(self.content, bg=PAPER)
        self.models_frame.pack(fill="x")

        footer = tk.Frame(page, bg=PAPER)
        footer.pack(side="bottom", before=header, fill="x", pady=(8, 8))
        tk.Frame(footer, bg=LINE, height=1).pack(fill="x", pady=(0, 6))
        status_row = tk.Frame(footer, bg=PAPER)
        status_row.pack(fill="x")
        self.status_label = self._label(status_row, "读取本机记录…", 8, MUTED, anchor="w")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.refresh_button = self._button(status_row, "刷新", self.refresh)
        self.refresh_button.pack(side="right")
        self.warning_label = self._label(footer, "", 8, ORANGE, anchor="w", justify="left", cursor="hand2")
        self.warning_label.pack(fill="x")
        self.warning_label.bind("<Button-1>", lambda _e: self._show_warnings())
        self._button(footer, "API 标准价估算 · 非订阅账单 ⓘ", self._show_info, anchor="w").pack(fill="x")

    def _update_scrollbar(self, first, last):
        self.scrollbar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            if self.scrollbar.winfo_manager():
                self.scrollbar.grid_remove()
        elif not self.scrollbar.winfo_manager():
            self.scrollbar.grid()

    def _mousewheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget == self.canvas or widget == self.models_frame:
                if self.scrollbar.winfo_manager():
                    self.canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            widget = getattr(widget, "master", None)

    def _toggle_topmost(self):
        self.root.attributes("-topmost", self.topmost.get())
        self._save_settings()

    def _today(self):
        self.follow_today = True
        self.selected_date = note_today()
        self.canvas.yview_moveto(0)
        self._render()

    def _set_period(self, mode):
        self.period = mode
        self.follow_today = period_bounds(self.selected_date, mode) == period_bounds(note_today(), mode)
        self.canvas.yview_moveto(0)
        self._render()
        self._save_settings()

    def _move_date(self, amount):
        self.selected_date = min(note_today(), move_period(self.selected_date, self.period, amount))
        self.follow_today = period_bounds(self.selected_date, self.period) == period_bounds(note_today(), self.period)
        self.canvas.yview_moveto(0)
        self._render()

    def _choose_date(self):
        dialog = self._dialog("选择日期", "320x230")
        title = {"day": "查看哪一天？", "week": "选择周内任意一天", "month": "选择月内任意一天"}[self.period]
        self._label(dialog, title, 15, bold=True).pack(anchor="w", padx=20, pady=(18, 12))
        dates = sorted(set(self.data.get("days", {})) | set(self.data.get("output_days", {}))
                       | {note_today().isoformat()}, reverse=True)
        selection = tk.StringVar(dialog, value=self.selected_date.isoformat())
        entry = ttk.Combobox(dialog, textvariable=selection, values=dates, font=(FONT, 11))
        entry.pack(fill="x", padx=20)
        self._label(dialog, "输入 YYYY-MM-DD，或选择有记录的日期。", 9, MUTED).pack(anchor="w", padx=20, pady=10)
        def select():
            try:
                chosen = date.fromisoformat(selection.get().strip())
                if chosen > note_today():
                    raise ValueError("future date")
            except ValueError:
                messagebox.showerror("日期无效", "请输入不晚于今天的日期，例如 2026-09-13。", parent=dialog)
                return
            self.selected_date = chosen
            self.follow_today = period_bounds(chosen, self.period) == period_bounds(note_today(), self.period)
            self.canvas.yview_moveto(0)
            self._render()
            dialog.destroy()
        self._button(dialog, "查看", select, accent=True).pack(anchor="e", padx=20, pady=10)
        entry.bind("<Return>", lambda _e: select())
        entry.focus_set()

    def _dialog(self, title, geometry):
        dialog = tk.Toplevel(self.root, bg=PAPER)
        dialog.title(title)
        dialog.geometry(geometry)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        style_window(dialog, PAPER, INK, LINE)
        dialog.grab_set()
        return dialog

    def _edit_prices(self, initial_model=None):
        dialog = self._dialog("模型刊例价", "420x600")
        content = tk.Frame(dialog, bg=PAPER, padx=22, pady=18)
        content.pack(fill="both", expand=True)
        self._label(content, "给模型填一张价签", 16, bold=True).pack(anchor="w")
        self._label(content, "美元 / 每百万 token · 保存到本机", 9, MUTED).pack(anchor="w", pady=(5, 15))
        model_names = sorted({name for day in self.data.get("days", {}).values() for name in day}
                             | set(getattr(self.pricebook, "models", {})))
        model_var = tk.StringVar(dialog, value=initial_model or (model_names[0] if model_names else ""))
        self._label(content, "模型名称", 9, MUTED).pack(anchor="w")
        model_entry = ttk.Combobox(content, values=model_names, textvariable=model_var, font=(FONT, 10))
        model_entry.pack(fill="x", pady=(3, 10))
        fields = {}
        fields_frame = tk.Frame(content, bg=PAPER)
        fields_frame.pack(fill="x")
        fields_frame.grid_columnconfigure(1, weight=1)
        for index, (key, title) in enumerate((("input", "普通输入"), ("cached", "缓存读取"), ("output", "输出"), ("cache_write", "缓存写入（选填）"))):
            self._label(fields_frame, title, 10).grid(row=index, column=0, sticky="w", pady=6)
            value = tk.StringVar(dialog)
            fields[key] = value
            ttk.Entry(fields_frame, textvariable=value, width=18, font=(FONT, 10)).grid(row=index, column=1, sticky="ew", padx=(12, 0), pady=6)
        self._label(content, "价格来源 / 备注", 9, MUTED).pack(anchor="w", pady=(12, 4))
        source_var = tk.StringVar(dialog, value="手动设置")
        ttk.Entry(content, textvariable=source_var, font=(FONT, 10)).pack(fill="x")
        source_label = self._label(content, "", 8, MUTED, wraplength=365, justify="left", anchor="w")
        source_label.pack(fill="x", pady=(8, 0))
        self._label(content, f"内置价表核实于 {getattr(self.pricebook, 'updated_at', '') or '未知日期'}", 8, MUTED).pack(anchor="w", pady=(8, 0))
        self._label(content, "历史日期也按当前价表估算。\nFast / Priority 与长上下文未计倍率。", 8, MUTED, justify="left").pack(anchor="w", pady=(4, 0))
        def populate(_event=None):
            quote = self.pricebook.quote(model_var.get().strip(), {})
            rates = quote.get("rates") or {}
            for key, variable in fields.items():
                rate = rates.get(key)
                variable.set("" if rate is None else str(rate))
            source = quote.get("source")
            if isinstance(source, dict):
                source = source.get("url") or source.get("name") or str(source)
            source_var.set(str(source or "手动设置"))
            source_label.configure(text="未知模型请依据实际报价填写。留空写入价时，包含缓存写入的记录会标记为部分估算。")
        model_entry.bind("<<ComboboxSelected>>", populate)
        populate()
        def save():
            model = model_var.get().strip()
            try:
                if not model:
                    raise ValueError("请填写模型名称。")
                rates = {key: float(variable.get()) if variable.get().strip() else None for key, variable in fields.items()}
                if any(rates[key] is None for key in ("input", "cached", "output")):
                    raise ValueError("请填写普通输入、缓存读取和输出价格。")
                if any(value is not None and (value < 0 or not math.isfinite(value)) for value in rates.values()):
                    raise ValueError("价格必须是大于或等于 0 的有限数值。")
                self.pricebook.set_override(model, rates["input"], rates["cached"], rates["output"],
                                            source=source_var.get().strip() or "手动设置", cache_write=rates["cache_write"])
            except (ValueError, TypeError, OSError) as exc:
                messagebox.showerror("价格未保存", str(exc), parent=dialog)
                return
            self._render()
            dialog.destroy()
        buttons = tk.Frame(content, bg=PAPER)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        def restore():
            try:
                self.pricebook.reset_override(model_var.get().strip())
            except (ValueError, OSError) as exc:
                messagebox.showerror("未能恢复内置价", str(exc), parent=dialog)
                return
            populate()
            self._render()
        self._button(buttons, "恢复内置价", restore).pack(side="left")
        self._button(buttons, "保存本地价格", save, accent=True).pack(side="right")

    def _read_account_data(self):
        from usage_note.account import read_account_identity
        from usage_note.account_limits import AccountLimitsError, fetch_account_limits
        current = read_account_identity(self.reader.codex_home)
        key = current.get("key")
        if key:
            try:
                snapshot = fetch_account_limits(self.reader.codex_home)
                if snapshot["key"] != key:
                    raise AccountLimitsError("登录账号已切换，等待下次刷新")
                if not self._closed:
                    self.account_store.remember(snapshot)
                self._quota_error = None
            except (AccountLimitsError, OSError, ValueError) as exc:
                self._quota_error = (str(exc) if isinstance(exc, AccountLimitsError)
                                     else "账号额度快照未能保存")
        elif not key:
            self._quota_error = None
        latest = read_account_identity(self.reader.codex_home)
        if latest.get("key") != key:
            self._quota_error = "登录账号已切换，等待下次刷新"
        return {"current_account": latest,
                "account_pages": self.account_store.pages(latest, note_now()),
                "account_quota_error": self._quota_error}

    def refresh(self):
        if self._busy or self._closed:
            return
        self._busy = True
        self.refresh_button.configure(state="disabled", text="读取中")
        self.status_label.configure(text="正在读取本机用量记录…")
        def worker():
            try:
                data = self.reader.scan()
                data.update(self._read_account_data())
                data["warnings"] = data.get("warnings", []) + self.account_store.warnings
                self._results.put((data, None))
            except Exception as exc:
                self._results.put((None, str(exc)))
        # Keep the owner alive until its bounded quota request runs finally and
        # closes the temporary app-server, even after the Tk window is closed.
        threading.Thread(target=worker, name="codex-usage-reader", daemon=False).start()

    def _poll(self):
        try:
            data, error = self._results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._busy = False
            self.refresh_button.configure(state="normal", text="刷新")
            if error is not None:
                self.status_label.configure(text=f"读取失败 · {self._next_refresh_at:%H:%M} 自动重试")
                self.warning_label.configure(text=f"{error[:46]}（点击查看）")
                self._scan_error = error
            else:
                self._scan_error = None
                self.data = data
                self._last_scan = note_now()
                if self.follow_today:
                    self.selected_date = note_today()
                self._render()
        self._schedule(100, self._poll)

    def _schedule_auto_refresh(self):
        instant = note_now()
        self._next_refresh_at = (instant.replace(second=0, microsecond=0)
                                 + timedelta(minutes=5 - instant.minute % 5))
        delay = math.ceil((self._next_refresh_at - instant).total_seconds() * 1000)
        self._schedule(max(1, delay), self._auto_refresh)

    def _auto_refresh(self):
        due = note_now() >= self._next_refresh_at
        # Recompute from wall time, including after sleep or a clock adjustment.
        # Manual refreshes never reschedule this timer.
        self._schedule_auto_refresh()
        if due:
            self.refresh()

    def _render(self):
        start, end = period_bounds(self.selected_date, self.period)
        current = (start, end) == period_bounds(note_today(), self.period)
        if self.period == "week":
            last = end - timedelta(days=1)
            end_label = f"{last:%m/%d}" if last.year == start.year else f"{last:%Y/%m/%d}"
            date_text = f"{start:%Y/%m/%d} – {end_label} ▾"
        elif self.period == "month":
            date_text = f"{start:%Y 年 %m 月} ▾"
        else:
            weekday = "一二三四五六日"[self.selected_date.weekday()]
            date_text = f"{self.selected_date.isoformat()}  周{weekday} ▾"
        self.date_button.configure(text=date_text)
        self.today_button.configure(text={"day": "今日", "week": "本周", "month": "本月"}[self.period])
        self.next_button.configure(state="disabled" if current else "normal")
        for mode, button in self.period_buttons.items():
            button.configure(bg=INK if mode == self.period else CARD,
                             fg=PAPER if mode == self.period else MUTED)
        models = period_models(self.data, self.selected_date, self.period)
        summary = summarize_day(models, self.pricebook)
        captions = {"day": ("今日", "当日"), "week": ("本周", "当周"), "month": ("本月", "当月")}
        self.summary_card.set_data(summary, period_output(self.data, self.selected_date, self.period),
                                   captions[self.period][0 if current else 1])
        if self.period == "day":
            self.charts.pack_forget()
        else:
            buckets = period_buckets(self.data, self.selected_date, self.period, tz=self.reader.tz)
            points = [{**bucket, **summarize_day(bucket["models"], self.pricebook)} for bucket in buckets]
            self.charts.pack(fill="x", before=self.quota_separator)
            self.charts.set_data(points, self.selected_date, self.period, self.reader.tz)
        self._render_quota()
        self._render_models(summary)
        if self._last_scan:
            self.status_label.configure(text=f"已刷新 {self._last_scan:%H:%M:%S} · 下次 {self._next_refresh_at:%H:%M}")
        warnings = self.data.get("warnings", []) + getattr(self.pricebook, "warnings", [])
        self.warning_label.configure(text=(f"有 {len(warnings)} 条读取提示（点击查看）" if warnings else self._settings_error or ""))

    def _render_quota(self):
        for child in self.quota.winfo_children():
            child.destroy()
        pages = self.data.get("account_pages") or self.account_store.pages(
            self.data.get("current_account", {}), note_now())
        current_key = pages[0]["key"]
        if current_key != self._last_current_account:
            self._selected_account = current_key
            self._last_current_account = current_key
        names = [page["key"] for page in pages]
        if self._selected_account not in names:
            self._selected_account = current_key
        self._account_pages = pages
        index = names.index(self._selected_account)
        account = pages[index]
        heading = tk.Frame(self.quota, bg=PAPER)
        heading.pack(fill="x", pady=(0, 2))
        self._label(heading, "账号额度", 9, bold=True).pack(side="left")
        nav = tk.Frame(heading, bg=PAPER)
        nav.pack(side="right")
        self.account_prev_button = self._button(nav, "‹", lambda: self._move_account(-1), width=1)
        self.account_prev_button.pack(side="left")
        self.account_prev_button.configure(state="normal" if index else "disabled")
        self.account_page_label = self._label(nav, f"{index + 1} / {len(pages)}", 8, MUTED)
        self.account_page_label.pack(side="left", padx=2)
        self.account_next_button = self._button(nav, "›", lambda: self._move_account(1), width=1)
        self.account_next_button.pack(side="left")
        self.account_next_button.configure(state="normal" if index < len(pages) - 1 else "disabled")
        if account.get("email") or account.get("status") == "signed_in":
            email = account.get("email") or "邮箱不可用"
            plan = account.get("plan")
            plan_name = {"pro": "Pro", "plus": "Plus", "free": "Free"}.get(plan, plan)
            prefix = "当前登录" if account["current"] else "历史账号"
            account_text = f"{prefix}：{email}" + (f" · {plan_name}" if plan_name else "")
        elif account.get("status") == "api_key":
            account_text = "当前登录：API Key 模式"
        else:
            account_text = "当前登录：未能读取账号信息"
        self.current_account_label = self._label(self.quota, account_text, 8, MUTED,
                                                anchor="w", justify="left", wraplength=310)
        self.current_account_label.pack(fill="x", pady=(2, 1))
        self.current_account_label.bind("<Configure>", lambda event:
                                       event.widget.configure(wraplength=max(80, event.width - 2)))
        groups = [group for group in quota_groups({"latest_rate_limits_by_id": account["limits"]})
                  if group["windows"] and group["id"] not in HIDDEN_QUOTA_IDS]
        if groups:
            self._render_quota_groups(self.quota, groups)
            stamp = local_time(account["at"])
            self._label(self.quota, f"{'查询于' if account['current'] else '上次快照'} {stamp} · 北京时间",
                        8, MUTED).pack(anchor="w", pady=(3, 0))
        else:
            self._label(self.quota, "尚无该账号的额度快照", 9, MUTED).pack(anchor="w", pady=(5, 2))
        if account["current"] and self.data.get("account_quota_error"):
            self._label(self.quota, self.data["account_quota_error"], 8, ORANGE, anchor="w",
                        justify="left", wraplength=300).pack(fill="x", pady=(2, 0))
        if len(pages) == 1:
            self._label(self.quota, "其他账号登录并刷新后，会自动加入", 8, MUTED).pack(anchor="w", pady=(3, 0))

    def _move_account(self, amount):
        names = [page["key"] for page in self._account_pages]
        if not names:
            return
        index = names.index(self._selected_account)
        self._selected_account = names[max(0, min(len(names) - 1, index + amount))]
        self._render_quota()

    def _render_quota_groups(self, parent, groups):
        for group in groups:
            header = tk.Frame(parent, bg=PAPER)
            header.pack(fill="x", pady=(6, 3))
            header.grid_columnconfigure(0, weight=1)
            title = self._label(header, group["title"], 9, bold=True, anchor="w", justify="left", wraplength=210)
            title.grid(row=0, column=0, sticky="w")
            timestamp = self._label(header, local_time(group["at"]), 8, MUTED)
            timestamp.grid(row=0, column=1, sticky="ne", padx=(8, 0))
            header.bind("<Configure>", lambda event, label=title, stamp=timestamp:
                        label.configure(wraplength=max(70, event.width - stamp.winfo_reqwidth() - 8)))
            for entry in group["windows"]:
                window = entry["window"]
                reset = window.get("resets_at", window.get("resetsAt"))
                due = (isinstance(reset, (int, float))
                       and not isinstance(reset, bool) and math.isfinite(reset)
                       and 0 < reset <= note_now().timestamp())
                used = window.get("used_percent", window.get("usedPercent"))
                try:
                    used = float(used)
                    remaining = max(0, min(100, 100 - used)) if math.isfinite(used) else None
                except (ValueError, TypeError):
                    remaining = None
                row = tk.Frame(parent, bg=PAPER)
                row.pack(fill="x", pady=2)
                self._label(row, entry["title"], 9, MUTED, width=5, anchor="w", wraplength=47,
                            justify="left").pack(side="left")
                bar = tk.Canvas(row, width=55, height=5, bg=LINE, highlightthickness=0)
                bar.pack(side="left", fill="x", expand=True, padx=(5, 10))
                color = MUTED if remaining is None or due else GREEN if remaining > 20 else ORANGE
                if remaining is not None and not due:
                    bar.bind("<Configure>", lambda event, b=bar, r=remaining, c=color: (b.delete("all"), b.create_rectangle(0, 0, event.width * r / 100, 5, fill=c, outline="")))
                suffix = f" · {local_time(reset, '%m-%d %H:%M')} 重置" if reset is not None else ""
                label = f"余 {remaining:g}%" if remaining is not None else "比例未知"
                if due:
                    label, suffix = "已到重置时间 · 待刷新", ""
                self._label(row, label + suffix, 8, color).pack(side="right")

    def _move_model(self, amount):
        if not self._model_rows:
            return
        names = [row["model"] for row in self._model_rows]
        index = names.index(self._selected_model) if self._selected_model in names else 0
        self._selected_model = names[max(0, min(len(names) - 1, index + amount))]
        self._render_models({"rows": self._model_rows})

    def _render_models(self, summary):
        self._model_rows = summary["rows"]
        names = [row["model"] for row in self._model_rows]
        if self._selected_model not in names:
            self._selected_model = names[0] if names else None
        index = names.index(self._selected_model) if names else 0
        self.model_page_label.configure(text=f"{index + 1 if names else 0} / {len(names)}")
        self.model_prev_button.configure(state="normal" if index > 0 else "disabled")
        self.model_next_button.configure(state="normal" if index < len(names) - 1 else "disabled")
        for child in self.models_frame.winfo_children():
            child.destroy()
        if not summary["rows"]:
            label = {"day": "这一天", "week": "这一周", "month": "这个月"}[self.period]
            self._label(self.models_frame, f"{label}，还没有用量记录。", 12, MUTED).pack(anchor="w", pady=(25, 8))
            self._label(self.models_frame, "继续使用 Codex，便签会自动更新。", 9, MUTED).pack(anchor="w")
            return
        for row in self._model_rows[index:index + 1]:
            card = tk.Frame(self.models_frame, bg=CARD, padx=12, pady=10)
            card.pack(fill="x", pady=(0, 8))
            card.grid_columnconfigure(0, weight=1)
            title = self._label(card, row["model"], 11, INK, True, anchor="w", cursor="hand2", justify="left", wraplength=200)
            title.grid(row=0, column=0, sticky="w")
            title.bind("<Button-1>", lambda _e, model=row["model"]: self._edit_prices(model))
            cost_text = money(row["quote"].get("total_usd"))
            if row["partial"] and row["known_usd"]:
                cost_text = money(row["known_usd"]) + " *"
            price = self._label(card, cost_text, 11, ORANGE, True)
            price.grid(row=0, column=1, sticky="ne", padx=(8, 0))
            card.bind("<Configure>", lambda event, label=title, cost=price:
                      label.configure(wraplength=max(65, event.width - cost.winfo_reqwidth() - 32)))
            tokens = row["tokens"]
            self._label(card, f"{row['total_tokens']:,} token", 8, MUTED).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 7))
            details = tk.Frame(card, bg=CARD)
            details.grid(row=2, column=0, columnspan=2, sticky="ew")
            for index, (key, name) in enumerate((("input", "输入"), ("cached", "缓存读取"), ("output", "输出"))):
                details.grid_columnconfigure(index, weight=1)
                cell = tk.Frame(details, bg=CARD)
                cell.grid(row=0, column=index, sticky="w")
                self._label(cell, name, 8, MUTED).pack(anchor="w")
                self._label(cell, f"{tokens.get(key, 0):,}", 9).pack(anchor="w", pady=(1, 0))
                self._label(cell, money(row["quote"].get(key + "_usd")), 8, MUTED).pack(anchor="w")
            if tokens.get("cache_write"):
                write_price = row["quote"].get("cache_write_usd")
                self._label(card, f"含缓存写入 {tokens['cache_write']:,} · {money(write_price)}", 8, MUTED).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _show_warnings(self):
        warnings = [str(item) for item in self.data.get("warnings", []) + getattr(self.pricebook, "warnings", [])]
        if self._settings_error:
            warnings.insert(0, self._settings_error)
        if getattr(self, "_scan_error", None):
            warnings.insert(0, self._scan_error)
        messagebox.showinfo("读取提示", "\n\n".join(warnings[:30]) or "当前没有读取提示。", parent=self.root)

    def _show_info(self):
        latest = local_time(self.data.get("latest_event_at"))
        notes = getattr(self.pricebook, "notes", [])
        if isinstance(notes, str):
            notes = [notes]
        details = "\n\n".join(str(note) for note in notes)
        text = ("统一按北京时间（UTC+8）汇总 Codex 本机历史（含归档），可能包含多个账号留下的记录。日期、曲线、额度快照和重置时间均为北京时间。\n\n"
                            "日／周／月切换决定汇总范围。自然周从周一开始，每小时一个点；自然月每天一个点。曲线表示各时段的用量，不是累计值。过去无记录时段按 0 计，未来不计入，当前时段尚未结束。\n\n"
                            "按你的统计口径，codex-auto-review 不计入 token、金额和模型明细。\n\n"
                            "总 token = 输入 + 缓存读取 + 输出。输入不含缓存读取；缓存写入是输入的子项，不重复累加。推理 token 已包含在输出中。\n\n"
                            "消费／产出卡片默认展示消费，可点击标签、左右箭头或在卡片内左右拖动；选中卡片后也可用键盘左右键。两页共用等高布局，刷新和日期切换保留当前页，重新打开默认回到消费。\n\n"
                            "产出只统计 Codex 自带日志：成功完成的文件修改中，新增与删除行数之和为改动行数（含代码、文档及其他文本），同一文件多次修改会累计行数；修改文件数在所选时段内按路径去重。完成轮次来自完成事件，不等于任务验收通过或合并代码。重复／分叉／归档记录只计一次，排除 codex-auto-review。\n\n"
                            "产出是日志已记录的小计，不是 Git 净增行数。通过终端脚本等方式写入、但没有内置文件修改记录的内容无法计入；没有产出记录的时段显示横线。不会访问 Issue/MR，也不会推测缺失记录。\n\n"
                            "金额按当前已知的标准 API 刊例价估算，单位为美元。它不是 ChatGPT / Codex 订阅实际账单，也不代表额度消耗。\n\n"
                            "历史日期也按当前价表估算。暂不调整长上下文、Fast / Priority、Batch 等特殊价格；缺少模型价格或缓存写入价时标为待定价，并仅显示已知价小计。可点击模型名称编辑价格与来源。\n\n"
                            "账号额度用左右箭头翻页：当前登录账号第一，其余按最早重置时间排序。其他账号保留上次查询快照，登录并成功刷新一次后自动收录。翻页不会切换登录。\n\n"
                            "启动和手动刷新立即读取用量并查询当前账号额度；自动更新在北京时间每小时的 00、05、10……55 分触发。手动刷新不会改变下一次自动更新时间，电脑休眠期间不更新，恢复后补一次并重新对齐钟点。\n\n"
                            "账号额度仅使用 Codex 账号接口结果，每次查询核对账号 ID 与查询前后身份。查询失败保留该账号已有快照及时间；没有快照则显示暂无数据，不用日志额度补全。已到重置时间的旧窗口显示待刷新，不推算剩余额度。Token、费用、曲线和模型明细仍是本机全部日志，与账号额度翻页无关。\n\n"
                            f"最近用量记录：{latest}\n已扫描文件：{self.data.get('files_scanned', 0):,}\n"
                            f"价表核实日期：{getattr(self.pricebook, 'updated_at', '') or '未知'}\n"
                            "用量日志仅在本机读取，不上传会话。账号额度由本机 Codex 查询官方服务；本地仅保存邮箱、套餐、额度与账号 ID 的哈希，不保存登录凭据。\n\n价表详细说明\n\n" + details)
        dialog = self._dialog("如何理解这张便签", "530x620")
        body = tk.Frame(dialog, bg=PAPER, padx=20, pady=20)
        body.pack(fill="both", expand=True)
        self._label(body, "这张便签怎样计数、计价", 15, bold=True).pack(anchor="w", pady=(0, 14))
        content = tk.Frame(body, bg=PAPER)
        content.pack(fill="both", expand=True)
        scroll = ttk.Scrollbar(content)
        scroll.pack(side="right", fill="y")
        explanation = tk.Text(content, bg=PAPER, fg=INK, font=(FONT, 10), wrap="word",
                              relief="flat", highlightthickness=0, yscrollcommand=scroll.set, padx=0, spacing3=4)
        explanation.insert("1.0", text)
        explanation.configure(state="disabled")
        explanation.pack(fill="both", expand=True)
        scroll.configure(command=explanation.yview)
        self._button(body, "知道了", dialog.destroy, accent=True).pack(anchor="e", pady=(14, 0))

    def close(self):
        if self._closed:
            return
        self._save_settings()
        self._closed = True
        for identifier in self._after_ids:
            self.root.after_cancel(identifier)
        self._after_ids.clear()
        self.root.destroy()
