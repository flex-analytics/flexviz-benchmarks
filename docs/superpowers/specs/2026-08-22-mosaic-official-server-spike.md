# Mosaic official `duckdb-server` — spike & adoption (plan 2.2)

**Date:** 2026-08-22 · **Outcome: ADOPTED, CURRENTLY RE-VERIFIED ON 0.30.0.**
`benchmarks/mosaic_duckdb_server.py` (the hand-rolled fork) is deleted; `mosaic-server`
now runs the official PyPI `duckdb-server` as its backend child. The original adoption
spike used 0.26.0; the current 0.30.0 re-spike is recorded below.

Current package under test: `duckdb-server==0.30.0` (declared in `pyproject.toml`,
installed in `.venv`). **Import name is `pkg`**, not `duckdb_server` —
`.venv/lib/python3.12/site-packages/pkg/{__main__,server,query}.py`, console script
`duckdb-server = pkg.__main__:serve`. Upstream: `uwdata/mosaic`.

## Spike answers

### 1. Programmatic start / chosen port — YES, with one injected line

Entrypoint is `pkg.server.server(con, cache)` (`server.py:114`) — it takes the DuckDB
connection and the diskcache instance as arguments, so the harness owns both.
`pkg.__main__.serve()` (`__main__.py:13-23`) is only a thin wrapper: `duckdb.connect(argv[1]
or ":memory:")` + `Cache()` + `server(con, cache)`.

**The port is hardcoded:** `app.listen(3000, ...)` (`server.py:169`). There is no port
argument, no env var, no CLI flag. Since the harness spawns a fresh server per trial and
needs a free port (a stale/foreign :3000 would otherwise wedge the whole matrix), the
child overrides `socketify.App.listen` before calling `server()`:

```python
_listen = socketify.App.listen
socketify.App.listen = lambda self, _p=None, h=None: _listen(self, {"port": port, "host": "127.0.0.1"}, h)
```

`socketify.App.listen` already accepts a `{"port", "host"}` dict (verified in its source),
so this is the library's own supported call path — the query/protocol/cache code of the
official server is untouched. Bonus: it binds `127.0.0.1` where the official banner binds
`0.0.0.0` (verified via `psutil.net_connections()` → `addr(ip='127.0.0.1', port=44659)`).

Failure mode if the port were left at 3000 and already taken: uWS invokes the listen
handler with `port == 0` and `app.run()` loops forever — a silent hang, not an error. The
override plus the existing readiness-wait avoids it.

### 2. Starts EMPTY — YES

`server(con, cache)` registers routes only; nothing creates a table. The contender hands it
`duckdb.connect(":memory:")`. Verified live: immediately after readiness,
`select count(*) from duckdb_tables()` → `[{"c":0}]`. The harness contract holds — the
child is spawned empty, `backend_root = psutil.Process(pid)` is set before `preload`, and
the store build lands entirely inside the `PeakWindow` delta.

### 3. Data loading — the server's own `exec` command over HTTP POST `/`

`app.any("/", http_handler)` (`server.py:165`); POST parses the JSON body and dispatches
`{"sql": ..., "type": "exec"}` to `con.execute(sql)` (`server.py:85-87`). No control route
of our own is needed — this is the documented API (README: `exec` / `arrow` / `json`).

