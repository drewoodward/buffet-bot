#!/usr/bin/env python3
"""
Self-contained SVG price charts.

WHY THIS EXISTS, and why every chart it emits carries its own <style>:

The first cycle's charts rendered as solid black rectangles on the dashboard.
The cause was not a colour choice -- it was a document-boundary mistake. Those
SVGs referenced CSS classes (.px, .ink, .grid) that were defined in the
dashboard page's stylesheet. An SVG loaded through <img src="..."> is a
SEPARATE DOCUMENT. It cannot see the parent page's CSS. Every shape therefore
fell back to SVG's default fill, which is black, and the chart painted itself
into an opaque rectangle.

So: this generator embeds a <style> block in every file, and no shape relies on
inherited anything. The embedded style also carries its own
prefers-color-scheme block, so the chart follows the viewer's system theme by
itself rather than borrowing the page's.

Rendering rules follow from that and from the chart being read, not decorated:
one price scale (never two), a recessive grid, a 2px line, direct labels only
where they carry information, and explicit fill on every drawn shape.
"""

from datetime import datetime, timezone

W, H = 880, 420
PAD_L, PAD_R, PAD_T, PAD_B = 8, 96, 54, 34

STYLE = """
<style>
  .bg    { fill: #ffffff; }
  .grid  { stroke: #e6eaef; stroke-width: 1; fill: none; }
  .band  { fill: #c9d6e6; fill-opacity: .42; stroke: none; }
  .px    { stroke: #2f5d8f; stroke-width: 2; fill: none;
           stroke-linejoin: round; stroke-linecap: round; }
  .ma50  { stroke: #8a7320; stroke-width: 1.5; fill: none; stroke-dasharray: 6 4; }
  .ma200 { stroke: #7a6a86; stroke-width: 1.5; fill: none; stroke-dasharray: 2 4; }
  .lvlR  { stroke: #a83f35; stroke-width: 1.5; fill: none; }
  .lvlS  { stroke: #2c7a58; stroke-width: 1.5; fill: none; }
  .lvlK  { stroke: #96691a; stroke-width: 2;   fill: none; }
  .dot   { fill: #2f5d8f; stroke: #ffffff; stroke-width: 2; }
  .ink   { fill: #1b2129; }
  .mut   { fill: #6d7987; }
  .tR    { fill: #a83f35; }
  .tS    { fill: #2c7a58; }
  .tK    { fill: #96691a; }
  .tM    { fill: #8a7320; }
  .last  { fill: #2f5d8f; }
  text   { font-family: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif; }
  .mono  { font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace; }
  @media (prefers-color-scheme: dark) {
    .bg    { fill: #161b23; }
    .grid  { stroke: #262e39; }
    .band  { fill: #3c5573; fill-opacity: .45; }
    .px    { stroke: #7fb0e8; }
    .ma50  { stroke: #d3a642; }
    .ma200 { stroke: #a795b8; }
    .lvlR  { stroke: #e0796c; }
    .lvlS  { stroke: #5cba8d; }
    .lvlK  { stroke: #d3a642; }
    .dot   { fill: #7fb0e8; stroke: #161b23; }
    .ink   { fill: #e3e8ef; }
    .mut   { fill: #8b95a4; }
    .tR    { fill: #e0796c; }
    .tS    { fill: #5cba8d; }
    .tK    { fill: #d3a642; }
    .tM    { fill: #d3a642; }
    .last  { fill: #7fb0e8; }
  }
</style>
"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def sma(values, n):
    out = []
    for i in range(len(values)):
        if i + 1 < n:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - n:i + 1]) / n)
    return out


def render(bars, title, subtitle, levels=(), ma_overlays=(), out_path="chart.svg"):
    """bars: [{time, open, high, low, close}]. levels: [(price, label, kind)] where
    kind is 'R' resistance, 'S' support, 'K' one of Kevin's lines."""
    n = len(bars)
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    ma_series = ma_overlays_series(closes, ma_overlays)
    ma_values = [v for _, series, _ in ma_series for v in series if v is not None]

    lo = min(lows + [p for p, _, _ in levels] + ma_values)
    hi = max(highs + [p for p, _, _ in levels] + ma_values)
    span = (hi - lo) or 1
    lo -= span * 0.06
    hi += span * 0.06
    span = hi - lo

    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    def x(i):
        return PAD_L + (plot_w * i / max(n - 1, 1))

    def y(p):
        return PAD_T + plot_h * (1 - (p - lo) / span)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'role="img" aria-label="{_esc(title)}. {_esc(subtitle)}">',
        STYLE,
        f'<rect x="0" y="0" width="{W}" height="{H}" class="bg"/>',
        f'<text x="{PAD_L}" y="26" class="ink" font-size="18" font-weight="600">{_esc(title)}</text>',
        f'<text x="{PAD_L}" y="44" class="mut" font-size="13">{_esc(subtitle)}</text>',
    ]

    # Recessive grid, four lines, each labelled with a value the chart reaches.
    for k in range(4):
        gy = PAD_T + plot_h * (k + 1) / 5
        price = lo + span * (1 - (k + 1) / 5)
        parts.append(f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{PAD_L+plot_w}" y2="{gy:.1f}" class="grid"/>')
        parts.append(f'<text x="{PAD_L+plot_w+6}" y="{gy+4:.1f}" class="mut mono" font-size="12">{price:,.0f}</text>')

    # High-low range as a band behind the close line: the day's range is real
    # information and a bare close line throws it away.
    top = " ".join(f"{x(i):.1f},{y(b['high']):.1f}" for i, b in enumerate(bars))
    bot = " ".join(f"{x(i):.1f},{y(b['low']):.1f}" for i, b in reversed(list(enumerate(bars))))
    parts.append(f'<polygon class="band" points="{top} {bot}"/>')

    for label, series, cls in ma_series:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(series) if v is not None)
        if pts:
            parts.append(f'<polyline class="{cls}" points="{pts}"/>')
            last_v = [v for v in series if v is not None][-1]
            parts.append(
                f'<text x="{PAD_L+6}" y="{y(last_v)-6:.1f}" class="tM mono" font-size="12" '
                f'font-weight="600">{label} {last_v:,.2f}</text>')

    placed = []
    for idx, (price, label, kind) in enumerate(sorted(levels, key=lambda t: -t[0])):
        cls = {"R": "lvlR", "S": "lvlS", "K": "lvlK"}[kind]
        tcls = {"R": "tR", "S": "tS", "K": "tK"}[kind]
        ly = y(price)
        parts.append(f'<line x1="{PAD_L}" y1="{ly:.1f}" x2="{PAD_L+plot_w}" y2="{ly:.1f}" class="{cls}"/>')

        ty = ly - 5
        while any(abs(ty - q) < 15 for q in placed):
            ty += 15
        placed.append(ty)

        if idx % 2 == 0:
            parts.append(
                f'<text x="{PAD_L+plot_w-6}" y="{ty:.1f}" text-anchor="end" class="{tcls} mono" '
                f'font-size="12.5" font-weight="600">{_esc(label)}</text>')
        else:
            parts.append(
                f'<text x="{PAD_L+6}" y="{ty:.1f}" text-anchor="start" class="{tcls} mono" '
                f'font-size="12.5" font-weight="600">{_esc(label)}</text>')

    pts = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, c in enumerate(closes))
    parts.append(f'<polyline class="px" points="{pts}"/>')
    parts.append(f'<circle cx="{x(n-1):.1f}" cy="{y(closes[-1]):.1f}" r="4.5" class="dot"/>')
    parts.append(
        f'<text x="{x(n-1)+9:.1f}" y="{y(closes[-1])+5:.1f}" class="last mono" font-size="14.5" '
        f'font-weight="600">{closes[-1]:,.2f}</text>')

    for i, anchor in ((0, "start"), (n // 2, "middle"), (n - 1, "end")):
        d = datetime.fromtimestamp(bars[i]["time"], timezone.utc).strftime("%-d %b")
        parts.append(f'<text x="{x(i):.1f}" y="{H-10}" text-anchor="{anchor}" class="mut mono" font-size="12">{d}</text>')

    parts.append("</svg>")
    svg = "\n".join(parts)
    with open(out_path, "w") as f:
        f.write(svg)
    return out_path, len(svg)


def ma_overlays_series(closes, spec):
    out = []
    for n, cls in ((50, "ma50"), (200, "ma200")):
        if n in spec:
            out.append((f"{n}-day", sma(closes, n), cls))
    return out
