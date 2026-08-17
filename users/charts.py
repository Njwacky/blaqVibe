"""Server-rendered SVG charts for the earnings page.

5 Whys:
1. Why server-rendered SVG instead of a JS chart library? The site ships
   zero third-party JS and must work with JS disabled; a CDN chart lib
   would add a network dependency and a CSP surface for one page. SVG is
   markup — inspectable, testable, selectable by screen readers.
2. Why chart the LEDGER (StarEvent) instead of counters? views/clones/
   stars are cumulative integers with no history — a chart of them would
   be a lie. StarEvent is the one append-only time series the wallet
   owns, so the charts show exactly what the ledger knows.
3. Why precompute coordinates in Python instead of the template?
   Scaling math (heights, padding, nice-max rounding) is logic; logic
   lives in Python where tests can assert it. The template stays
   declarative.
4. Why last 14 days? Long enough to see a trend, short enough that bars
   stay readable at ~47px per day.
5. Why do the generators return None for all-zero input instead of an
   empty chart? An empty axes box would read as "you earned nothing"
   when the truth is "the ledger has no rows yet" — the caller renders
   an honest empty state instead.

All output is generated from dates and integers only — no user text ever
reaches these strings, so the template's |safe is XSS-free by
construction.
"""
import math
from html import escape

CHART_W, CHART_H = 700, 190
PAD_L, PAD_R, PAD_T, PAD_B = 34, 10, 14, 24
PLOT_W = CHART_W - PAD_L - PAD_R
PLOT_H = CHART_H - PAD_T - PAD_B
BASE_Y = PAD_T + PLOT_H

EARNED_FILL = '#10B981'
SPENT_FILL = '#EF4444'


def _svg_start(label):
    return (
        f'<svg viewBox="0 0 {CHART_W} {CHART_H}" role="img" aria-label="{label}" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        f'<title>{label}</title>'
    )


def _gridlines(parts, values, fmt):
    """Horizontal gridlines at the given values, with value labels."""
    for v in values:
        frac = v / values[-1] if values[-1] else 0
        y = BASE_Y - frac * PLOT_H
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{CHART_W - PAD_R}" y2="{y:.1f}" '
            f'stroke="var(--line)" stroke-width="1" stroke-dasharray="2 3"/>'
        )
        parts.append(
            f'<text x="{PAD_L - 6}" y="{y + 3:.1f}" text-anchor="end" font-size="9" '
            f'fill="var(--muted)">{fmt(v)}</text>'
        )


def activity_chart(days, max_val):
    """Two-tone bars: earned vs spent per day. Returns None when flat-empty.

    days: list of {'date': date, 'earned': int, 'spent': int} (14 entries).
    """
    if max_val <= 0:
        return None
    nice = max(5, int(math.ceil(max_val / 5.0)) * 5)
    parts = [_svg_start('Stars earned vs spent, last 14 days')]
    _gridlines(parts, [0, nice / 2, nice], lambda v: str(int(v)))
    slot = PLOT_W / len(days)
    bar_w = min(16.0, slot * 0.34)
    for i, d in enumerate(days):
        x = PAD_L + i * slot + slot / 2
        h_earn = (d['earned'] / nice) * PLOT_H
        h_spent = (d['spent'] / nice) * PLOT_H
        if h_earn > 0:
            parts.append(
                f'<rect x="{x - bar_w - 1.5:.1f}" y="{BASE_Y - h_earn:.1f}" '
                f'width="{bar_w:.1f}" height="{h_earn:.1f}" rx="2" fill="{EARNED_FILL}"/>'
            )
        if h_spent > 0:
            parts.append(
                f'<rect x="{x + 1.5:.1f}" y="{BASE_Y - h_spent:.1f}" '
                f'width="{bar_w:.1f}" height="{h_spent:.1f}" rx="2" fill="{SPENT_FILL}"/>'
            )
        parts.append(
            f'<text x="{x:.1f}" y="{CHART_H - 6}" text-anchor="middle" font-size="8" '
            f'fill="var(--muted)">{d["date"].day}</text>'
        )
    # Legend (top-right).
    parts.append('<g font-size="9" fill="var(--muted)">')
    parts.append(
        f'<rect x="{CHART_W - PAD_R - 120}" y="{PAD_T}" width="8" height="8" rx="2" '
        f'fill="{EARNED_FILL}"/><text x="{CHART_W - PAD_R - 108}" y="{PAD_T + 8}">earned</text>'
    )
    parts.append(
        f'<rect x="{CHART_W - PAD_R - 62}" y="{PAD_T}" width="8" height="8" rx="2" '
        f'fill="{SPENT_FILL}"/><text x="{CHART_W - PAD_R - 50}" y="{PAD_T + 8}">spent</text>'
    )
    parts.append('</g>')
    parts.append('</svg>')
    return ''.join(parts)


