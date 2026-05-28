# Report Enhancements Design

**Date:** 2026-05-28
**File:** `benchmarks/report.py`

## Goal

Improve the benchmark HTML report with four enhancements:
1. Log/linear x-axis toggle for the rows-scaling chart
2. p25–p75 shaded bands showing measurement spread
3. A styled HTML separator with explanatory content between the two chart sections
4. A benchmark metadata card showing run configuration and notes

## Architecture

The report output shifts from a single `make_subplots` figure to a **full custom HTML page** assembled from two separate Plotly figures plus HTML/CSS sections.

Page structure (top to bottom):
1. **Metadata card** — styled HTML showing benchmark config and notes
2. **Figure 1** — rows-scaling chart with log/linear toggle, p25–p75 bands, and the shared legend
3. **Separator section** — styled HTML divider with two description cards (one per chart section)
4. **Figure 2** — traces-scaling chart with p25–p75 bands, no legend (shared via figure 1)

The page is assembled in a new `build_page()` function that calls `fig.to_html(full_html=False)` on each figure and wraps them in a hand-authored HTML template with embedded CSS.

## Components

### `load_json(path)` (replaces `load_summaries`)
Returns the full JSON dict. Callers destructure `config`, `notes`, `summary`, and `trials` from it.

### `compute_bands(trials_json) -> dict`
Pre-computes p25 and p75 for each metric from raw trial values.

- Input: `trials` dict nested as `rows → n_traces → source → tool → [trial, ...]`
- Output: lookup dict keyed by `(rows, n_traces, source, tool, metric_field)` → `(p25, p75)`
- Metrics: `total_ms`, `query_ms`, `transfer_ms`, `render_ms`, `peak_python_mb`, `peak_browser_mb`
- For nullable metrics (query, transfer, render), filters `None` before computing; only produces a band entry if ≥ 2 non-None values exist
- Uses `statistics.quantiles(data, n=4)` to get quartiles

### `build_figure(summaries, bands, *, x_key, row_filter, metrics, show_legend, add_toggle, ...)`
Builds a single-row Plotly figure (rows-scaling or traces-scaling).

- For each `(tool, source, metric)` combination, adds:
  - A `Scatter` line+markers trace for the median (existing behavior)
  - A lower-bound `Scatter` trace at p25 (transparent line, no legend)
  - An upper-bound `Scatter` trace at p75 with `fill='tonexty'` at ~20% opacity of the tool color (no legend)
- All three traces share the same `legendgroup=f"{tool}__{src}"`
- Band traces: `showlegend=False`, `hoverinfo='skip'`, `line=dict(width=0)`
- Tool color with 20% opacity: computed as `rgba(r, g, b, 0.2)` by parsing the hex color
- Shared y-axes within timing and memory groups (same logic as current code)
- `add_toggle=True`: adds the `updatemenus` log/linear toggle (figure 1 only)
- `show_legend=True`: median traces show in legend (first per legendgroup); `False` suppresses all legend entries (figure 2)

### Log/linear toggle (figure 1 only)
A Plotly `updatemenus` button group with two buttons: **"Log"** and **"Linear"**.

Each button calls `Plotly.relayout` updating `xaxis.type`, `xaxis2.type`, … for all columns in figure 1 simultaneously. Default state: log scale (matching current behavior).

Buttons positioned top-right above figure 1.

### Cross-figure legend linking (JS)
A `<script>` block in the page HTML listens to `plotly_restyle` on figure 1's div (fires after visibility is applied). On each event it:
1. Checks if the restyle update contains a `visible` key
2. Reads the current visibility of all traces in figure 1 from `gd.data`
3. Matches each trace to figure 2 by `legendgroup`
4. Calls `Plotly.restyle(gd2, {visible: [...]})` on figure 2 to mirror visibility

Using `plotly_restyle` (post-change) rather than `plotly_legendclick` (pre-change) means `gd.data` already reflects the new state when the handler runs.

This keeps the single legend in figure 1 interactive for both charts.

### `build_page(fig1, fig2, config, notes, dims, fixed_n_traces, fixed_rows) -> str`
Assembles the complete HTML string.

**Metadata card** shows:
- Tools benchmarked (from `dims["tools"]`)
- Sizes (formatted with K/M suffixes)
- n_traces values
- Data sources
- Repeats + warmup
- Seed
- Benchmark-specific fields present in config: `bins` (histogram), `n_points` (line)
- Notes as a bulleted list

**Separator section** contains:
- A horizontal rule / visual divider
- Two side-by-side description cards:
  - *Rows scaling* — "How render time grows as dataset size increases, with number of traces fixed at {fixed_n_traces}"
  - *Traces scaling* — "How render time grows as more data series are added, with dataset size fixed at {fixed_rows:,} rows"

**CSS** is embedded in a `<style>` block in the `<head>`. Uses system font stack, neutral color palette, clean card styling with subtle shadows, and a responsive grid for the metadata fields.

### `_hex_to_rgba(hex_color, alpha) -> str`
Helper that converts a `#rrggbb` string to `rgba(r, g, b, alpha)` for the band fill colors.

## Data Flow

```
JSON file
  ├── config  ──────────────────────────────────────────► build_page (metadata card)
  ├── notes   ──────────────────────────────────────────► build_page (notes list)
  ├── summary ──► _detect_dimensions ──► dims
  │                                        ├──────────► build_figure (x/y data)
  │                                        └──────────► build_page (separator text)
  └── trials  ──► compute_bands ──► bands ──────────► build_figure (shaded bands)
```

## CLI changes

No new flags. `--no-memory` continues to suppress memory columns in both figures. `--fixed-n-traces` and `--fixed-rows` continue to control the separator description text and the filter applied per figure.

## Visual style

- Page background: `#f8f9fa` (off-white)
- Cards: white with `border-radius: 8px` and `box-shadow: 0 1px 3px rgba(0,0,0,0.1)`
- Typography: system font stack, `#1a1a2e` headings, `#4a4a6a` body text
- Plotly template: `plotly_white` (unchanged)
- Band fill opacity: 0.2
- Tool colors and markers: unchanged
