"""Monospaced table formatter for Telegram ``<pre>`` blocks with colored emoji.

The main entry-point is :func:`format_matches_table` which accepts a list of
pre-parsed match dicts and returns an HTML string ready for
``bot.send_message(parse_mode="HTML")``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import escape
from typing import Any

_DE_PREFIX = re.compile(r"^de_", re.IGNORECASE)


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


# ---- FaceitAnalyser formatters ------------------------------------------- #

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    """Safely convert to int."""
    if val is None:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def format_overview(nickname: str, data: dict[str, Any]) -> str:
    """Format lifetime stats from FaceitAnalyser /api/stats/<id>.

    NOTE: The API returns ``kdr`` / ``krr`` as *cumulative sums* (not ratios).
    Use ``avg_kdr`` / ``avg_krr`` for per-match averages, and compute the
    true overall ratio as ``k / d``.
    """

    m = _safe_int(data.get("m"))
    w = _safe_int(data.get("w"))
    l_val = _safe_int(data.get("l"))
    k = _safe_int(data.get("k"))
    d = _safe_int(data.get("d"))
    a = _safe_int(data.get("a"))
    hs = _safe_int(data.get("hs"))
    wr = _safe_float(data.get("wr"))
    avg_kdr = _safe_float(data.get("avg_kdr"))
    avg_krr = _safe_float(data.get("avg_krr"))
    hsp = _safe_float(data.get("hsp"))
    current_elo = _safe_int(data.get("current_elo"))
    highest_elo = _safe_int(data.get("highest_elo"))
    lowest_elo = _safe_int(data.get("lowest_elo"))
    avg_elo = _safe_int(data.get("avg_elo"))
    avg_k = _safe_float(data.get("avg_k"))
    avg_d = _safe_float(data.get("avg_d"))
    diff = _safe_int(data.get("diff"))

    # True overall K/D ratio
    overall_kd = k / d if d > 0 else 0.0

    lines = [
        f"📊 <b>Lifetime Overview for {escape(nickname)}</b>\n",
        f"🎮 Matches: <b>{m:,}</b>  |  🏆 Wins: <b>{w:,}</b>  |  ❌ Losses: <b>{l_val:,}</b>",
        f"📈 Win Rate: <b>{wr:.1f}%</b>\n",
        f"🎯 Total K/A/D: <b>{k:,}</b> / <b>{a:,}</b> / <b>{d:,}</b>",
        f"⚔️ Overall K/D: <b>{overall_kd:.2f}</b>  |  Avg K/D: <b>{avg_kdr:.2f}</b>",
        f"💀 Avg K/R: <b>{avg_krr:.2f}</b>",
        f"🔫 Avg Kills/Match: <b>{avg_k:.1f}</b>  |  Avg Deaths: <b>{avg_d:.1f}</b>",
        f"💥 K-D Diff (total): <b>{'+' if diff >= 0 else ''}{diff:,}</b>",
        f"🎯 Headshots: <b>{hs:,}</b>  |  HS%: <b>{hsp:.1f}%</b>\n",
        f"🏅 ELO: <b>{current_elo}</b>",
        f"   📈 Highest: <b>{highest_elo}</b>  |  📉 Lowest: <b>{lowest_elo}</b>  |  📊 Avg: <b>{avg_elo}</b>",
    ]

    return "\n".join(lines)


# ---- map stats table ----------------------------------------------------- #

_MAP_COLS = [
    ("Map",    8, "<"),
    ("M",      4, ">"),   # matches
    ("WR%",    5, ">"),
    ("K/D",    5, ">"),
    ("K/R",    5, ">"),
    ("HS%",    5, ">"),
    ("AvgK",   5, ">"),
]


def _map_row(values: list[str]) -> str:
    """Build one row for the map-stats table."""
    parts: list[str] = []
    for (_, w, align), val in zip(_MAP_COLS, values):
        parts.append(_vpad(val, w, align))
    return " ".join(parts)


def format_map_stats_table(nickname: str, segments: list[dict[str, Any]]) -> str:
    """Format per-map stats from FaceitAnalyser /api/maps/<id>."""

    if not segments:
        return f"No map stats found for {escape(nickname)}."

    # Sort by number of matches descending
    segments.sort(key=lambda s: _safe_int(s.get("m")), reverse=True)

    header_line = f"🗺️ <b>Map Stats for {escape(nickname)}</b>\n\n"

    hdr = _map_row([h for h, _, _ in _MAP_COLS])
    sep = "-" * _visual_width(hdr)
    lines: list[str] = [hdr, sep]

    for seg in segments:
        map_name = str(seg.get("segment_value", "-"))
        # Strip de_ prefix and capitalize
        if map_name.lower().startswith("de_"):
            map_name = map_name[3:].capitalize()
        else:
            map_name = map_name.capitalize() if map_name != "-" else "-"

        m = _safe_int(seg.get("m"))
        wr = _safe_float(seg.get("wr"))
        kdr = _safe_float(seg.get("avg_kdr"))
        krr = _safe_float(seg.get("avg_krr"))
        hsp = _safe_float(seg.get("hsp"))
        avg_k = _safe_float(seg.get("avg_k"))

        if m == 0:
            continue

        lines.append(_map_row([
            _trunc(map_name, 8),
            str(m),
            f"{wr:.0f}",
            f"{kdr:.2f}",
            f"{krr:.2f}",
            f"{hsp:.0f}",
            f"{avg_k:.1f}",
        ]))

    table_body = escape("\n".join(lines))
    html = f"{header_line}<pre>{table_body}</pre>"

    if len(html) > 4096:
        html = html[:4090] + "</pre>"

    return html


# ---- highlights ---------------------------------------------------------- #

def _normalize_map(raw: str | None) -> str:
    """``de_mirage`` → ``Mirage``."""
    if not raw:
        return ""
    return _DE_PREFIX.sub("", raw.strip()).capitalize()


def _format_highlight_match(match: dict[str, Any], target: str) -> str:
    """Format a single highlight/lowlight match entry."""
    # The 'target' field tells us which field holds the record value
    # Common targets: i6=kills, i7=assists, i8=deaths, c3=kdr, c2=krr, c4=hsp
    val = match.get(target, match.get("k", "?"))
    map_raw = match.get("map", match.get("i1", ""))
    map_name = _normalize_map(map_raw)
    date = match.get("date", "")

    detail = f"<b>{val}</b>"
    if map_name:
        detail += f" ({map_name})"
    if date:
        detail += f" [{date}]"
    return detail


def format_highlights(nickname: str, data: dict[str, Any]) -> str:
    """Format highlights/lowlights from FaceitAnalyser /api/highlights/<id>.

    Actual API structure:
    {"highlights": {"kills": {"matches": [...], "target": "i6", ...}},
     "lowlights": {"kills": {"matches": [...], ...}}}
    """

    lines = [f"🏆 <b>Highlights &amp; Lowlights for {escape(nickname)}</b>"]

    highlights = data.get("highlights", {})
    lowlights = data.get("lowlights", {})

    # Metrics to display (excluding hltv per user request)
    metrics = [
        ("kills", "🎯 Kills"),
        ("assists", "🤝 Assists"),
        ("deaths", "💀 Deaths"),
        ("kdr", "⚔️ K/D Ratio"),
        ("krr", "💥 K/R Ratio"),
        ("headshotpercent", "🔫 HS%"),
        ("diff", "📊 K-D Diff"),
    ]

    for key, label in metrics:
        parts = []

        # Best (highlights)
        hi = highlights.get(key, {})
        if isinstance(hi, dict):
            matches = hi.get("matches", [])
            target = hi.get("target", "k")
            if matches:
                detail = _format_highlight_match(matches[0], target)
                parts.append(f"  📈 Best: {detail}")

        # Worst (lowlights)
        lo = lowlights.get(key, {})
        if isinstance(lo, dict):
            matches = lo.get("matches", [])
            target = lo.get("target", "k")
            if matches:
                detail = _format_highlight_match(matches[0], target)
                parts.append(f"  📉 Worst: {detail}")

        if parts:
            lines.append(f"\n{label}:")
            lines.extend(parts)

    html = "\n".join(lines)

    if len(html) > 4096:
        html = html[:4090] + "…"

    return html


# ---- insights (win vs loss) --------------------------------------------- #

def _pretty_segment_value(seg_val: str, segment: str) -> str:
    """Convert raw segment_value to a user-friendly label."""
    # Win/loss indicators
    if seg_val in ("1", "True", "true"):
        return "🟢 When Winning"
    if seg_val in ("0", "False", "false"):
        return "🔴 When Losing"
    # UUID (used by 'all' segment) — show as "Overall"
    if len(seg_val) > 30 and "-" in seg_val:
        return "📊 Overall"
    # Map names
    if seg_val.startswith("de_"):
        return f"🗺️ {_normalize_map(seg_val)}"
    # Weekday, hour, etc.
    return f"📌 {seg_val}"


def format_insights(
    nickname: str,
    segment: str,
    segments: list[dict[str, Any]],
) -> str:
    """Format win/loss comparison from FaceitAnalyser /api/insights/<id>/<seg>."""

    if not segments:
        return f"No insights data found for {escape(nickname)}."

    # Sort by matches descending
    segments.sort(key=lambda s: _safe_int(s.get("m")), reverse=True)

    lines = [f"🔍 <b>Insights for {escape(nickname)}</b> — <i>{escape(segment)}</i>"]

    for seg in segments:
        seg_val = str(seg.get("segment_value", "—"))
        m = _safe_int(seg.get("m"))
        wr = _safe_float(seg.get("wr"))
        kdr = _safe_float(seg.get("avg_kdr"))
        krr = _safe_float(seg.get("avg_krr"))
        hsp = _safe_float(seg.get("hsp"))
        avg_k = _safe_float(seg.get("avg_k"))
        avg_d = _safe_float(seg.get("avg_d"))

        if m == 0:
            continue

        label = _pretty_segment_value(seg_val, segment)

        lines.append(f"\n<b>{escape(label)}</b> ({m} matches)")
        lines.append(f"  WR: {wr:.0f}%  |  K/D: {kdr:.2f}  |  K/R: {krr:.2f}")
        lines.append(f"  HS%: {hsp:.0f}%  |  Avg K: {avg_k:.1f}  |  Avg D: {avg_d:.1f}")

    html = "\n".join(lines)

    if len(html) > 4096:
        html = html[:4090] + "…"

    return html

