// contract.js — shared page contract. Each probe imports these helpers and finishes
// a trial by calling benchDone(t0, fields). The clock ends AFTER a double-rAF
// post-render barrier — a frame barrier after the render commit, not a claim that
// compositor presentation is proven.
// Two render proofs are supported and BOTH count toward `marks`:
//   1. vector marks (canvas/svg/path/rect/polyline), recursing through shadow DOM;
//   2. <img> elements that decoded to non-zero dimensions AND are non-blank (the
//      rasterizers — Vaex/Datashader — render a single <img>, so without this they
//      would always report `no_marks`). The non-blank-pixel check is always-on, per
//      the spec's "always-on non-blank-pixel assertion".
window.__benchHelpers = {
  // Resolve on the second animation frame after the render commit (frame barrier).
  afterPaint() {
    return new Promise((res) => requestAnimationFrame(() => requestAnimationFrame(res)));
  },
  // Recursively count vector chart marks across nested shadow DOM (Perspective needs this).
  countVectorMarks(root = document) {
    let n = 0;
    root.querySelectorAll("canvas,svg,path,rect,polyline").forEach(() => n++);
    root.querySelectorAll("*").forEach((el) => {
      if (el.shadowRoot) n += window.__benchHelpers.countVectorMarks(el.shadowRoot);
    });
    return n;
  },
  // True iff the decoded image is not a single flat colour (i.e. it actually drew data).
  // Downsamples onto a 32x32 canvas and checks that >1 distinct pixel value appears.
  imageIsNonBlank(img) {
    if (!img.complete || img.naturalWidth === 0 || img.naturalHeight === 0) return false;
    const w = 32, h = 32;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, w, h);
    const px = ctx.getImageData(0, 0, w, h).data;
    const first = (px[0] << 16) | (px[1] << 8) | px[2];
    for (let i = 4; i < px.length; i += 4) {
      if (((px[i] << 16) | (px[i + 1] << 8) | px[i + 2]) !== first) return true;
    }
    return false;
  },
  // Count non-blank decoded <img> render marks (rasterizers).
  countImageMarks(root = document) {
    let n = 0;
    root.querySelectorAll("img").forEach((img) => {
      if (window.__benchHelpers.imageIsNonBlank(img)) n++;
    });
    return n;
  },
  // Signal that the engine's in-browser native store is built (client/WASM tools),
  // then block until the harness has sampled resident memory and releases us.
  // Server/raster pages never call this; they render directly on load.
  async benchStored() {
    window.__bench_stored = true;
    await new Promise((res) => {
      if (window.__bench_go) return res();
      const id = setInterval(() => {
        if (window.__bench_go) { clearInterval(id); res(); }
      }, 2);
    });
  },
  // Normalise a fetch() input (string | Request | URL) to a URL string.
  fetchUrl(input) {
    return typeof input === "string" ? input : (input && input.url) || "";
  },
  // The most recent resource-timing entry whose name contains `needle`, optionally
  // starting at or after `since` (performance.now() timeline). Null when the resource
  // timing buffer has not published it yet — callers keep their own fetch-hook fallback.
  lastResourceEntry(needle, since = null) {
    const entries = performance.getEntriesByType("resource");
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i];
      if (e.name.indexOf(needle) !== -1 && (since === null || e.startTime >= since)) return e;
    }
    return null;
  },
  // The benchDone timing fields a resource entry yields: server_ms = TTFB (request ->
  // first byte), transfer_ms = body receive, client_from = last byte. Without an entry
  // every component stays null — never derived, never zero.
  resourceFields(entry) {
    if (!entry) {
      return { server_ms: null, transfer_ms: null, client_from: null, payload_bytes: null };
    }
    return {
      server_ms: Math.max(0, entry.responseStart - entry.requestStart),
      transfer_ms: Math.max(0, entry.responseEnd - entry.responseStart),
      client_from: entry.responseEnd,
      payload_bytes: entry.transferSize || 0,
    };
  },
  // Finish a trial. `t0` is the performance.now()-timeline origin of the request that
  // triggered the pipeline; the total is read AFTER the barrier, so the barrier is
  // inside the measured window. Components a pipeline cannot separate stay null —
  // never derived by subtracting a null. `fields.client_from` (the resource entry's
  // responseEnd, same timeline) becomes client_ms = last byte -> barrier.
  async benchDone(t0, fields = {}) {
    await window.__benchHelpers.afterPaint();
    const now = performance.now();
    const marks = window.__benchHelpers.countVectorMarks() + window.__benchHelpers.countImageMarks();
    const { client_from, ...rest } = fields;
    window.__bench = {
      status: marks > 0 ? "ok" : "no_marks",
      marks,
      server_ms: null, transfer_ms: null, client_ms: null, payload_bytes: null,
      ...rest,
      total_ms: Math.max(0, now - t0),
    };
    if (client_from != null) window.__bench.client_ms = Math.max(0, now - client_from);
  },
  benchError(err) { window.__bench = { status: "error", err: String(err) }; },
};
