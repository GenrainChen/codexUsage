"""Two lightweight Tk curves with shared hourly/daily hover inspection."""

from __future__ import annotations

import bisect
import math
import tkinter as tk
from datetime import timedelta

from usage_note.periods import local_midnight, period_bounds
from usage_note.timebase import BEIJING_TZ

PAPER, INK, MUTED = "#F7F4EC", "#292D2A", "#797C72"
LINE, GREEN, ORANGE = "#E2DED3", "#4E775E", "#CA602E"
FONT = "Microsoft YaHei UI"


def compact(value):
    for scale, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= scale:
            return f"{value / scale:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{value:g}"


class UsageCharts(tk.Frame):
    """Plot interval totals, never a smoothed or cumulative interpolation."""

    def __init__(self, parent, money_format):
        super().__init__(parent, bg=PAPER)
        self.money_format = money_format
        self.points = []
        self.mode = "week"
        self._coords = {}
        self.heading = tk.Label(self, bg=PAPER, fg=INK, font=(FONT, 10, "bold"), anchor="w")
        self.heading.pack(fill="x", pady=(12, 1))
        self.plots = {}
        for key in ("tokens", "known_usd"):
            canvas = tk.Canvas(self, bg=PAPER, height=139, highlightthickness=0, bd=0)
            canvas.pack(fill="x")
            canvas.bind("<Configure>", lambda _e: self._draw())
            canvas.bind("<Motion>", lambda e, k=key: self._hover(e.x, k))
            canvas.bind("<Button-1>", lambda e, k=key: self._hover(e.x, k))
            canvas.bind("<Leave>", lambda _e: self._clear_hover())
            self.plots[key] = canvas
        self.detail = tk.Label(self, bg=PAPER, fg=MUTED, font=(FONT, 8),
                               anchor="w", justify="left", height=2)
        self.detail.pack(fill="x", pady=(1, 8))
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", pady=(0, 8))

    def set_data(self, points, anchor, mode, tz=BEIJING_TZ):
        self.points, self.mode, self.tz = points, mode, tz
        self.start, self.end = period_bounds(anchor, mode)
        self.start_at, self.end_at = local_midnight(self.start, tz), local_midnight(self.end, tz)
        self.heading.configure(text="每小时用量 / 自然周" if mode == "week" else "每天用量 / 自然月")
        self._draw()
        self._clear_hover()

    def _fraction(self, at):
        if self.mode == "month":
            return (at.date() - self.start).days / max(1, (self.end - self.start).days - 1)
        return ((at.timestamp() - self.start_at.timestamp()) /
                max(3600, self.end_at.timestamp() - self.start_at.timestamp() - 3600))

    def _draw(self):
        if not hasattr(self, "start"):
            return
        self._coords.clear()
        for key, canvas in self.plots.items():
            canvas.delete("all")
            width = max(160, canvas.winfo_width())
            left, right, top, bottom = 51, width - 9, 32, 112
            color = GREEN if key == "tokens" else ORANGE
            title = "TOKEN" if key == "tokens" else "费用 / USD"
            partial = key == "known_usd" and any(not p["fully_priced"] for p in self.points)
            if partial:
                title += " · 已知价小计 *"
            canvas.create_text(0, 10, text=title, fill=color, anchor="w", font=(FONT, 8, "bold"))
            values = [point[key] for point in self.points]
            maximum = max(values, default=0)
            if maximum > 0:
                scale = 10 ** math.floor(math.log10(maximum))
                ceiling = math.ceil(maximum / scale) * scale
            else:
                ceiling = 1
            if key == "tokens":
                ceiling = max(2, ceiling)
            for value in (0, ceiling / 2, ceiling):
                y = bottom - value / ceiling * (bottom - top)
                canvas.create_line(left, y, right, y, fill=LINE)
                label = compact(value) if key == "tokens" else self.money_format(value)
                canvas.create_text(left - 7, y, text=label, fill=MUTED, anchor="e", font=(FONT, 7))
            if self.mode == "week":
                ticks = [(self.start + timedelta(days=i), "一二三四五六日"[i]) for i in range(7)]
            else:
                last = (self.end - self.start).days
                ticks = [(self.start + timedelta(days=d - 1), str(d)) for d in (1, 8, 15, 22, last)]
            for day, label in ticks:
                x = left + self._fraction(local_midnight(day, self.tz)) * (right - left)
                canvas.create_text(x, bottom + 16, text=label, fill=MUTED, font=(FONT, 7))
            coords = [(left + self._fraction(p["at"]) * (right - left),
                       bottom - p[key] / ceiling * (bottom - top)) for p in self.points]
            self._coords[key] = coords
            if len(coords) > 1:
                polygon = [coords[0][0], bottom]
                polygon.extend(value for point in coords for value in point)
                polygon.extend((coords[-1][0], bottom))
                canvas.create_polygon(polygon, fill="#E7EDE3" if key == "tokens" else "#F2E6D9", outline="")
                # Straight segments preserve measured spikes and cannot overshoot zero.
                for index in range(1, len(coords)):
                    incomplete = key == "known_usd" and (
                        not self.points[index - 1]["fully_priced"] or not self.points[index]["fully_priced"])
                    canvas.create_line(*coords[index - 1], *coords[index], fill=color, width=2,
                                       dash=(3, 3) if incomplete else ())
            for index, (x, y) in enumerate(coords):
                if self.mode == "month" or len(coords) == 1 or (key == "known_usd" and not self.points[index]["fully_priced"]):
                    canvas.create_oval(x - 2, y - 2, x + 2, y + 2, outline=color,
                                       fill=PAPER if key == "known_usd" and not self.points[index]["fully_priced"] else color)
            if coords and self.points[-1]["current"] and right - coords[-1][0] > 62:
                canvas.create_text((coords[-1][0] + right) / 2, (top + bottom) / 2,
                                   text="尚未发生", fill=MUTED, font=(FONT, 8))

    def _hover(self, x, key):
        coords = self._coords.get(key, [])
        if not coords:
            return
        xs = [point[0] for point in coords]
        step = xs[1] - xs[0] if len(xs) > 1 else 3
        if x < xs[0] - 8 or x > xs[-1] + max(step / 2, 3):
            self._clear_hover()
            return
        index = min(bisect.bisect_left(xs, x), len(xs) - 1)
        if index and abs(xs[index - 1] - x) <= abs(xs[index] - x):
            index -= 1
        point = self.points[index]
        for name, canvas in self.plots.items():
            canvas.delete("hover")
            px, py = self._coords[name][index]
            color = GREEN if name == "tokens" else ORANGE
            canvas.create_line(px, 29, px, 114, fill=MUTED, dash=(2, 3), tags="hover")
            canvas.create_oval(px - 3, py - 3, px + 3, py + 3, fill=PAPER, outline=color,
                               width=2, tags="hover")
        at, end = point["at"], point["end"]
        if self.mode == "week":
            offset = at.strftime("%z")
            label = f"{at:%m-%d %H:%M}–{end:%H:%M}  UTC{offset[:3]}:{offset[3:]}"
        else:
            label = f"{at:%Y-%m-%d}"
        if point["current"]:
            label += " · 进行中"
        cost = self.money_format(point["known_usd"])
        if not point["fully_priced"]:
            cost += " * 已知价"
        self.detail.configure(text=f"{label}\n{point['tokens']:,} token   ·   {cost}", fg=INK)

    def _clear_hover(self):
        for canvas in self.plots.values():
            canvas.delete("hover")
        unit = "小时" if self.mode == "week" else "天"
        text = f"悬停查看每{unit}数值 · 已过时段无记录按 0 计"
        if self.points and self.points[-1]["current"]:
            text += "\n当前时段尚未结束，随记录自动更新"
        else:
            text += "\n费用按当前刊例价估算"
        self.detail.configure(text=text, fg=MUTED)
