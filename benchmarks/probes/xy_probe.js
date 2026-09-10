// xy_probe.js — completion probe for the xy WebGL2 browser path (Class A-ish).
//
// The page IS xy's own to_html() document, computed inside the GET, so t0 is the
// top-level navigation's requestStart and the server/transfer/client split comes from the
// PerformanceNavigationTiming entry (same fields resourceFields reads off a resource
// entry). The render is a synchronous xy.renderStandalone(el, spec, buf) at end of <body>;
// we wrap it (init scripts run before page scripts, like flexviz hooks window.Plotly) so we
// stop on the real draw call AND capture xy's decimation disclosure (spec.traces[].tier /
// n_marks / n_points) — the density-grid-vs-exact fact the comparison must not hide.
(() => {
  const H = window.__benchHelpers;
  let done = false;

  function finish(spec) {
    if (done) return;
    done = true;
    const nav = performance.getEntriesByType("navigation")[0];
    const fields = nav ? H.resourceFields(nav) : {};
    if (spec && Array.isArray(spec.traces) && spec.traces.length) {
      const tiers = [...new Set(spec.traces.map((t) => t.tier))];
      fields.render_tier = tiers.length === 1 ? tiers[0] : tiers.join("+");
      fields.render_marks = spec.traces.reduce((a, t) => a + (t.n_marks || 0), 0);
    }
    H.benchDone(nav ? nav.requestStart : 0, fields).catch((e) => H.benchError(e));
  }

  // Wrap renderStandalone the instant the bundle assigns window.xy.
  let _xy;
  try {
    Object.defineProperty(window, "xy", {
      configurable: true,
      get() {
        return _xy;
      },
      set(v) {
        _xy = v;
        if (v && typeof v.renderStandalone === "function") {
          const orig = v.renderStandalone;
          v.renderStandalone = function (el, spec, buf) {
            const r = orig.call(this, el, spec, buf);
            Promise.resolve(r)
              .then(() => finish(spec))
              .catch((e) => H.benchError(e));
            return r;
          };
        }
      },
    });
  } catch (e) {
    /* window.xy assigned in a way we can't intercept — the load fallback still fires. */
  }

  // Fallback if the wrap was bypassed: the sync render has happened by load; time it
  // anyway (without the tier disclosure). The `done` guard keeps the wrapped path winning.
  addEventListener("load", () => setTimeout(() => finish(undefined), 0));
})();
