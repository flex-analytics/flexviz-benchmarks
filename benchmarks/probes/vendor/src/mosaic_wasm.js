import * as vg from '@uwdata/vgplot';
import * as duckdb from '@duckdb/duckdb-wasm';
// Build an AsyncDuckDB from LOCAL vendored bundles (no jsdelivr fetch). Resolve the URLs
// to ABSOLUTE against the page first: the wasm URL string is forwarded into the worker,
// which would otherwise re-resolve a relative path against the worker's own location.
export async function makeDuckDB(wasmUrl, workerUrl) {
  const wasmAbs = new URL(wasmUrl, self.location.href).href;
  const workerAbs = new URL(workerUrl, self.location.href).href;
  const worker = new Worker(workerAbs);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(wasmAbs);
  return db;
}
export function connectorFor(db) { return vg.wasmConnector({ duckdb: db }); }
// Insert an Arrow IPC buffer as table `name` (shared across connections in one db).
export async function insertArrow(db, name, uint8) {
  const con = await db.connect();
  await con.insertArrowFromIPCStream(uint8, { name, create: true });
  await con.close();
}
export { vg };
