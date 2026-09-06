// flexviz_probe.js — Playwright init-script for FlexViz pages.
//
// WINDOW: the POST /dashboard/update request -> the post-render barrier. FlexViz's page
// requests its data first and draws each figure once with Plotly.react when the response
// lands, so there is no startup newPlot to clock from: t0 is the request (the resource
// entry's requestStart, or the fetch-hook instant when the buffer has not published the
// entry yet) and capture triggers on the `react` that draws the answer. server_ms (TTFB)
// / transfer_ms / client_ms (last byte -> barrier) come from that same entry, so the
// three components sum to total_ms up to the fetch-call-to-requestStart slack.
//
// The newPlot hook stays: FlexViz still calls newPlot as a fallback for a figure the
// init response did not draw, and a page that ever draws BEFORE requesting must keep
// that draw inside the window rather than starting the clock at the request.
//
// Uses Object.defineProperty to intercept window.Plotly assignment so the
// hook is installed synchronously the moment Plotly sets itself on window —
// avoiding the 50ms polling race condition when Plotly.js is browser-cached.
(function () {
  var captured = false;
  var requestedAt = null;
  var plotStart = null;

  // t0 fallbacks that cannot go missing: without them capture() falls back to
  // 0 = navigation start when neither newPlot nor the resource entry is seen, silently
  // folding the whole page load into total_ms. The fetch hook records the fetch-call
  // instant — marginally earlier than requestStart, so the fallback errs against flexviz,
  // the right direction. Same guard plotly_resampler_probe.js already carries.
  var origFetch = window.fetch;
  window.fetch = function (input) {
    var url = window.__benchHelpers.fetchUrl(input);
    if (requestedAt === null && url.indexOf('/update') !== -1) {
      requestedAt = performance.now();
    }
    return origFetch.apply(this, arguments);
  };

  function capture() {
    if (captured || !window.__benchHelpers) return;
    captured = true;

    var entry = window.__benchHelpers.lastResourceEntry('/update');

    // t0 = the update request (its resource entry's requestStart, or the fetch-hook
    // instant when the entry is not yet published) — unless a newPlot ran first, which
    // is then the earlier and therefore correct origin. No request and no newPlot at
    // all is an explicit error, never a total_ms that quietly includes the page load.
    var t0 = plotStart !== null ? plotStart : entry ? entry.requestStart : requestedAt;
    if (t0 === null) {
      window.__benchHelpers.benchError('no /dashboard/update request observed');
      return;
    }
    window.__benchHelpers.benchDone(t0, window.__benchHelpers.resourceFields(entry));
  }

  function hookPlotly(plotly) {
    // newPlot starts the clock if it ever runs before the request (any draw that
    // precedes the request belongs inside the window); react is the capture trigger —
    // it is the call that draws the /dashboard/update answer, and only then is the
    // resource entry there to split server/transfer.
    var origNewPlot = plotly.newPlot.bind(plotly);
    plotly.newPlot = function () {
      if (plotStart === null) plotStart = performance.now();
      return origNewPlot.apply(this, arguments);
    };

    var origReact = plotly.react.bind(plotly);
    plotly.react = function () {
      var result = origReact.apply(this, arguments);
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
