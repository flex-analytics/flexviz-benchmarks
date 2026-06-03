import { build } from 'esbuild';
import { cpSync, mkdirSync, writeFileSync, readFileSync, readdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'vendor');            // emit alongside (probes/vendor)
const dist = join(out, 'dist');
const nm = join(here, 'node_modules');
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

// 2. Copy DuckDB-WASM runtime assets (wasm + worker) — duckdb resolves these via a
//    runtime config object, not import.meta.url, so esbuild won't emit them.
const dd = join(nm, '@duckdb', 'duckdb-wasm', 'dist');
for (const f of ['duckdb-eh.wasm', 'duckdb-browser-eh.worker.js']) cpSync(join(dd, f), join(dist, f));

// 3. Vendor Perspective from its PREBUILT cdn bundles. Each @finos/perspective* package
//    ships a self-contained dist/cdn/*.js (the same artifact jsdelivr serves) that has no
//    cross-package or bare imports and references its own .wasm via local import.meta.url.
//    Re-bundling the ESM-from-source entry with esbuild fails (it imports a worker from
//    .ts); the cdn bundles are the supported, robust offline path. We copy them flat into
//    dist/ (so the import.meta.url wasm refs resolve) and emit a shim `perspective.js`
//    that re-exports the engine + side-imports the viewer + d3fc plugin.
const perspCopies = [
  ['@finos/perspective/dist/cdn/perspective.js', 'perspective-core.js'],
  ['@finos/perspective/dist/cdn/perspective-server.wasm', 'perspective-server.wasm'],
  ['@finos/perspective-viewer/dist/cdn/perspective-viewer.js', 'perspective-viewer.js'],
  ['@finos/perspective-viewer/dist/cdn/perspective-viewer.wasm', 'perspective-viewer.wasm'],
  ['@finos/perspective-viewer-d3fc/dist/cdn/perspective-viewer-d3fc.js', 'perspective-viewer-d3fc.js'],
];
for (const [rel, name] of perspCopies) {
  const src = join(nm, rel);
  if (!existsSync(src)) throw new Error(`missing Perspective cdn asset: ${rel}`);
  cpSync(src, join(dist, name));
}
writeFileSync(join(dist, 'perspective.js'),
  "import perspective from './perspective-core.js';\n" +
  "import './perspective-viewer.js';\n" +
  "import './perspective-viewer-d3fc.js';\n" +
  "export { perspective };\n");

// 4. Integrity manifest (sha256 of every emitted asset). Fail if a wasm is suspiciously tiny.
const manifest = {};
for (const f of readdirSync(dist)) {
  const buf = readFileSync(join(dist, f));
  if (f.endsWith('.wasm') && buf.length < 1024) throw new Error(`vendored wasm too small: ${f}`);
  manifest[f] = createHash('sha256').update(buf).digest('hex');
}
writeFileSync(join(out, 'manifest.json'), JSON.stringify(manifest, null, 2));
console.log('vendored assets:', Object.keys(manifest).join(', '));
