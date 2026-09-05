"""Render the flexviz README hero chart (site_data/benchmark-ttfr*.svg).

Reads the site_data/benchmarks.json that export_site.py derived through the
publication gate and emits the two-panel time-to-first-render bar chart as
light and dark SVGs. The flexviz README embeds these by raw URL, so pushing
this repo is what updates the README image — no flexviz commit involved.
`make site-data` runs this after export_site.py; the SVGs can never carry a
number the gate did not pass.

Design notes: this is a highlight palette (accent = FlexViz, recessive greys =
comparators), not a categorical one; identity is never color-alone because
every bar is direct-labeled with engine name and value. Text wears ink tokens,
never the series color.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROWS = 200_000_000
TRACES = "5"
SOURCE = "in-memory"
PANELS = [("LINE", "line"), ("HISTOGRAM", "histogram")]
DISPLAY = {"mosaic-server": "mosaic"}

THEMES = {
    "": {  # light — flexviz.tech brand tokens
        "bg": "#FAFAF7",
        "border": "rgba(18,18,18,0.08)",
        "ink": "#121212",
        "sub": "rgba(18,18,18,0.62)",
        "muted": "rgba(18,18,18,0.45)",
        "track": "#EFEFEB",
        "accent": "#E8590C",
        "grey_strong": "#64645F",
        "grey_soft": "#ADADAA",
    },
    "-dark": {
        "bg": "#161614",
        "border": "rgba(237,237,234,0.10)",
        "ink": "#EDEDEA",
        "sub": "rgba(237,237,234,0.62)",
        "muted": "rgba(237,237,234,0.42)",
        "track": "#262623",
        "accent": "#E8590C",
        "grey_strong": "#8F8F89",
        "grey_soft": "#5F5F5A",
    },
}

FONT = "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

# viewBox geometry; the README displays the SVG at width 850 so everything
# scales, only the ratios matter.
W, H = 1000, 158
PANEL_X = (36, 532)
NAME_X = 96  # right edge of the engine-name column, relative to panel
TRACK_X = 108
TRACK_W = 330
TRACK_H = 20
ROW_Y0 = 50
ROW_PITCH = 30
LABEL_RESERVE = 116  # track pixels kept free so the value label fits inside
BAR_MIN = 8


def panel_rows(data: dict, chart: str) -> list[tuple[str, float]]:
    series = data["charts"][chart]["timing"][SOURCE][TRACES]
    rows = []
    for tool, points in series.items():
        ms = dict((int(r), v) for r, v in points).get(ROWS)
        if ms is not None:
            rows.append((DISPLAY.get(tool, tool), ms))
    rows.sort(key=lambda r: r[1])
    return rows


def fmt(ms: float) -> str:
    return f"{round(ms):,} ms"


def render(data: dict, t: dict) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="{FONT}">',
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="10" '
        f'fill="{t["bg"]}" stroke="{t["border"]}"/>',
    ]
    for (title, chart), px in zip(PANELS, PANEL_X):
        rows = panel_rows(data, chart)
        scale = (TRACK_W - LABEL_RESERVE) / max(ms for _, ms in rows)
        parts.append(
            f'<text x="{px + 8}" y="34" font-size="11" letter-spacing="2.5" '
            f'fill="{t["muted"]}">{title}</text>'
        )
        for i, (name, ms) in enumerate(rows):
            y = ROW_Y0 + i * ROW_PITCH
            color = (
                t["accent"]
                if name == "flexviz"
                else t["grey_strong"]
                if name == "mosaic"
                else t["grey_soft"]
            )
            name_fill = t["ink"] if name == "flexviz" else t["sub"]
            weight = "600" if name == "flexviz" else "400"
            bar_w = max(BAR_MIN, ms * scale)
            parts += [
                f'<text x="{px + NAME_X}" y="{y + 15}" font-size="13.5" '
                f'text-anchor="end" font-weight="{weight}" '
                f'fill="{name_fill}">{name}</text>',
                f'<rect x="{px + TRACK_X}" y="{y}" width="{TRACK_W}" '
                f'height="{TRACK_H}" rx="6" fill="{t["track"]}"/>',
                f'<rect x="{px + TRACK_X}" y="{y}" width="{bar_w:.1f}" '
                f'height="{TRACK_H}" rx="4" fill="{color}"/>',
                f'<text x="{px + TRACK_X + bar_w + 10:.1f}" y="{y + 15}" '
                f'font-size="13.5" fill="{t["ink"]}">{fmt(ms)}</text>',
            ]
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data",
        type=Path,
        default=Path(__file__).parent.parent / "site_data" / "benchmarks.json",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent.parent / "site_data",
    )
    args = ap.parse_args()
    data = json.loads(args.data.read_text())

    for suffix, theme in THEMES.items():
        out = args.out / f"benchmark-ttfr{suffix}.svg"
        out.write_text(render(data, theme) + "\n")
        print(f"wrote {out}")

    alt = []
    for title, chart in PANELS:
        pairs = ", ".join(f"{n} {fmt(ms)}" for n, ms in panel_rows(data, chart))
        alt.append(f"{title.capitalize()}: {pairs}.")
    print("\nalt text:")
    print(
        f"Time to first render at {ROWS // 1_000_000}M rows and {TRACES} traces. " + " ".join(alt)
    )


if __name__ == "__main__":
    main()
