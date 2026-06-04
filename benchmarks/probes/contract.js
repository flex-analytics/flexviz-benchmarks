// contract.js — shared page contract. Each probe imports these helpers and finishes
// a trial by calling benchDone(...). Render-complete requires a double-rAF (paint).
// Two render proofs are supported and BOTH count toward `marks`:
//   1. vector marks (canvas/svg/path/rect/polyline), recursing through shadow DOM;
//   2. <img> elements that decoded to non-zero dimensions AND are non-blank (the
//      rasterizers — Vaex/Datashader — render a single <img>, so without this they
//      would always report `no_marks`). The non-blank-pixel check is always-on, per
//      the spec's "always-on non-blank-pixel assertion".
window.__benchHelpers = {
  // Resolve after the next compositor paint (two nested rAFs).
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
  async benchDone(fields) {
    await window.__benchHelpers.afterPaint();
    const marks = window.__benchHelpers.countVectorMarks() + window.__benchHelpers.countImageMarks();
    window.__bench = {
      status: marks > 0 ? "ok" : "no_marks",
      marks,
      query_ms: null, transfer_ms: null, render_ms: null, payload_bytes: null,
      ...fields,
    };
  },
  benchError(err) { window.__bench = { status: "error", err: String(err) }; },
};