* **in-memory** → `CREATE OR REPLACE TABLE bench AS SELECT * FROM read_parquet('<tmp>')`
  (materialized native table, matching Mosaic's `loadParquet` default).
* **disk** → `CREATE OR REPLACE VIEW bench AS SELECT * FROM '<path>'`.
  **Disclosed deviation:** Mosaic's default disk load materializes; a view is chosen so the
  file scan stays inside the timed window (`view: true` is a documented Mosaic option).

**Arrow IPC is NOT loadable by the installed engine.** duckdb 1.5.3 has no IPC reader:

```
FROM '/tmp/x.arrow'              → Binder Error: No extension found that is capable of reading the file
FROM read_arrow('/tmp/x.arrow')  → Catalog Error: Table Function with name read_arrow does not exist!
```

So the in-memory handoff file is **Parquet** (`compression="uncompressed"`, keeping the
write and the read as cheap as the old IPC handoff). `base.spill_arrow_path()` is still the
path source (it keeps the multi-GB spill off tmpfs), and `read_parquet()` is called
explicitly because that helper's `.arrow` suffix defeats duckdb's extension sniffing. The
handoff stays a **file path in the SQL**, never an HTTP body — uWS 400s bodies over ~1GB.

### 4. Cache — per-trial temp dir; `persist: true` confirmed store-only

`pkg.__main__` uses `Cache()` with diskcache's shared default directory. The contender
passes `Cache(<tempfile.mkdtemp()>)` per trial and `rmtree`s it in teardown.

Confirmed in `pkg/query.py:14-27`: `retrieve()` reads `cache.get(key)` but only writes
`cache[key] = result` `if query.get("persist", False)`. Plain vgplot mark queries never set
it, and `exec` bypasses `retrieve()` entirely (`server.py:85`). Verified live: after a full
load + `json` + `arrow` round trip the cache dir held only `cache.db{,-shm,-wal}` — no
entries. So the per-trial dir is a safeguard, not a fix.

### 5. WebSocket URL — `ws://127.0.0.1:{port}/` (unchanged)

`app.ws("/*", ...)` (`server.py:154`) matches any path, so the fork's URL works verbatim
against the official server. `probes/mosaic_server.html.j2` was not touched.

### 6. Gotcha found: a failed query answers **HTTP 200**

`HTTPHandler.error` calls `res.write_status(500)` *after* `http_handler` has already written
the CORS headers (`server.py:135-152`, `71-73`), so uWS discards the status. Measured:

```
POST {"sql": "... FROM '/nope/missing.parquet'", "type": "exec"}
  → 200  b'IO Error: No files found that match the pattern "/nope/missing.parquet"'
```

`raise_for_status()` is therefore blind, and a failed load would have sailed through
`preload` and resurfaced as a baffling "rendered no marks" ceiling. A successful `exec`
returns an **empty** body (`HTTPHandler.done` → `res.end("")`), so `_exec()` treats a
non-empty body as the failure signal and raises. Verified: a bad path now raises
`mosaic-server exec failed (200): IO Error: ...` out of `preload`.

## What changed

* `benchmarks/core/contenders/mosaic_server.py` — rewritten. The backend child is now
  `subprocess.Popen([sys.executable, "-c", _CHILD, port, cache_dir], stdout=DEVNULL)`
  running the official `pkg.server.server`. `subprocess` rather than `multiprocessing`
  spawn: an mp child re-imports this module and would drag polars/numpy (~100 MB) into a
  process whose whole job is to be a memory baseline; the subprocess child carries exactly
  the official server's own dependency set (`duckdb`, `socketify`, `diskcache`, `pyarrow`,
  `ujson`) — the same footprint as `uvx duckdb-server`. `.pid` feeds `psutil` and
  `terminate()/kill()` keep teardown identical. Retry-on-port-collision (3 ×) and the
  15 s TCP readiness wait are kept, plus a `poll()` early-out so a child that dies on
  import fails in milliseconds instead of burning 45 s.
* `benchmarks/mosaic_duckdb_server.py` — **deleted**, along with the `sys.path` shim that
  imported it.
* `tests/test_contenders_render.py` — unchanged; the existing
  `test_mosaic_server_renders_line` already covers both sources and the memory assertion.

## Validation

```
uv run pytest "tests/test_contenders_render.py::test_mosaic_server_renders_line" -q   → 2 passed
uv run ruff check/format benchmarks/core/contenders/mosaic_server.py                  → clean
```

Real browser + vgplot trials, 200k rows × 3 traces (`backend_timed_peak_mb` is the VmHWM
window on the spawned pid — the memory contract holds):

| chart | source | total_ms | backend timed peak MB | preload peak MB | resident MB |
|---|---|---|---|---|---|
| histogram | in-memory | 114.2 | 13.4 | 15.3 | 15.3 |
| line | in-memory | 123.6 | 72.9 | 22.3 | 22.3 |
| line | disk-parquet | 189.3 | 108.0 | 1.9 | — |

The disk row reads exactly as the view semantics predict: near-zero preload, the scan
charged to the timed window.

## Follow-up for the orchestrator

`CLAUDE.md:139` still says *"Mosaic-server runs `benchmarks/mosaic_duckdb_server.py`
(socketify + duckdb, no diskcache) as a spawned child, starting table-less so the empty
backend can be memory-baselined before `POST /load` builds the store."* That file is
outside this task's ownership and other agents are editing it. It should become: runs the
official `duckdb-server` (`pkg.server.server`) as a spawned subprocess on a free port,
starting table-less, with the store built via an `exec` SQL POST.

---

## Re-spike against duckdb-server 0.30.0 (2026-08-22, Phase 7.7)

The suite moved `duckdb-server` 0.26.0 → **0.30.0** and `duckdb` 1.5.3 → **1.5.5**. This
spike's conclusions were re-verified against the new source, because a workaround kept
after upstream fixed it is how a benchmark grows fiction. All four still hold, with
moved line references:

| Finding | 0.26.0 | 0.30.0 | Verdict |
|---|---|---|---|
| Listen port hardcoded | `server.py:169` | `server.py:168` — still `app.listen(3000, …)` | Injection **still required** (fresh port per trial). |
| CORS headers written before the 500 | `server.py:135-152` | `http_handler` writes them at `server.py:132-137` before dispatch; `HTTPHandler.error` calls `write_status(500)` at `server.py:69-71` | Bug **still present**: uWS drops a status written after headers, so a failed query is HTTP 200 with the error in the body. `_exec`'s body-not-status check **stays**. |
| `pkg.server.server(con, cache)` entry point | as spiked | unchanged | Child spawn code **unchanged**. |
| Cache stores only `persist: true` queries | `query.py:25` | `query.py:25`, unchanged | Per-trial diskcache dir **still justified** (isolation, not cache neutralization). |
| duckdb Arrow-IPC reader | absent in 1.5.3 | still absent in 1.5.5 — `read_arrow`, `scan_arrow_ipc` and `read_ipc` all raise `CatalogException` | In-memory handoff **stays Parquet**. |

Nothing became unnecessary, so nothing was deleted. All 19 workload gates and the full
suite pass on 0.30.0/1.5.5.
