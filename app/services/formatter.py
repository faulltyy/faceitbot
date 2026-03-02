"""Monospaced table formatter for Telegram ``<pre>`` blocks.

The main entry-point is :func:`format_matches_table` which accepts a list of
pre-parsed match dicts and returns an HTML string ready for
``bot.send_message(parse_mode="HTML")``.
"""

from __future__ import annotations

from html import escape
from typing import Any


# ---- helpers ------------------------------------------------------------- #

def safe_truncate(text: str, width: int) -> str:
    """Truncate *text* to *width* characters, adding ``…`` when shortened."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def safe_float(value: Any, precision: int = 2) -> str:
    """Convert *value* to a formatted float string, or ``"-"`` on failure."""
    try:
        return f"{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "-"


def format_elo(elo_diff: int | float | None, current_elo: int | float | None) -> str:
    """Format ELO column: ``+25(2115)`` or ``N/A``."""
    if elo_diff is None or current_elo is None:
        return "N/A"
    try:
        diff = int(elo_diff)
        elo = int(current_elo)
        sign = "+" if diff >= 0 else ""
        return f"{sign}{diff}({elo})"
    except (TypeError, ValueError):
        return "N/A"


# ---- public API ---------------------------------------------------------- #

# Column widths (tuned for mobile Telegram monospace ~42-44 chars per line)
_W = {
    "#":   2,   # match index
    "R":   1,   # W / L
    "Map": 8,   # map name
    "K":   2,   # kills
    "K/D": 4,   # k/d ratio
    "K/R": 4,   # k/r ratio
    "ADR": 6,   # adr
    "ELO": 10,  # elo diff + current
}


def _header_line() -> str:
    """Build the fixed-width column header."""
    return (
        f"{'#':>{_W['#']}} "
        f"{'R':<{_W['R']}} "
        f"{'Map':<{_W['Map']}} "
        f"{'K':>{_W['K']}} "
        f"{'K/D':>{_W['K/D']}} "
        f"{'K/R':>{_W['K/R']}} "
        f"{'ADR':>{_W['ADR']}} "
        f"{'ELO':>{_W['ELO']}}"
    )


def _data_line(
    idx: int,
    result: str,
    map_name: str,
    kills: int,
    kd: str,
    kr: str,
    adr: str,
    elo_str: str,
) -> str:
    """Build one fixed-width data row."""
    return (
        f"{idx:>{_W['#']}} "
        f"{result:<{_W['R']}} "
        f"{safe_truncate(map_name, _W['Map']):<{_W['Map']}} "
        f"{kills:>{_W['K']}} "
        f"{kd:>{_W['K/D']}} "
        f"{kr:>{_W['K/R']}} "
        f"{adr:>{_W['ADR']}} "
        f"{elo_str:>{_W['ELO']}}"
    )


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
        and optionally ``elo_diff``, ``elo_after``.
    current_elo:
        The player's current FACEIT ELO (shown in the header).

    Returns
    -------
    str
        HTML string safe for ``parse_mode="HTML"``; the table lives inside
        a ``<pre>`` block for monospaced alignment.
    """

    if not matches:
        return "No recent CS2 matches found."

    total = len(matches)

    # ---- emoji header (outside <pre>) ------------------------------------ #
    elo_badge = f"  |  ELO: {current_elo}" if current_elo else ""
    header = f"📊 Last {total} CS2 matches for {escape(nickname)}{elo_badge}\n\n"

    # ---- monospaced table (inside <pre>) --------------------------------- #
    lines: list[str] = [_header_line()]
    lines.append("-" * len(lines[0]))  # separator

    for idx, m in enumerate(matches, start=1):
        result = "W" if m.get("win") is True else "L"
        map_name = m.get("map") or "-"
        kills = int(m.get("kills", 0))
        kd = safe_float(m.get("kd"))
        kr = safe_float(m.get("kr"))
        adr = safe_float(m.get("adr"))
        elo_str = format_elo(m.get("elo_diff"), m.get("current_elo"))

        lines.append(_data_line(idx, result, map_name, kills, kd, kr, adr, elo_str))

    table_body = escape("\n".join(lines))

    # ---- assemble -------------------------------------------------------- #
    html = f"{header}<pre>{table_body}</pre>"

    # Telegram messages are capped at 4096 characters
    if len(html) > 4096:
        html = html[:4090] + "</pre>"

    return html
