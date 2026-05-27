// flexviz_probe.js — Playwright init-script for FlexViz pages.
// Hooks Plotly.newPlot / Plotly.react; writes window.__benchTimings after
// the first render completes. Uses PerformanceResourceTiming to split
// query_ms (server processing) from transfer_ms (body receive).
(function () {
  var heapBefore = (performance.memory || {}).usedJSHeapSize || 0;
  var benchDone  = false;

  function capture() {
    if (benchDone) return;
    benchDone = true;

    var entries = performance.getEntriesByType('resource');
    var entry = null;
    for (var i = entries.length - 1; i >= 0; i--) {
      var name = entries[i].name;
      if (name.indexOf('/update') !== -1 || name.indexOf('/dashboard/update') !== -1) {
        entry = entries[i];
        break;
      }
    }

    var heapAfter = (performance.memory || {}).usedJSHeapSize || 0;
    window.__benchTimings = {
      query_ms:        entry ? Math.max(0, entry.responseStart - entry.requestStart) : 0,
      transfer_ms:     entry ? Math.max(0, entry.responseEnd   - entry.responseStart) : 0,
      render_ms:       entry ? Math.max(0, performance.now()   - entry.responseEnd)   : performance.now(),
      peak_browser_mb: Math.max(0, (heapAfter - heapBefore) / 1048576),
      payload_bytes:   entry ? (entry.transferSize || 0) : 0,
    };
  }

  function hookPlotly() {
    if (!window.Plotly) { setTimeout(hookPlotly, 50); return; }
    ['newPlot', 'react'].forEach(function (method) {
      var orig = window.Plotly[method].bind(window.Plotly);
      window.Plotly[method] = function () {
        var result = orig.apply(this, arguments);
        if (result && typeof result.then === 'function') {
          result.then(function () { setTimeout(capture, 0); });
        } else {
          setTimeout(capture, 0);
        }
        return result;
      };
    });
  }

  hookPlotly();
})();