def balance_chart(trend, min_bal, max_bal):
    """Line chart of wallet balance at the end of each day.

    Returns None only when the ledger has no rows AND the balance is zero
    (nothing real to draw). A steady non-zero balance is drawn as a flat
    line at its true level — that IS the truth.
    """
    if min_bal == 0 and max_bal == 0:
        return None
    span = max_bal - min_bal
    pad = span * 0.1 or max(1.0, abs(max_bal) * 0.1)

    def y_for(v):
        if span == 0:
            return BASE_Y - PLOT_H / 2
        return BASE_Y - ((v - (min_bal - pad)) / (span + 2 * pad)) * PLOT_H

    parts = [_svg_start('Wallet balance, last 14 days')]
    lo, hi = min_bal - pad, max_bal + pad
    _gridlines(parts, [lo, (lo + hi) / 2, hi], lambda v: f'{v:.0f}')
    slot = PLOT_W / len(trend)
    points = []
    for i, bal in enumerate(trend):
        x = PAD_L + i * slot + slot / 2
        points.append(f'{x:.1f},{y_for(bal):.1f}')
    parts.append(
        f'<polyline points="{" ".join(points)}" fill="none" stroke="var(--link)" '
        f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
    )
    # End dot + final value label (the current balance).
    last_x = PAD_L + (len(trend) - 1) * slot + slot / 2
    last_y = y_for(trend[-1])
    parts.append(f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3" fill="var(--link)"/>')
    parts.append(
        f'<text x="{last_x:.1f}" y="{last_y - 6:.1f}" text-anchor="middle" font-size="10" '
        f'font-weight="700" fill="var(--link)">{trend[-1]:.0f}★</text>'
    )
    parts.append('</svg>')
    return ''.join(parts)


# --- Admin dashboard builders ------------------------------------------------
# 5 Whys (why these live here, sharing the earnings chart's math):
# 1. Why reuse this module? The axis/scale/gridline decisions are already
#    tested and CSS-variable aware; two chart engines would drift.
# 2. Why generic builders instead of one function per metric? The admin
#    dashboard charts ~6 time series; a parameterised builder keeps each
#    metric a data-shape problem, not a drawing problem.
# 3. Why does every builder return None on all-zero input? Same rule as
#    the earnings charts: an empty axes box would read "the site did
#    nothing" when the truth is "we only started logging this recently".
#    The caller renders an honest empty state.
# 4. Why is user text (vibe titles) escaped here? These SVGs are injected
#    with |safe in the template; the ONLY trusted path to |safe is
#    escaping at the boundary where user text enters the markup.
# 5. Why no third-party chart lib? The site ships zero third-party JS and
#    must work with JS disabled — see the module docstring.

def daily_bars_chart(label, days, series, stacked=True, fmt=str):
    """Vertical bars per day for one or more series.

    days:  list of datetime.date
    series: [{'name': str, 'color': str, 'values': [int,...]}] (len == len(days))
    Returns None when every value across every series is zero.
    """
    total_max = 0
    for s in series:
        total_max = max(total_max, max(s['values'], default=0))
    if total_max <= 0:
        return None
    if stacked:
        nice = max(5, int(math.ceil(total_max / 5.0)) * 5)
    else:
        nice = total_max
    parts = [_svg_start(label)]
    _gridlines(parts, [0, nice / 2, nice], lambda v: fmt(v))
    slot = PLOT_W / max(len(days), 1)
    bar_w = min(18.0, slot * 0.62)
    for i, d in enumerate(days):
        x = PAD_L + i * slot + slot / 2
        y_cursor = BASE_Y
        for s in series:
            v = s['values'][i] if i < len(s['values']) else 0
            h = (v / nice) * PLOT_H if v else 0
            if h > 0:
                if stacked:
                    y_cursor -= h
                    y = y_cursor
                else:
                    y = BASE_Y - h
                parts.append(
                    f'<rect x="{x - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                    f'height="{h:.1f}" rx="2" fill="{s["color"]}">'
                    f'<title>{escape(s["name"])} — {d.isoformat()}: {v}</title></rect>'
                )
            if not stacked:
                y_cursor = BASE_Y
        # Month tick on the 1st, day number otherwise.
        tick = d.strftime('%b') if d.day == 1 or (i == 0 and len(days) <= 7) else str(d.day)
        parts.append(
            f'<text x="{x:.1f}" y="{CHART_H - 6}" text-anchor="middle" font-size="8" '
            f'fill="var(--muted)">{escape(tick)}</text>'
        )
    # Legend.
    parts.append('<g font-size="9" fill="var(--muted)">')
    lx = CHART_W - PAD_R - 8
    for s in reversed(series):
        text = escape(s['name'])
        width = len(text) * 5.2 + 16
        lx -= width
        parts.append(
            f'<rect x="{lx:.0f}" y="{PAD_T}" width="8" height="8" rx="2" fill="{s["color"]}"/>'
            f'<text x="{lx + 11:.0f}" y="{PAD_T + 8}">{text}</text>'
        )
    parts.append('</g>')
    parts.append('</svg>')
    return ''.join(parts)


def h_bars_chart(label, items, hrefs=None, fmt=str, bar_color='var(--link)'):
    """Horizontal bars for rankings (top vibes by stars/clones).

    items: [{'label': str, 'value': int}] (biggest first)
    hrefs: optional parallel list of relative URLs — the label becomes a link.
    Returns None when every value is zero.
    """
    if not items or max(i['value'] for i in items) <= 0:
        return None
    row_h = 24
    height = 34 + row_h * len(items) + 14
    parts = [
        f'<svg viewBox="0 0 {CHART_W} {height}" role="img" aria-label="{label}" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">'
        f'<title>{label}</title>'
    ]
    label_w = 150
    bar_x0 = PAD_L + label_w
    bar_max_w = CHART_W - bar_x0 - PAD_R - 46
    top = max(i['value'] for i in items)
    for idx, item in enumerate(items):
        y = 22 + idx * row_h
        value = item['value']
        w = (value / top) * bar_max_w if value else 0
        text = escape(str(item['label'])[:26] + ('…' if len(str(item['label'])) > 26 else ''))
        parts.append(
            f'<text x="{PAD_L + label_w - 8}" y="{y + 12:.0f}" text-anchor="end" '
            f'font-size="10" fill="var(--muted)">{text}</text>'
        )
        if hrefs and idx < len(hrefs) and hrefs[idx]:
            parts.append(f'<a href="{escape(hrefs[idx])}">')
        parts.append(
            f'<rect x="{bar_x0}" y="{y}" width="{w:.1f}" height="14" rx="3" '
            f'fill="{bar_color}" opacity="0.9">'
            f'<title>{text} — {value}</title></rect>'
        )
        parts.append(
            f'<text x="{bar_x0 + w + 6:.1f}" y="{y + 11:.0f}" font-size="10" '
            f'fill="var(--muted)">{fmt(value)}</text>'
        )
        if hrefs and idx < len(hrefs) and hrefs[idx]:
            parts.append('</a>')
    parts.append('</svg>')
    return ''.join(parts)
