// plotly_resampler_probe.js — Playwright init-script for plotly-resampler's Dash page.
//
// WHY A RELAYOUT AND NOT THE PAGE LOAD: plotly-resampler downsamples inside add_trace(),
// and show_dash serves the already-aggregated figure with the resample callback
// registered prevent_initial_call=True. Clocking the page load would measure a Dash
// bootstrap plus an n_points-point Plotly draw — flat at every row count, and no
// measurement of the engine at all.
//
// So: let the page load, paint and SETTLE, all untimed; then fire the "Reset axes"
// gesture ({xaxis.autorange:true, xaxis.showspikes:false} — the modebar's own relayout
// payload, which construct_update_data routes to the "reset to the global data view"
// branch, i.e. a full-n re-aggregation). t0 is the resulting POST
// /_dash-update-component, and benchDone reads the clock after the post-render barrier.
// Same window shape as flexviz_probe.js: request that triggers the pipeline -> barrier.
//
// THREE THINGS ABOUT THIS PAGE THE HOOK HAS TO SURVIVE:
//   1. Dash fires its OWN update-component POST while booting — Plotly's initial
//      autosize relayout reaches the callback, which answers no_update. Firing into that
//      window is fatal, not just noisy: Plotly treats reset-axes on axes it is still
//      autoranging as a no-op and emits nothing, so the page hangs to the wait cap. Hence
//      the settle gate below; it is entirely outside the measured window.
//   2. dcc.Graph applies the returned Patch through its own bundled Plotly module, NOT
//      window.Plotly — observed doing so on some loads and not others. So the redraw is
//      detected via the graph div's `plotly_afterplot` event, which Plotly emits
//      whichever module drew, rather than by monkey-patching window.Plotly.react.
//   3. dcc.Graph(id=...) is the WRAPPER div; the Plotly graph div is the .js-plotly-plot
//      inside it, and only that one carries _fullLayout and the plotly_* events.
(function () {
  var UPDATE = '_dash-update-component';
  var QUIET_MS = 300;  // no update-component traffic for this long => Dash has settled
  var captured = false, responded = false;
  var triggeredAt = null, requestedAt = null, readyAt = null;
  var lastReq = 0, plots = 0;

  // Fetch hook: exact request timestamps, independent of when the resource timing buffer
  // publishes the entry. window.__pr_t0 is read by the correctness gate.
  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf(UPDATE) === -1) return origFetch.apply(this, arguments);
    lastReq = performance.now();
    var mine = triggeredAt !== null && requestedAt === null;
    if (mine) { requestedAt = lastReq; window.__pr_t0 = requestedAt; }
    var p = origFetch.apply(this, arguments);
    return mine ? p.then(function (r) { responded = true; return r; }) : p;
  };

  function onAfterPlot() {
    plots++;
    // Local redraws from our own Plotly.relayout fire this too — they happen before the
    // server answers, so `responded` is what separates them from the patch redraw.
    if (captured || !responded || !window.__benchHelpers) return;
    captured = true;
    var entries = performance.getEntriesByType('resource'), entry = null;
    for (var i = entries.length - 1; i >= 0; i--) {
      if (entries[i].name.indexOf(UPDATE) !== -1 && entries[i].startTime >= triggeredAt) {
        entry = entries[i];
        break;
      }
    }
    // The entry gives the server/transfer split; without it (buffer not yet published)
    // the components stay null rather than being derived — total_ms is exact either way.
    window.__benchHelpers.benchDone(entry ? entry.requestStart : requestedAt, {
      server_ms:     entry ? Math.max(0, entry.responseStart - entry.requestStart) : null,
      transfer_ms:   entry ? Math.max(0, entry.responseEnd - entry.responseStart) : null,
      client_from:   entry ? entry.responseEnd : null,
      payload_bytes: entry ? (entry.transferSize || 0) : null,
    });
  }

  function ready() {
    var gd = document.querySelector('#resample-figure .js-plotly-plot');
    if (!gd || !gd._fullLayout || !gd.data || !gd.data.length || !gd.on) {
      setTimeout(ready, 10);
      return;
    }
    gd.on('plotly_afterplot', onAfterPlot);
    readyAt = performance.now();
    settle(gd);
  }

  function settle(gd) {
    // Both conditions matter: at least one completed draw, and a quiet window since the
    // last Dash round trip (or since the graph appeared, if Dash made none).
    if (plots === 0 || performance.now() - Math.max(lastReq, readyAt) < QUIET_MS) {
      setTimeout(function () { settle(gd); }, 25);
      return;
    }
    triggeredAt = performance.now();
    // The modebar "Reset axes" payload. BOTH keys are required: autorange without
    // showspikes hits construct_update_data's dash.no_update branch, and the page would
    // then hang until the harness timed it out.
    try {
      window.Plotly.relayout(gd, { 'xaxis.autorange': true, 'xaxis.showspikes': false });
    } catch (e) {
      window.__benchHelpers.benchError(e);
    }
  }

  ready();
})();
