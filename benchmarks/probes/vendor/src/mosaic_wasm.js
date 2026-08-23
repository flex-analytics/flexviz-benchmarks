import * as vg from '@uwdata/vgplot';
import * as duckdb from '@duckdb/duckdb-wasm';
// Build an AsyncDuckDB from LOCAL vendored bundles (no jsdelivr fetch) over DuckDB-WASM's
// documented default path: hand selectBundle() the vendored candidates and let its feature
// detection choose. URLs resolve ABSOLUTE (against this module, which sits next to the
// assets): the wasm URL string is forwarded into the worker, which would otherwise
// re-resolve a relative path against the worker's own location.
const asset = (f) => new URL(f, import.meta.url).href;
const BUNDLES = {
  mvp: { mainModule: asset('duckdb-mvp.wasm'), mainWorker: asset('duckdb-browser-mvp.worker.js') },
  eh: { mainModule: asset('duckdb-eh.wasm'), mainWorker: asset('duckdb-browser-eh.worker.js') },
};
// -> { db, bundle } where `bundle` is the selected candidate's name (provenance).
export async function makeDuckDB() {
  const picked = await duckdb.selectBundle(BUNDLES);
  const bundle = Object.keys(BUNDLES).find((n) => BUNDLES[n].mainModule === picked.mainModule);
  const worker = new Worker(picked.mainWorker);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(picked.mainModule, picked.pthreadWorker);
  return { db, bundle };
}
export function connectorFor(db) { return vg.wasmConnector({ duckdb: db }); }
// Insert an Arrow IPC buffer as table `name` (shared across connections in one db).
export async function insertArrow(db, name, uint8) {
  const con = await db.connect();
  await con.insertArrowFromIPCStream(uint8, { name, create: true });
  await con.close();
}
export { vg };
