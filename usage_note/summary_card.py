"""Consumption and output share one layout so paging never changes height."""

import tkinter as tk

from usage_note.timebase import TIMEZONE_LABEL


class SummaryCard(tk.Frame):
    def __init__(self, parent, label, button, money):
        super().__init__(parent, bg=parent.cget("bg"), takefocus=True)
        self.page = 0
        self._money = money
        self._data = None
        self._press = None
        muted, ink, card = "#797C72", "#292D2A", "#EEEADF"
        self._green, self._orange = "#4E775E", "#CA602E"
        nav = tk.Frame(self, bg=self.cget("bg"))
        nav.pack(fill="x", pady=(0, 5))
        self.tabs = []
        for index, name in enumerate(("消费", "产出")):
            tab = button(nav, name, lambda p=index: self.show_page(p))
            tab.pack(side="left", padx=(0, 3))
            self.tabs.append(tab)
        controls = tk.Frame(nav, bg=nav.cget("bg"))
        controls.pack(side="right")
        self.prev_button = button(controls, "‹", lambda: self.show_page(self.page - 1), width=1)
        self.prev_button.pack(side="left")
        self.page_label = label(controls, "1 / 2", 8, muted)
        self.page_label.pack(side="left", padx=2)
        self.next_button = button(controls, "›", lambda: self.show_page(self.page + 1), width=1)
        self.next_button.pack(side="left")

        hero = tk.Frame(self, bg=card, padx=16, pady=12)
        hero.pack(fill="x")
        hero.grid_columnconfigure(0, weight=1)
        caption_row = tk.Frame(hero, bg=card)
        caption_row.grid(row=0, column=0, sticky="ew")
        self.total_caption = label(caption_row, "今日 TOKEN", 9, muted)
        self.total_caption.pack(side="left")
        self.timezone_label = label(caption_row, TIMEZONE_LABEL, 8, muted)
        self.timezone_label.pack(side="right")
        self.total_tokens = label(hero, "0", 27, ink, True, anchor="w", width=1)
        self.total_tokens.grid(row=1, column=0, sticky="ew", pady=(1, 2))
        cost_row = tk.Frame(hero, bg=card)
        cost_row.grid(row=2, column=0, sticky="ew")
        self.cost_caption = label(cost_row, "标准刊例价", 9, muted)
        self.cost_caption.pack(side="left")
        self.total_price = label(cost_row, "$0.00", 18, self._orange, True)
        self.total_price.pack(side="right")

        self.breakdown = tk.Frame(self, bg=self.cget("bg"))
        self.breakdown.pack(fill="x", pady=(12, 3))
        self.breakdown.grid_columnconfigure(1, weight=1)
        self.rows = []
        for index in range(3):
            title = label(self.breakdown, "", 10, ink, anchor="w", width=8)
            title.grid(row=index, column=0, sticky="w", pady=3)
            count = label(self.breakdown, "0", 10)
            count.grid(row=index, column=1, sticky="e", padx=12)
            detail = label(self.breakdown, "", 10, muted, anchor="e", width=12)
            detail.grid(row=index, column=2, sticky="e")
            self.rows.append((title, count, detail))
        self.note = label(self, "", 8, muted, anchor="w", width=1, height=1)
        self.note.pack(fill="x")

        # Bind only this card: ordinary vertical scrolling remains untouched.
        self._bind_swipe(self)
        self.bind("<Left>", lambda _e: self._key_page(-1))
        self.bind("<Right>", lambda _e: self._key_page(1))

    def _bind_swipe(self, widget):
        if not isinstance(widget, tk.Button):
            widget.bind("<ButtonPress-1>", self._swipe_start, add="+")
            widget.bind("<ButtonRelease-1>", self._swipe_end, add="+")
            widget.bind("<Shift-MouseWheel>", self._horizontal_wheel)
        for child in widget.winfo_children():
            self._bind_swipe(child)

    def _swipe_start(self, event):
        self._press = (event.x_root, event.y_root)
        self.focus_set()

    def _swipe_end(self, event):
        if self._press is None:
            return
        dx, dy = event.x_root - self._press[0], event.y_root - self._press[1]
        self._press = None
        if abs(dx) >= 45 and abs(dx) > abs(dy) * 1.5:
            self.show_page(self.page + (1 if dx < 0 else -1))
            return "break"

    def _horizontal_wheel(self, event):
        if event.delta:
            self.show_page(self.page + (1 if event.delta < 0 else -1))
        return "break"

    def _key_page(self, delta):
        self.show_page(self.page + delta)
        return "break"

    def show_page(self, page):
        self.page = max(0, min(1, page))
        self.focus_set()
        self._render()

    def set_data(self, consumption, output, caption):
        self._data = consumption, output, caption
        self._render()

    def _render(self):
        if self._data is None:
            return
        consumption, output, caption = self._data
        for index, tab in enumerate(self.tabs):
            tab.configure(bg="#292D2A" if index == self.page else self.cget("bg"),
                          fg=self.cget("bg") if index == self.page else "#797C72")
        self.prev_button.configure(state="normal" if self.page else "disabled")
        self.next_button.configure(state="disabled" if self.page else "normal")
        self.page_label.configure(text=f"{self.page + 1} / 2")
        if self.page == 0:
            self.total_caption.configure(text=caption + " TOKEN")
            self.total_tokens.configure(text=f"{consumption['tokens']:,}")
            self.total_price.configure(text=self._money(consumption["known_usd"]), fg=self._orange)
            self.cost_caption.configure(text="标准刊例价" if consumption["fully_priced"] else "已知价小计 · 部分待定价")
            for widgets, (key, title, color) in zip(self.rows, (
                    ("input", "输入", "#292D2A"), ("cached", "缓存读取", self._green),
                    ("output", "输出", self._orange))):
                item = consumption["categories"][key]
                widgets[0].configure(text=title, fg=color)
                widgets[1].configure(text=f"{item['tokens']:,}")
                widgets[2].configure(text=self._money(item["usd"]) + (" *" if item["partial"] else ""))
            writes = consumption["cache_write"]
            self.note.configure(text=f"其中缓存写入 {writes:,} token，已含在输入中" if writes
                                else "输入不含缓存读取 · * 表示仅含已知价格")
        else:
            recorded = output["recorded"]
            self.total_caption.configure(text=caption + " 改动行数")
            self.total_tokens.configure(text=f"{output['added'] + output['deleted']:,}" if recorded else "—")
            self.cost_caption.configure(text="完成轮次")
            self.total_price.configure(text=f"{output['turns']:,} 轮" if recorded else "—", fg=self._green)
            for widgets, (key, title, color, unit) in zip(self.rows, (
                    ("added", "新增", self._green, "行"), ("deleted", "删除", self._orange, "行"),
                    ("files", "修改文件", "#292D2A", "个"))):
                widgets[0].configure(text=title, fg=color)
                widgets[1].configure(text=f"{output[key]:,}" if recorded else "—")
                widgets[2].configure(text=unit)
            self.note.configure(text="本机日志已记录 · 改动含代码与文档" if recorded else "该时段暂无 Codex 产出记录")
