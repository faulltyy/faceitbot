"""Monospaced table formatter for Telegram ``<pre>`` blocks with colored emoji.

The main entry-point is :func:`format_matches_table` which accepts a list of
pre-parsed match dicts and returns an HTML string ready for
``bot.send_message(parse_mode="HTML")``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any


# ---- visual-width helpers ------------------------------------------------ #

def _visual_width(text: str) -> int:
    """Calculate visual width in a monospace font (emoji = 2 cells)."""
    w = 0
    for ch in text:
        # Emoji and pictographic symbols render as 2 cells in monospace
        cp = ord(ch)
        if cp >= 0x1F300:
            w += 2
        else:
            w += 1
    return w


def _vpad(text: str, width: int, align: str = "<") -> str:
    """Pad *text* to a fixed *visual* width (accounts for wide emoji)."""
    pad = max(0, width - _visual_width(text))
    if align == ">":
        return " " * pad + text
    return text + " " * pad


def _trunc(text: str, w: int) -> str:
    """Truncate *text* to *w* chars, appending ``…`` when shortened."""
    return text if len(text) <= w else text[: w - 1] + "…"


# ---- color helpers ------------------------------------------------------- #

def _color_result(win: bool | None) -> str:
    if win is True:
        return "🟢"
    return "🔴"


def _color_kd(val: float) -> str:
    emoji = "🟢" if val >= 1.0 else "🔴"
    return f"{emoji}{val:.2f}"


def _color_kr(val: float) -> str:
    if val < 0.6:
        emoji = "🔴"
    elif val <= 0.75:
        emoji = "🟠"
    else:
        emoji = "🟢"
    return f"{emoji}{val:.2f}"


def _color_adr(val: float) -> str:
    if val < 60:
        emoji = "🔴"
    elif val <= 75:
        emoji = "🟠"
    else:
        emoji = "🟢"
    return f"{emoji}{val:.1f}"


def _elo(diff: int | float | None, elo: int | float | None) -> str:
    """Format ELO column: ``+25(2115)`` or ``N/A``."""
    if diff is None or elo is None:
        if elo is not None:
            return str(int(elo))
        return "N/A"
    try:
        d, e = int(diff), int(elo)
        return f"{'+' if d >= 0 else ''}{d}({e})"
    except (TypeError, ValueError):
        return "N/A"


# ---- datetime helper ----------------------------------------------------- #

def _format_datetime(ts: int | float | None) -> str:
    """Convert a Unix timestamp to ``March 8 14:40`` format (UTC)."""
    if ts is None:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        day = dt.day  # no leading zero
        month = dt.strftime("%B")  # full month name
        time = dt.strftime("%H:%M")
        return f"{month} {day} {time}"
    except (ValueError, OSError, OverflowError):
        return "-"


# ---- column spec --------------------------------------------------------- #
# (header, visual_width, align)

_COLS = [
    ("#",   2, ">"),
    ("R",   2, "<"),     # emoji: 🟢/🔴
    ("Map", 6, "<"),
    ("K",   2, ">"),
    ("D",   2, ">"),
    ("K/D", 6, ">"),     # emoji + 4-char value
    ("K/R", 6, ">"),     # emoji + 4-char value
    ("ADR", 6, ">"),     # emoji + 4-char value
    ("ELO", 10, ">"),
]

_SEP = " "


def _row(values: list[str]) -> str:
    """Build one row using visual-width-aware padding."""
    parts: list[str] = []
    for (_, w, align), val in zip(_COLS, values):
        parts.append(_vpad(val, w, align))
    return _SEP.join(parts)


# ---- public API ---------------------------------------------------------- #

def format_matches_table(
    nickname: str,
    matches: list[dict[str, Any]],
    current_elo: int | None = None,
) -> str:
    """Return a fully formatted HTML string for the last-N matches table."""
    if not matches:
        return "No recent CS2 matches found."

    total = len(matches)

    # ---- emoji header (outside <pre>) ------------------------------------ #
    elo_badge = f"  |  ELO: {current_elo}" if current_elo else ""
    header = f"📊 Last {total} CS2 matches for {escape(nickname)}{elo_badge}\n\n"

    # ---- build table ----------------------------------------------------- #
    hdr = _row([h for h, _, _ in _COLS])
    sep = "-" * _visual_width(hdr)
    lines: list[str] = [hdr, sep]

    for idx, m in enumerate(matches, start=1):
        win = m.get("win")
        map_name = m.get("map") or "-"
        kills = int(m.get("kills", 0))
        deaths = int(m.get("deaths", 0))
        kd = float(m.get("kd", 0))
        kr = float(m.get("kr", 0))
        adr = float(m.get("adr", 0))
        elo_str = _elo(m.get("elo_diff"), m.get("current_elo"))

        lines.append(_row([
            str(idx),
            _color_result(win),
            _trunc(map_name, 6),
            str(kills),
            str(deaths),
            _color_kd(kd),
            _color_kr(kr),
            _color_adr(adr),
            elo_str,
        ]))

        # sub-line: date/time when the match was played
        date_str = _format_datetime(m.get("finished_at"))
        lines.append(f"   ↳ {date_str}")

    table_body = escape("\n".join(lines))

    # ---- assemble -------------------------------------------------------- #
    html = f"{header}<pre>{table_body}</pre>"

    # Telegram messages are capped at 4096 characters
    if len(html) > 4096:
        html = html[:4090] + "</pre>"

    return html
