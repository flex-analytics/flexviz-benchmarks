// flexviz_probe.js — Playwright init-script for FlexViz pages.
// Hooks Plotly.newPlot / Plotly.react; writes window.__benchTimings after
// the first render completes. Uses PerformanceResourceTiming to split
// query_ms (server processing) from transfer_ms (body receive).
//
// Uses Object.defineProperty to intercept window.Plotly assignment so the
// hook is installed synchronously the moment Plotly sets itself on window —
// avoiding the 50ms polling race condition when Plotly.js is browser-cached.
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

  function hookPlotly(plotly) {
    ['newPlot', 'react'].forEach(function (method) {
      var orig = plotly[method].bind(plotly);
      plotly[method] = function () {
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

  // If Plotly is already present (shouldn't happen in an init-script, but be safe).
  if (window.Plotly) {
    hookPlotly(window.Plotly);
    return;
  }

  // Intercept the moment Plotly assigns itself to window.Plotly.
  // This fires synchronously inside the Plotly bundle's UMD assignment,
  // guaranteeing the hook is in place before any newPlot/react call.
  try {
    Object.defineProperty(window, 'Plotly', {
      configurable: true,
      enumerable: true,
      get: function () { return undefined; },
      set: function (plotly) {
        // Restore to a plain writable property so Plotly's own code can reassign.
        Object.defineProperty(window, 'Plotly', {
          value: plotly,
          writable: true,
          configurable: true,
          enumerable: true,
        });
        hookPlotly(plotly);
      },
    });
  } catch (e) {
    // defineProperty failed (e.g. Plotly already defined non-configurable) — fall back to polling.
    (function poll() {
      if (!window.Plotly) { setTimeout(poll, 50); return; }
      hookPlotly(window.Plotly);
    })();
  }
})();
