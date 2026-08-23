# Fallback licence texts

Licence texts for packages whose published npm tarball omits a `LICENSE` file, so
`gen_notices.py` cannot read one from `node_modules/`. Each file here is the licence text
from the upstream project repository, retrieved once and committed so the notices can be
regenerated offline.

| Package | Declared licence | Source |
|---|---|---|
| `@duckdb/duckdb-wasm` | MIT (`package.json`) | <https://github.com/duckdb/duckdb-wasm/blob/main/LICENSE> |

Filenames are the package name with `/` replaced by `__`.
