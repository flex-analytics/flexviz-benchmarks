// plotly_resampler_probe.js — Playwright init-script for plotly-resampler's Dash page.
//
// WINDOW: the GET /_dash-layout request -> the post-render barrier. The contender gives
// Dash a CALLABLE app.layout, so that request is where the FigureResampler is built and
// MinMaxLTTB runs over the full hf arrays (see core/contenders/plotly_resampler.py).
// server_ms is its TTFB (construction + aggregation + figure JSON), transfer_ms its
// body, client_ms last byte -> barrier (React render + the dcc.Graph Plotly draw). Same
// window shape as flexviz's: the request that triggers the pipeline -> barrier.
//
// THREE THINGS ABOUT THIS PAGE THE HOOK HAS TO SURVIVE:
//   1. dash-renderer issues the layout request with window.fetch (its GET helper in
//      dash-renderer's bundle), so the fetch hook is enough — no XHR path to cover. It
//      only supplies the t0 FALLBACK: the resource entry's requestStart is used
//      whenever the resource-timing buffer has published it.
//   2. dcc.Graph(id=...) is the WRAPPER div; the Plotly graph div is the .js-plotly-plot
//      inside it, and only that one carries the plotly_* events.
//   3. That div fires exactly ONE plotly_afterplot for the page's first draw, so the
//      listener has to be attached before the draw ends. Polling for the div is too
//      late — a 5 ms poll was measured attaching after the only afterplot had already
//      fired. dcc.Graph draws with the global Plotly (its bundle resolves `Plotly` as a
//      global at call time), so the listener is attached from inside that react call,
//      where the div's event emitter is already initialised. The poll below is only a
//      fallback for a Plotly that never passes through window.
(function () {
  var LAYOUT = '_dash-layout';
  var GRAPH = '#resample-figure .js-plotly-plot';
  var captured = false;
  var requestedAt = null;

  // Fetch hook: the request instant, independent of when the resource timing buffer
  // publishes the entry. Marginally earlier than requestStart, so the fallback errs
  // against plotly-resampler — the right direction.
  var origFetch = window.fetch;
  window.fetch = function (input) {
    var url = window.__benchHelpers.fetchUrl(input);
    if (requestedAt === null && url.indexOf(LAYOUT) !== -1) {
      requestedAt = performance.now();
    }
    return origFetch.apply(this, arguments);
  };

  function capture() {
    if (captured || !window.__benchHelpers) return;
    captured = true;
    var entry = window.__benchHelpers.lastResourceEntry(LAYOUT);
    // No request and no entry at all is an explicit error, never a total_ms that
    // quietly includes the page load.
    var t0 = entry ? entry.requestStart : requestedAt;
    if (t0 === null) {
      window.__benchHelpers.benchError('no GET /_dash-layout request observed');
      return;
    }
    window.__benchHelpers.benchDone(t0, window.__benchHelpers.resourceFields(entry));
  }

  // The graph div only exists once the layout response has been applied, so any
  // plotly_afterplot on it is by construction after the response.
  function attach(gd) {
    if (!gd || gd.__benchBound || typeof gd.on !== 'function') return false;
    gd.__benchBound = true;
    gd.on('plotly_afterplot', capture);
    return true;
  }

  function hookPlotly(plotly) {
    var origReact = plotly.react.bind(plotly);
    plotly.react = function (gd) {
      var result = origReact.apply(this, arguments);
      attach(typeof gd === 'string' ? document.getElementById(gd) : gd);
      return result;
    };
  }

  if (window.Plotly) {
    hookPlotly(window.Plotly);
  } else {
    try {
      // Intercept the moment Plotly assigns itself to window.Plotly, so the hook is in
      // place before dcc.Graph's first react. Same trap flexviz_probe.js uses.
      Object.defineProperty(window, 'Plotly', {
        configurable: true,
        enumerable: true,
        get: function () { return undefined; },
        set: function (plotly) {
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
      // defineProperty failed — the poll below is the only path left.
    }
  }

  (function poll() {
    var gd = document.querySelector(GRAPH);
    if (captured || (gd && gd.__benchBound) || attach(gd)) return;
    setTimeout(poll, 5);
  })();
})();
