"""Monospaced table formatter for Telegram ``<pre>`` blocks.

The main entry-point is :func:`format_matches_table` which accepts a list of
pre-parsed match dicts and returns an HTML string ready for
``bot.send_message(parse_mode="HTML")``.
"""

from __future__ import annotations

from html import escape
from typing import Any


# ---- helpers ------------------------------------------------------------- #

def _trunc(text: str, w: int) -> str:
    """Truncate *text* to *w* chars, appending ``…`` when shortened."""
    return text if len(text) <= w else text[: w - 1] + "…"


def _fmt(value: Any, precision: int = 2) -> str:
    """Float → string, or ``-`` on failure."""
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "-"


def _elo(diff: int | float | None, elo: int | float | None) -> str:
    """Format ELO column: ``+25(2115)`` or ``N/A``."""
    if diff is None or elo is None:
        return "N/A"
    try:
        d, e = int(diff), int(elo)
        return f"{'+' if d >= 0 else ''}{d}({e})"
    except (TypeError, ValueError):
        return "N/A"


# ---- table layout -------------------------------------------------------- #

# Column spec: (header, width, align)
#   align: '<' left, '>' right
_COLS = [
    ("#",   2, ">"),
    ("R",   1, "<"),
    ("Map", 7, "<"),
    ("K",   2, ">"),
    ("K/D", 4, ">"),
    ("K/R", 4, ">"),
    ("ADR", 5, ">"),
    ("ELO", 10, ">"),
]

_SEP = " "  # single space between columns


def _row(values: list[str]) -> str:
    """Build one fixed-width row from a list of pre-formatted cell strings."""
    parts: list[str] = []
    for (_, w, align), val in zip(_COLS, values):
        parts.append(f"{val:{align}{w}}")
    return _SEP.join(parts)


# ---- public API ---------------------------------------------------------- #

def format_matches_table(
    nickname: str,
    matches: list[dict[str, Any]],
    current_elo: int | None = None,
) -> str:
    """Return a fully formatted HTML string for the last-N matches table.

    Parameters
    ----------
    nickname:
        The FACEIT nickname (displayed in the title).
    matches:
        Pre-parsed match dicts, each containing at minimum:
        ``map``, ``kills``, ``kd``, ``kr``, ``adr``, ``win``
        and optionally ``elo_diff``, ``current_elo``.
    current_elo:
        The player's current FACEIT ELO (shown in the header).
    """
    if not matches:
        return "No recent CS2 matches found."

    total = len(matches)

    # ---- emoji header (outside <pre>) ------------------------------------ #
    elo_badge = f"  |  ELO: {current_elo}" if current_elo else ""
    header = f"📊 Last {total} CS2 matches for {escape(nickname)}{elo_badge}\n\n"

    # ---- build table ----------------------------------------------------- #
    hdr = _row([h for h, _, _ in _COLS])
    sep = "-" * len(hdr)
    lines: list[str] = [hdr, sep]

    for idx, m in enumerate(matches, start=1):
        result = "W" if m.get("win") is True else "L"
        map_name = m.get("map") or "-"
        kills = str(int(m.get("kills", 0)))
        kd = _fmt(m.get("kd"))
        kr = _fmt(m.get("kr"))
        adr = _fmt(m.get("adr"), precision=1)
        elo_str = _elo(m.get("elo_diff"), m.get("current_elo"))

        lines.append(_row([
            str(idx),
            result,
            _trunc(map_name, 7),
            kills,
            kd,
            kr,
            adr,
            elo_str,
        ]))

    table_body = escape("\n".join(lines))

    # ---- assemble -------------------------------------------------------- #
    html = f"{header}<pre>{table_body}</pre>"

    # Telegram messages are capped at 4096 characters
    if len(html) > 4096:
        html = html[:4090] + "</pre>"

    return html
