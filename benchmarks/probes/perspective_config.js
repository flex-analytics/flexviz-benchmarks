// perspective_config.js — builds the <perspective-viewer> restore config INSIDE the
// timed window. Extent discovery (min/max) runs on the perspective engine itself, so
// axis-range work is charged to the clock like every other tool's (extent policy:
// in-window for everyone). Mirrors restore_config() in perspective_wasm.py, which the
// same-picture tests lock against the numpy oracle.
window.__perspectiveConfig = async function (table, chart, nTraces, binsOrPts) {
  async function extent(col) {
    const view = await table.view({
      group_by: ['__one'],
      expressions: { '__one': '1', '__lo': `"${col}"`, '__hi': `"${col}"` },
      columns: ['__lo', '__hi'],
      aggregates: { '__lo': 'min', '__hi': 'max' },
    });
    const cols = await view.to_columns();
    await view.delete();
    return [cols['__lo'][0], cols['__hi'][0]]; // row 0 = grand-total row
  }
  if (chart === 'histogram') {
    // Long-form table (value + trace). min(bins-1, ...) keeps value == hi in the last
    // bin (closed on the right, matches np.histogram).
    const [lo, hi] = await extent('value');
    const width = (hi - lo) / binsOrPts || 1;
    const bin = `min(${binsOrPts - 1}, floor(("value" - ${lo}) / ${width}))`;
    return {
      plugin: 'Y Bar',
      expressions: { bin },
      group_by: ['bin'],
      split_by: ['trace'],
      columns: ['value'],
      aggregates: { value: 'count' },
    };
  }
  // Line: mean-per-bin over ~n_points x-bins — perspective-native aggregated line.
  // (group_by on raw continuous x makes one group per distinct float: 6.7s at 1M rows
  // and bad_alloc beyond — a misuse, not a ceiling.)
  const [lo, hi] = await extent('x');
  const width = (hi - lo) / binsOrPts || 1;
  const idx = `min(${binsOrPts - 1}, floor(("x" - ${lo}) / ${width}))`;
  const xbin = `${lo} + ${width} * (${idx} + 0.5)`;
  const cols = Array.from({ length: nTraces }, (_, t) => `y${t + 1}`);
  return {
    plugin: 'Y Line',
    expressions: { xbin },
    group_by: ['xbin'],
    columns: cols,
    aggregates: Object.fromEntries(cols.map((c) => [c, 'avg'])),
  };
};
