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
