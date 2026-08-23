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
#   mosaic-wasm    DuckDB-WASM store ceiling (line >20M, hist 50Mx5)  -> <=20M
# The size ceilings below are the 2026-08 observations; the Phase-6 feasibility protocol
# replaces them with per-cell measurements — do not extrapolate new ones by hand here.
# Each phase writes its own JSON; ttfr_bench.py checkpoints after every cell.
set -u

OUT=results/full_$(date +%Y-%m-%d)
mkdir -p "$OUT"

ALL=flexviz,mosaic-server,mosaic-wasm,perspective-server,perspective-wasm,vaex,datashader
NO_PERSP=flexviz,mosaic-server,mosaic-wasm,vaex,datashader
SERVERS=flexviz,mosaic-server,vaex,datashader

# Correctness gates first: a matrix run on engines that render the wrong picture is
# machine-hours spent producing numbers that must be thrown away.
echo "=== [$(date +%H:%M:%S)] GATES: make verify-workloads ==="
if ! make verify-workloads; then
  echo "ABORT: native-workload correctness gates failed — matrix not run" >&2
  exit 2
fi

FAIL=0
SUMMARY=()

run() {  # run <name> <chart> <sizes> <contenders>
  local name=$1 chart=$2 sizes=$3 tools=$4
  echo "=== [$(date +%H:%M:%S)] PHASE $name: chart=$chart sizes=$sizes ==="
  uv run python benchmarks/ttfr_bench.py \
      --chart "$chart" --sizes "$sizes" --n-traces 1,2,5 \
      --data-sources in-memory,disk-parquet --contenders "$tools" \
      --repeats 5 --warmup 1 --seed 42 \
      --flexviz-repo ../flexviz --json-out "$OUT/$name.json"
  # Capture BEFORE any command substitution: $(date) would reset $? to date's status
  # and report a crashed phase as exit=0 (it did, for hist_200m).
  local rc=$?
  SUMMARY+=("$(printf '%-12s %-9s exit=%s' "$name" "$chart" "$rc")")
  if [ "$rc" -ne 0 ]; then FAIL=1; fi
  echo "=== [$(date +%H:%M:%S)] PHASE $name exit=$rc ==="
}

run hist_small  histogram 1000000,2000000,5000000            "$ALL"
run line_small  line      1000000,2000000,5000000            "$ALL"
run hist_mid    histogram 10000000,20000000                  "$NO_PERSP"
run line_mid    line      10000000,20000000                  "$NO_PERSP"
run hist_big    histogram 50000000                           "$SERVERS"
run line_big    line      50000000,100000000                 "$SERVERS"
# 200M last: the histogram phase regenerates a 5-wide parquet (~8GB, disk permitting).
run hist_200m   histogram 200000000                          "$SERVERS"
run line_200m   line      200000000                          "$SERVERS"

echo "=== [$(date +%H:%M:%S)] PHASE SUMMARY ==="
printf '  %s\n' "${SUMMARY[@]}"
# Nonzero if ANY phase failed: a partial matrix must never look like a clean run to CI
# or to the operator scrolling past hours of log.
echo "=== ALL PHASES DONE (aggregate exit=$FAIL) ==="
exit "$FAIL"
