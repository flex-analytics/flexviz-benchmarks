#!/usr/bin/env bash
# TTFR matrix PHASE TEMPLATE, one environment, one pass.
# NOT yet "the full publishable matrix": the size ceilings below are 2026-08 hand-entered
# observations. Phase 6's feasibility protocol replaces them with per-cell measurements;
# until it has run, a matrix produced here is diagnostic.
# Phases are ordered cheapest-first so a complete mid-scale story exists early.
# Roster shrinks with size by VALIDATED ceilings, not convenience:
#   perspective-*  histogram: unsupported (config.EXCLUSIONS — no histogram chart type).
#                  line: ADMITTED at n_traces=1 only (config.MAX_TRACES — native X/Y Line
#                  carries one y series); above 1M rows viewer-charts truncates to its
#                  2M-cell cap, so those cells record rendered_fraction and are censored.
#                  hist2d: unsupported (config.EXCLUSIONS — no continuous 2-D binning).
#   plotly-resampler-*  line only (config.EXCLUSIONS), in-memory only — so they join
#                  the line rosters and neither histogram nor hist2d.
#   altair-vegafusion  histogram + hist2d only (config.EXCLUSIONS — Vega-Lite has no
#                  downsampling transform), so it joins every roster except the line ones.
#   mosaic-wasm    DuckDB-WASM store ceiling (line >20M, hist 50Mx5)  -> <=20M
# hist2d runs at n_traces=1 only (config.CHART_N_TRACES) and its dataset is two
# columns wide at every trace count, so it has none of the histogram phases' width
# trap — no need to split its 200M phase by n_traces.
# The size ceilings below are the 2026-08 observations; the Phase-6 feasibility protocol
# replaces them with per-cell measurements — do not extrapolate new ones by hand here.
# Each phase writes its own JSON; ttfr_bench.py checkpoints after every cell.
# --reuse-datasets: an existing dataset whose identity drifted (a polars bump, an
# edited datagen.py) is an ERROR here, not a silent 57GB rebuild discovered as
# ENOSPC three phases in. Missing datasets still generate, so a first run works.
set -u

# OUT is overridable so a killed run resumes into the same directory:
#   OUT=results/full_2026-08-30 ./run_matrix.sh
OUT=${OUT:-results/full_$(date +%Y-%m-%d)}
mkdir -p "$OUT"

ALL=flexviz,altair-vegafusion,mosaic-server,mosaic-wasm,perspective-server,perspective-wasm,vaex,datashader
NO_PERSP=flexviz,altair-vegafusion,mosaic-server,mosaic-wasm,vaex,datashader
SERVERS=flexviz,altair-vegafusion,mosaic-server,vaex,datashader
# plotly-resampler is LINE-ONLY (config.EXCLUSIONS — no binning API) and in-memory ONLY
# (config.MEMORY_ONLY — no out-of-core path), so it joins the line rosters and nothing
# else. Two entries: the library default (single-threaded MinMaxLTTB) and parallel=True.
# No hand-entered size ceiling — same rule as the rest of this file: if a cell will not
# fit, the harness records the failure rather than a guess made here deciding for it.
PR=plotly-resampler,plotly-resampler-par
ALL_LINE=$ALL,$PR
NO_PERSP_LINE=$NO_PERSP,$PR
SERVERS_LINE=$SERVERS,$PR

# Correctness gates first: a matrix run on engines that render the wrong picture is
# machine-hours spent producing numbers that must be thrown away.
echo "=== [$(date +%H:%M:%S)] GATES: make verify-workloads ==="
if ! make verify-workloads; then
  echo "ABORT: native-workload correctness gates failed — matrix not run" >&2
  exit 2
fi

FAIL=0
SUMMARY=()

run() {  # run <name> <chart> <sizes> <contenders> [data-sources]
  local name=$1 chart=$2 sizes=$3 tools=$4 sources=${5:-in-memory,disk-parquet}
  # Resume: a finished phase is never re-run, because ttfr_bench.py truncates its
  # --json-out at the first checkpoint — a re-run that dies early DESTROYS the cells the
  # previous run finished (it did, for line_big on 2026-08-30). "Finished" is
  # zero `not_requested` statuses, not "has summary rows": a phase killed mid-matrix
  # checkpoints real cells too, and would otherwise be skipped while still half-empty.
  if [ -f "$OUT/$name.json" ] && python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
sys.exit(any(s['status'] == 'not_requested' for s in d['statuses']))
" "$OUT/$name.json" 2>/dev/null; then
    echo "=== SKIP $name: $OUT/$name.json is already complete ==="
    SUMMARY+=("$(printf '%-12s %-9s SKIPPED (complete)' "$name" "$chart")")
    return 0
  fi
  echo "=== [$(date +%H:%M:%S)] PHASE $name: chart=$chart sizes=$sizes src=$sources ==="
  uv run python benchmarks/ttfr_bench.py \
      --chart "$chart" --sizes "$sizes" \
      --data-sources "$sources" --contenders "$tools" \
      --repeats 5 --warmup 1 --seed 42 --reuse-datasets \
      --flexviz-repo ../flexviz --json-out "$OUT/$name.json"
  # Capture BEFORE any command substitution: $(date) would reset $? to date's status
  # and report a crashed phase as exit=0 (it did, for hist_200m).
  local rc=$?
  SUMMARY+=("$(printf '%-12s %-9s exit=%s' "$name" "$chart" "$rc")")
  if [ "$rc" -ne 0 ]; then FAIL=1; fi
  echo "=== [$(date +%H:%M:%S)] PHASE $name exit=$rc ==="
}

run hist_small  histogram 1000000,2000000,5000000            "$ALL"
run hist2d_small  hist2d  1000000,2000000,5000000            "$ALL"
run line_small  line      1000000,2000000,5000000            "$ALL_LINE"
run hist_mid    histogram 10000000,20000000                  "$NO_PERSP"
run hist2d_mid    hist2d  10000000,20000000                  "$NO_PERSP"
run line_mid    line      10000000,20000000                  "$NO_PERSP_LINE"
run hist_big    histogram 50000000                           "$SERVERS"
run hist2d_big    hist2d  50000000                           "$SERVERS"
run line_big    line      50000000,100000000                 "$SERVERS_LINE"
# 200M last: the histogram phase regenerates a 5-wide parquet (~8GB, disk permitting).
# Split by data source, NOT by trace count: a single hist_200m phase was SIGKILLed at the
# `200M nt5 disk-parquet` cell twice. Dataset width is max(--n-traces), so splitting on
# traces would rebuild the Parquet two columns wide and make those cells cheaper than
# every other disk cell; `data_sources` is a CONFIG_SPLIT key and leaves the file
# byte-identical.
run hist_200m_in   histogram 200000000                       "$SERVERS" in-memory
run hist_200m_disk histogram 200000000                       "$SERVERS" disk-parquet
run hist2d_200m_in   hist2d  200000000                       "$SERVERS" in-memory
run hist2d_200m_disk hist2d  200000000                       "$SERVERS" disk-parquet
run line_200m   line      200000000                          "$SERVERS_LINE"

echo "=== [$(date +%H:%M:%S)] PHASE SUMMARY ==="
printf '  %s\n' "${SUMMARY[@]}"
# Nonzero if ANY phase failed: a partial matrix must never look like a clean run to CI
# or to the operator scrolling past hours of log.
echo "=== ALL PHASES DONE (aggregate exit=$FAIL) ==="
exit "$FAIL"
