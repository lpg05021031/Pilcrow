"""Code, diffs and terminal output as HTML for a document node (the renderers of
widgets/transcript/bodies/code.py as functions)."""

from __future__ import annotations

import html
from typing import List, Optional, Sequence, Tuple

from ... import theme
from ...lib.fmt import lang_from_path
from ...widgets.transcript.bodies.code import LINE, DiffHunk, DiffLine, highlight_lines, mark_html, word_ranges

__all__ = ["LINE", "code_html", "diff_html"]


def code_html(
    text: str,
    path: str = "",
    lang: str = "",
    start_line: int = 1,
    max_lines: int = 400,
    highlight_line: Optional[int] = None,
    expanded: bool = False,
) -> Tuple[str, int, int]:
    """Numbered lines as a table; returns (html, total lines, shown lines)."""
    t = theme.tokens()
    lines = text.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    language = lang or (lang_from_path(path) if path else "text")
    shown = lines if expanded else lines[:max_lines]
    hl = highlight_lines("\n".join(shown), language) if len(lines) < 20000 else [html.escape(x) for x in shown]
    hit_bg = theme.blend(t.accent_wash, t.inset).name()
    rows: List[str] = []
    for i, body in enumerate(hl):
        n = start_line + i
        bg = f' bgcolor="{hit_bg}"' if highlight_line == n else ""
        rows.append(
            f'<tr><td width="1" align="right" style="color:{t.text_4};padding-left:14px;padding-right:12px"{bg}>'
            f'<p style="margin:0;line-height:{LINE}px">{n}</p></td><td style="padding-right:16px"{bg}>'
            f'<p style="margin:0;white-space:pre-wrap;line-height:{LINE}px">{body or "&nbsp;"}</p></td></tr>'
        )
    return f'<table cellspacing="0" cellpadding="0" width="100%">{"".join(rows)}</table>', len(lines), len(shown)


def diff_html(
    hunks: Sequence[DiffHunk], path: str = "", numbers: bool = True, max_rows: int = 0, expanded: bool = False
) -> Tuple[str, int, int]:
    """Unified hunks as numbered rows tinted with the diff tokens; returns (html, total rows, shown rows)."""
    t = theme.tokens()
    lang = lang_from_path(path) if path else "text"
    rows_all: List[Tuple[str, Optional[DiffLine], Optional[DiffHunk], int]] = []
    for hi, h in enumerate(hunks):
        if len(hunks) > 1 or h.old_start > 1 or h.header:
            rows_all.append(("hunk", None, h, hi))
        for ln in h.lines:
            rows_all.append(("line", ln, None, hi))
    limit = len(rows_all) if (not max_rows or expanded) else min(max_rows, len(rows_all))
    shown = rows_all[:limit]
    words = word_ranges(hunks)
    toks = {}
    for h in hunks:
        old_l = [ln for ln in h.lines if ln.t != "+"]
        new_l = [ln for ln in h.lines if ln.t != "-"]
        for group in (old_l, new_l):
            for ln, hl in zip(group, highlight_lines("\n".join(x.s for x in group), lang)):
                toks[ln] = hl
    add_bg = theme.blend(t.diff_add, t.inset).name()
    del_bg = theme.blend(t.diff_del, t.inset).name()
    add_hl = theme.blend(t.diff_add_hl, add_bg).name()
    del_hl = theme.blend(t.diff_del_hl, del_bg).name()
    hunk_bg = theme.blend(t.fill_1, t.inset).name()
    ui = theme.font_ui().family()
    cols = 3 if numbers else 2
    out: List[str] = []
    for kind, ln, h, hi in shown:
        if kind == "hunk":
            assert h is not None
            prev = hunks[hi - 1] if hi > 0 else None
            gap = h.old_start - (prev.old_start + prev.old_lines) if prev else h.old_start - 1
            text = f"{gap} unchanged line{'s' if gap > 1 else ''}" if gap > 0 else ""
            ctx = f' <span style="color:{t.text_4};font-size:11px">{html.escape(h.header)}</span>' if h.header else ""
            out.append(
                f'<tr><td colspan="{cols}" style="padding-left:14px;padding-right:14px;padding-top:4px;'
                f"padding-bottom:4px\"><p style=\"margin:0;line-height:26px;font-family:'{ui}';"
                f'font-size:{theme.FS_MICRO}px;color:{t.text_3};background-color:{hunk_bg}">'
                f'<span style="color:{t.text_4};letter-spacing:1px">⋯</span>&nbsp;&nbsp;'
                f"{html.escape(text)}{ctx}</p></td></tr>"
            )
            continue
        assert ln is not None
        if ln.t == "+":
            bg, sign, sign_c, num_c, hl_c = add_bg, "+", t.ok, t.text_3, add_hl
        elif ln.t == "-":
            bg, sign, sign_c, num_c, hl_c = del_bg, "−", t.err, t.text_3, del_hl
        else:
            bg, sign, sign_c, num_c, hl_c = "", "&nbsp;", t.text_4, t.text_4, ""
        bga = f' bgcolor="{bg}"' if bg else ""
        ranges = words.get(ln)
        body = mark_html(ln.s, ranges, hl_c) if ranges else toks.get(ln, html.escape(ln.s))
        cells = ""
        line = f'<p style="margin:0;line-height:{LINE}px">'
        if numbers:
            num = ln.o if ln.t == "-" else ln.n
            cells += (
                f'<td width="1" align="right" style="color:{num_c};padding-left:14px;padding-right:8px"{bga}>'
                f"{line}{num if num is not None else '&nbsp;'}</p></td>"
            )
        cells += (
            f'<td width="1" align="center" style="color:{sign_c};padding-left:5px;padding-right:5px"{bga}>'
            f"{line}{sign}</p></td>"
        )
        cells += (
            f'<td style="padding-right:18px"{bga}>'
            f'<p style="margin:0;white-space:pre-wrap;line-height:{LINE}px">{body or "&nbsp;"}</p></td>'
        )
        out.append(f"<tr>{cells}</tr>")
    return f'<table cellspacing="0" cellpadding="0" width="100%">{"".join(out)}</table>', len(rows_all), len(shown)
