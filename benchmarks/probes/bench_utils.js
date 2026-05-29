// bench_utils.js — injected into HTML-artifact tool pages.
// Expects window.__benchQueryMs to be pre-set by the Python setup step.
// Sets window.__benchTimings on the 'load' event.
(function () {
  var heapBefore = (performance.memory || {}).usedJSHeapSize || 0;

  window.addEventListener('load', function () {
    var heapAfter = (performance.memory || {}).usedJSHeapSize || 0;
    window.__benchTimings = {
      query_ms:        window.__benchQueryMs       || 0,
      transfer_ms:     null,
      render_ms:       performance.now(),
      peak_browser_mb: Math.max(0, (heapAfter - heapBefore) / 1048576),
      payload_bytes:   window.__benchPayloadBytes  || 0,
    };
  });
})();
