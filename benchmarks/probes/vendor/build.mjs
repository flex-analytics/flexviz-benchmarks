import { build } from 'esbuild';
import {
  cpSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
  existsSync,
  rmSync,
} from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'vendor');            // emit alongside (probes/vendor)
const dist = join(out, 'dist');
const nm = join(here, 'node_modules');
rmSync(dist, { recursive: true, force: true });
mkdirSync(dist, { recursive: true });

// 1. esbuild the Mosaic ESM entries to self-contained bundles. These cannot use a stock
//    prebuilt bundle: the offline DuckDB-WASM path requires our own wiring (makeDuckDB
//    instantiates a LOCAL AsyncDuckDB from the vendored wasm and passes it to
//    wasmConnector — vgplot/mosaic-core otherwise fetch duckdb wasm from jsdelivr).
await build({
  entryPoints: {
    'mosaic_wasm': join(here, 'src', 'mosaic_wasm.js'),
    'mosaic_server_vgplot': join(here, 'src', 'mosaic_server_vgplot.js'),
  },
  bundle: true, format: 'esm', outdir: dist,
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.wasm': 'file' }, assetNames: '[name]', logLevel: 'info',
});

// 2. Copy DuckDB-WASM runtime assets (wasm + worker) for the two bundles selectBundle()
//    chooses between — duckdb resolves these via a runtime config object, so esbuild
//    won't emit them. The COI/pthreads build is deliberately not vendored (experimental,
//    and it would require cross-origin isolation headers that change the page's
//    capability environment).
const dd = join(nm, '@duckdb', 'duckdb-wasm', 'dist');
for (const f of [
  'duckdb-mvp.wasm',
  'duckdb-browser-mvp.worker.js',
  'duckdb-eh.wasm',
  'duckdb-browser-eh.worker.js',
]) cpSync(join(dd, f), join(dist, f));

// 3. Vendor Perspective 5.2 from its PREBUILT cdn bundles (@perspective-dev/*). Each
//    package ships a self-contained dist/cdn/*.js with no bare imports and inlined
//    workers. Unlike the 3.x set these do NOT resolve their wasm as flat siblings: the
//    viewer fetches `../wasm/perspective-viewer.wasm` and the client rewrites its own
//    URL to `<base>/server/dist/wasm/perspective-server.wasm`. So the PACKAGE-RELATIVE
//    layout is preserved verbatim under dist/ — a flat copy 404s the engine.
//    Both engine binaries ship: the loader picks memory64 on hosts that support it
//    (16GB heap vs wasm32's 4GB), which is part of the WASM contender's measured ceiling.
const perspCopies = [
  '@perspective-dev/client/dist/cdn/perspective.js',
  '@perspective-dev/server/dist/wasm/perspective-server.wasm',
  '@perspective-dev/server/dist/wasm/perspective-server.memory64.wasm',
  '@perspective-dev/viewer/dist/cdn/perspective-viewer.js',
  '@perspective-dev/viewer/dist/wasm/perspective-viewer.wasm',
  '@perspective-dev/viewer/dist/css/themes.css',
  '@perspective-dev/viewer-charts/dist/cdn/perspective-viewer-charts.js',
];
for (const rel of perspCopies) {
  const src = join(nm, rel);
  if (!existsSync(src)) throw new Error(`missing Perspective cdn asset: ${rel}`);
  const target = join(dist, rel.replace('@perspective-dev/', ''));
  mkdirSync(dirname(target), { recursive: true });
  cpSync(src, target);
}
// viewer-charts self-registers all 16 chart plugins at import (register() at module scope).
writeFileSync(join(dist, 'perspective.js'),
  "import perspective from './client/dist/cdn/perspective.js';\n" +
  "import './viewer/dist/cdn/perspective-viewer.js';\n" +
  "import './viewer-charts/dist/cdn/perspective-viewer-charts.js';\n" +
  "export { perspective };\n");

// 4. Integrity manifest (sha256 of every emitted asset, keyed by dist-relative path —
//    perspective's tree is nested). Fail if a wasm is suspiciously tiny.
const manifest = {};
const walk = (rel) => {
  for (const e of readdirSync(join(dist, rel), { withFileTypes: true })) {
    const child = rel ? `${rel}/${e.name}` : e.name;
    if (e.isDirectory()) { walk(child); continue; }
    const buf = readFileSync(join(dist, child));
    if (child.endsWith('.wasm') && buf.length < 1024) throw new Error(`vendored wasm too small: ${child}`);
    manifest[child] = createHash('sha256').update(buf).digest('hex');
  }
};
walk('');
writeFileSync(join(out, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log('vendored assets:', Object.keys(manifest).join(', '));
