// flexviz_probe.js — Playwright init-script for FlexViz pages.
// Hooks Plotly.react and hands the /dashboard/update resource entry to benchDone,
// which reads the clock after the post-render barrier. The entry splits
// server_ms (TTFB) from transfer_ms (body receive) and client_ms (last byte -> barrier).
//
// Uses Object.defineProperty to intercept window.Plotly assignment so the
// hook is installed synchronously the moment Plotly sets itself on window —
// avoiding the 50ms polling race condition when Plotly.js is browser-cached.
(function () {
  var captured = false;
  var requestedAt = null;

  // t0 fallback that cannot go missing: without it capture() falls back to
  // 0 = navigation start when the resource entry is absent, silently folding the
  // whole page load into total_ms. The hook records the fetch-call instant —
  // marginally earlier than requestStart, so the fallback errs against flexviz,
  // the right direction. Same guard plotly_resampler_probe.js already carries.
  var origFetch = window.fetch;
  window.fetch = function (input) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (requestedAt === null && url.indexOf('/update') !== -1) {
      requestedAt = performance.now();
    }
    return origFetch.apply(this, arguments);
  };

  function capture() {
    if (captured) return;
    captured = true;

    var entries = performance.getEntriesByType('resource');
    var entry = null;
    for (var i = entries.length - 1; i >= 0; i--) {
      var name = entries[i].name;
      if (name.indexOf('/update') !== -1 || name.indexOf('/dashboard/update') !== -1) {
        entry = entries[i];
        break;
      }
    }
    if (!window.__benchHelpers) return;

    // t0 = the update request's requestStart (the fetch-hook instant when the resource
    // entry is not yet published); benchDone reads the clock after the barrier, so
    // nothing is precomputed here. server_ms is TTFB — server work until the FIRST
    // byte, not a pure engine query. No request observed at all is an explicit error,
    // never a total_ms that quietly includes the page load.
    var t0 = entry ? entry.requestStart : requestedAt;
    if (t0 === null) {
      window.__benchHelpers.benchError('no /dashboard/update request observed');
      return;
    }
    window.__benchHelpers.benchDone(t0, {
      server_ms:     entry ? Math.max(0, entry.responseStart - entry.requestStart) : null,
      transfer_ms:   entry ? Math.max(0, entry.responseEnd   - entry.responseStart) : null,
      client_from:   entry ? entry.responseEnd : null,
      payload_bytes: entry ? (entry.transferSize || 0) : null,
    });
  }

  function hookPlotly(plotly) {
    // Only hook 'react', not 'newPlot': FlexViz always calls newPlot first with
    // empty stub traces (before any /dashboard/update request), then calls react
    // after the initial data fetch completes.  Capturing on newPlot finds no
    // resource timing entry and records zeros; capturing on react gets the real
    // query/transfer split from the completed /dashboard/update entry.
    ['react'].forEach(function (method) {
      var orig = plotly[method].bind(plotly);
      plotly[method] = function () {
        var result = orig.apply(this, arguments);
        if (result && typeof result.then === 'function') {
          // Arm the barrier from the resolution microtask, BEFORE the next rendering
          // opportunity. A setTimeout hop here is a macrotask: whenever the update
          // pipeline has crossed a frame deadline (a heavy react, or headless
          // Chromium scheduling a frame right after the react task's damage), the
          // frame lands between the microtask and the timer, and the two-frame
          // barrier then starts one frame late — measured +16.5 ms on every trial at
          // n_traces=5 and ~+11 ms median headless at n_traces=1, phase-dependent
          // (0–1 frame). Every other charted probe arms synchronously (mosaic calls
          // benchDone directly, the rasterizers arm from the decode() continuation,
          // plotly-resampler from its plotly_afterplot handler); this was the one
          // clock in the suite that didn't.
          result.then(capture);
        } else {
          capture(); // defensive only: Plotly 3's react always returns a promise
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
