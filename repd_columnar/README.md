# Offline REPD DuckDB/Parquet prototype

This CPU analytical lane uses the **existing data-grid-gb owner**, not a new
repository or browser engine. It follows the incumbent data-centre export's
DuckDB/ZSTD/full-row-readback pattern. Parked intelligence PR9/11 is not treated
as an accepted importer. No collector, source registry, schedule, live importer,
current JSON switch, browser DuckDB-WASM or GPU/CuPy dependency is added.

Input is immutable product commit
`3a3106e248131f6e430c1ea03b4858d659755303`, edition `202611171609` (V9.39).
The pin names all 21 exact Git blobs, including 16 project partitions and the raw
source release descriptor. Actual scope
is **7,775 canonical projects / 3,604 solar**; the full 14,967-row raw source
population and the older 7,680-project scope are different. Existing DESNZ/OGL attribution
and source policy remain authoritative; this processing grants no new rights.

```powershell
python -m pip install -r repd_columnar/requirements.txt
$env:REPD_SOURCE_ROOT='<existing product checkout with the pinned Git commit>'
python -B -m unittest discover -s repd_columnar/tests -v
python -B -m repd_columnar --source-root $env:REPD_SOURCE_ROOT --out '<new caller-owned directory outside all Git worktrees>' --technology solar --submitted-year missing --limit 100
```

Dependency setup and Git checkout can use network. Once they exist, processing
and tests are offline; no remote source is fetched during export. The optional
read-only CPU Actions workflow runs on scoped PR changes or manual dispatch,
with a fixed source checkout, separate dependency setup and 10-minute timeout.
It uploads candidate artifacts only. No production data or live pointer is written.

Outputs are `repd.parquet`, a bounded `filtered.json`, byte-identical
`solar1000-manifest.json`, offline-only `benchmark-baseline.json`, measurements
and a manifest. The Parquet table has all original fields, original source
ordinal, nullable frozen-pilot ordinal and an authoritative complete `record_json`.
Nested objects/lists use JSON columns; all eight milestone/update dates use DATE.
Every original row and every typed field is compared after readback. Unicode,
nulls, IDs, relationships, original order, the 1,000 seed IDs and their order remain
unchanged. The frozen pilot manifest SHA256 is
`c625edbb9c471a1e9a9034a1afe99881fbfabb1fcadcf0623355931f5b6aed1f`.

Unknown capacity remains null, never zero. Dates remain calendar days, not
invented timestamps. First-seen/last-seen fields are not inferred from REPD
record update, planning submission or processing time. Source/package SHA256,
raw CSV/workbook hashes and output-package hashes have separate names and meanings.
The raw hashes are reference values checked against the pinned product release
descriptor; this export does not fetch or rehash the raw CSV/workbook. Its 14,967
original-source population counts unique REPD references; the raw descriptor
records 14,968 CSV rows including a duplicate.
Nothing is declared independently VERIFIED by a serialization check.

The limited projection supports technology and independent submission/update/
operational year filters, `missing`, a source-order limit of 1..1000, exact
matching count and truncation disclosure. Filter values are parameterized and
validated. Output paths must be NEW, outside every Git worktree; overwrite,
source-directory writes and path traversal are refused. Pins are checked before
creating an output directory. A failed later write leaves its new candidate
directory for inspection; it never replaces an accepted artifact.

Measurements report all three alternating samples per format. JSON includes
file read, full parse and filtering; Parquet includes a fresh DuckDB connection,
query/readback and returned-record decoding. CPU and elapsed time are separate.
**OS cache is uncontrolled**; these are not cold-cache or browser-transfer tests
and do not establish a universal speedup. The full JSON baseline is for offline
comparison, not browser delivery. File sizes compare ZSTD Parquet with uncompressed
JSON; the manifest also records a gzip JSON size reference, without claiming an
HTTP transfer or gzip query measurement. A future reviewed server/export step may serve
small projections; no application consumer is changed by this prototype.
