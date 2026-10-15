from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPaintEvent
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.i18n import tr
from app.core.context import AppContext
from app.core.history import average_chars, entries_since, top_voices
from app.ui.theme import color
from app.utils.helpers import fmt_int


# hand-drawn to avoid pulling in PyQt6-Charts
class BarChart(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.data: list[tuple[str, int]] = []
        self.setMinimumHeight(150)

    def set_data(self, data: list[tuple[str, int]]) -> None:
        self.data = data
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bottom, top = h - 18, 8
        p.setPen(QColor(color("border")))
        p.drawLine(0, bottom, w, bottom)
        if not self.data:
            return
        peak = max(v for _, v in self.data) or 1
        n = len(self.data)
        slot = w / n
        bar_w = max(2.0, slot * 0.7)
        p.setPen(Qt.PenStyle.NoPen)
        for i, (day, value) in enumerate(self.data):
            bh = (bottom - top) * value / peak
            x = i * slot + (slot - bar_w) / 2
            p.setBrush(QColor(color("accent") if value else color("border")))
            p.drawRoundedRect(QRectF(x, bottom - max(bh, 1), bar_w, max(bh, 1)), 2, 2)
        p.setPen(QColor(color("muted")))
        p.drawText(2, h - 4, self.data[0][0][5:])
        last = self.data[-1][0][5:]
        p.drawText(w - p.fontMetrics().horizontalAdvance(last) - 2, h - 4, last)
        p.drawText(2, top + 10, tr("max {n}", n=fmt_int(peak)))
        p.end()


class StatsView(QWidget):
    def __init__(self, ctx: AppContext, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        lay = QVBoxLayout(self)
        t = QLabel(tr("Credits used, last 30 days"))
        t.setObjectName("Title")
        lay.addWidget(t)
        self.chart = BarChart()
        lay.addWidget(self.chart)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.summary)
        lay.addStretch(1)
        self.render()

    def render(self) -> None:
        usage = self.ctx.usage
        self.chart.set_data(usage.last_days(30))
        history = self.ctx.history.items
        week = usage.week_total(0)
        prev = usage.week_total(1)
        trend = ""
        if prev:
            delta = (week - prev) / prev * 100
            trend = tr(" ({sign}{delta:.0f}% vs last week)", sign='+' if delta >= 0 else '', delta=delta)
        top = top_voices(history)
        top_html = "".join(f"<li>{name} — {count}</li>" for name, count in top) or "<li>—</li>"
        recent = entries_since(history, 7)
        speed = usage.sec_per_kchar
        speed_text = tr("{s:.1f} s per 1000 chars", s=speed) if speed else tr("no data yet")
        by_acc = []
        if self.ctx.accounts:
            for acc in self.ctx.accounts.accounts:
                by_acc.append(f"<li>{acc.name}: {fmt_int(usage.by_account.get(acc.id, 0))}</li>")
        self.summary.setText(
            tr("<b>This week:</b> {week} credits{trend}<br>"
               "<b>Generations this week:</b> {recent}<br>"
               "<b>Average text size:</b> {avg} characters<br>"
               "<b>Speed:</b> {speed}<br>"
               "<b>Top voices:</b><ol>{top}</ol>"
               "<b>Usage by key (local count):</b><ul>{by_acc}</ul>",
               week=fmt_int(week), trend=trend, recent=len(recent), avg=fmt_int(average_chars(history)),
               speed=speed_text, top=top_html, by_acc="".join(by_acc) or "<li>—</li>")
        )
