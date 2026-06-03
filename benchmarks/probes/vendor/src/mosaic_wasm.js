import * as vg from '@uwdata/vgplot';
import * as duckdb from '@duckdb/duckdb-wasm';
// Build an AsyncDuckDB from LOCAL vendored bundles (no jsdelivr fetch).
export async function makeDuckDB(wasmUrl, workerUrl) {
  const worker = new Worker(workerUrl);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), worker);
  await db.instantiate(wasmUrl);
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
