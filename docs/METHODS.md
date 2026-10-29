# Methods: data-centre pipeline and conventional generation

This covers `datacentres/` and `conventional/` only. The ETYS network products
(`derived/`, `pipelines/fetch_sources.py`, `pipelines/build_network_model.py`) keep
their own documentation in the README.

The work is tracked in GridAtlas issue
[#73](https://github.com/Ventusltd/gridatlas/issues/73). This file covers steps 2–6:
the fetcher, schema, de-duplication, conventional assets, and the repository and CI.

## Why it lives here

The choice was between three places:

| option | verdict |
|---|---|
| **`Ventusltd/data-grid-gb`** (this repository) | **chosen** |
| a new repository | rejected for now |
| `Ventusltd/lightning` | wrong scope |

Reasons for choosing this repository:

1. **It already does this job for the network.** It fetches public GB grid sources,
   pins them by SHA-256, derives browser-sized products, verifies them before they
   are read, and refreshes them through Actions. Data centres are new demand on that
   network. Conventional stations are existing generation on it. Both belong next to
   the nodes they would connect to.
2. **It already holds NESO and OpenStreetMap data, with attribution recorded.** The
   connection-register data that names data-centre demand is NESO data. The
   share-alike duty that comes with OSM geometry is already documented here.
3. **It is public.** Its licence is Apache-2.0 for code, the same as GridAtlas and
   lightning. That licence arrives through the open draft PR #1, which adds `LICENSE`
   and `NOTICE`. Until PR #1 merges, `main` carries no LICENSE file, so it should merge
   before or with this work.
4. **A new repository costs more than it buys today.** It would need its own licence,
   CI, branch protection and consumers, for two folders that share this repository's
   method. If the pipeline outgrows this repository, `datacentres/` and `conventional/`
   move out as they stand: they share no code with `derived/`.

Lightning is the wrong home: it is a weather-observation archive with its own release
cadence. This scaffold copies its verification style (`ci/verify_data.py`,
`reproduce.py`, two-leg tests) and none of its data.

## Layout

```
assetpipe/                  standard-library machinery shared by both domains
  pipeline.py               fetch, pin, normalise (FETCHER_CONTRACT is documented here)
  schema.py                 record schemas and validators
  xlsx.py                   optional openpyxl reader for spreadsheet sources
datacentres/ conventional/
  sources.json              registry: URL, publisher, licence, licence URL, open yes/no/unverified
  fetchers/<module>.py      one module per source (placeholders until a lane delivers)
  raw/<source>/<file>       raw bytes exactly as downloaded
  raw/MANIFEST.json         url, method, bytes, sha256, fetched_at of every raw file
  normalised/<source>.geojson  one deterministic FeatureCollection per source
  normalised/INDEX.json     sha256, count, licence, share-alike flag per output
  NOTICE                    licence and attribution for every source
pipelines/fetch_assets.py   CLI: list | fetch | normalise
ci/verify.py                fail-closed verifier (style of lightning ci/verify_data.py)
reproduce.py                offline reproduction with a summary digest (style of lightning reproduce.py)
tests/test_assets.py        unit tests; run with and without requirements.txt
```

## The open-data rule, as code

`sources.json` gives each source `"open": "yes" | "no" | "unverified"`.

- **Fully open** means OGL v3, CC BY, CC0, public domain, or ODbL with attribution.
  Non-commercial, approval-based and paid sources are `no`.
- `unverified` means nobody has yet quoted the licence that applies to the exact file
  being fetched.
- `fetch` skips `no` always. It skips `unverified` unless `--include-unverified` is
  given, which takes a snapshot for review only.
- `normalise` writes output only for `yes`. `ci/verify.py` fails if an output exists
  for a source that is not `yes`, or if the output's licence differs from the
  registry's.
- A source marked `no` may not have a ready fetcher. The verifier and the tests both
  check this.

## Pinning and determinism

- Each fetcher declares fixed requests: no "latest" links and no dates computed from
  today.
- The first fetch records each file's SHA-256. A later fetch that returns different
  bytes fails until it is rerun with `--update-pins`, so an upstream change becomes a
  reviewed diff.
- An unchanged re-fetch keeps the original `fetched_at`, so it leaves no diff.
- Raw files larger than 25 MiB are pinned by hash but not committed
  (`"committed": false`).
- A normaliser reads only the pinned bytes and must not use the network, the clock or
  randomness. Output is sorted by id and serialised canonically, one feature per line.
  `reproduce.py check` re-runs every normaliser and fails if the result is not
  byte-identical to the committed file.

## Data-centre record (`gb-datacentre-pipeline-record.v1`)

| field | rule |
|---|---|
| `id` | `<source>:<source ref>`, a lower-case slug; stable across fetches |
| `name`, `operator` | string or `null`; never `""` for unknown |
| `status` | `operational`, `under_construction`, `consented`, `in_planning`, `connection_request`, `announced`, `unknown` |
| `it_load_mw` | the IT (critical) load the source states, or `null` |
| `grid_connection_mw` | the import capacity the source states (e.g. a connection register), or `null` |
| `dates` | any of `announced`, `application`, `consented`, `connection_offer`, `energisation`, `operational`, as `YYYY`, `YYYY-MM` or `YYYY-MM-DD` |
| `source` | `id`, `licence`, `licence_url`, `evidence_url` (https) |
| `location` | `lon`, `lat`, `precision`: `exact`, `site`, `address`, `postcode`, `locality`, `region` or `unknown` (when `unknown`, there is no point and the geometry is `null`) |

Rules the validator enforces:

- **IT load and grid connection are two fields.** A record carrying a bare `mw`,
  `capacity_mw`, `power_mw`, `load_mw` or `demand_mw` is rejected. `reproduce.py`
  reports the two quantities separately and never adds them together.
- **Unknown is null, not zero.** Zero or negative MW is rejected, and so is an empty
  string.
- **A pipeline entry is not a commitment.** No status means "committed" or
  "confirmed". `connection_request` and `announced` are stages, not promises.
- **Proximity is not connection, capacity or feasibility.** Every normalised file
  carries three honesty sentences, and the verifier fails if any is removed or altered.

## Conventional generation record (`gb-conventional-generation-record.v1`)

This record uses the same `id`, `name`, `operator`, `source` and `location` fields,
plus:

- `fuel`: one of `gas`, `coal`, `oil`, `nuclear`, `biomass`, `efw`, `diesel`,
  `gas_oil_dual`, `other_thermal`;
- `status`: one of `operational`, `mothballed`, `closed`, `under_construction`,
  `consented`, `in_planning`, `unknown`;
- `capacity_mw`, with a required `capacity_basis` stating what the MW is, for example
  "DUKES 5.11 installed capacity";
- `commissioned_year`.

Renewables are excluded because REPD covers them. Reconciliation with the GridAtlas
offtakers power-station class belongs to step 5 and its lane. Its output will be a
normalised product from these sources, never an edit to them.

## De-duplication (step 4)

Per-source outputs are never edited to remove duplicates. A merged product will be a
separate file, `normalised/merged.geojson`, with:

- every contributing `source.id` and record id kept as provenance;
- each disagreement on status, MW or location listed as a conflict rather than resolved
  silently.

Licence rule for the merged product: if it includes any ODbL source
(`share_alike: true`, which today means OpenStreetMap), the merged database is ODbL.
`INDEX.json` records this as `combined_licence_note`.

## Verify and reproduce

```bash
python pipelines/fetch_assets.py list                       # sources, open status, fetcher status
python pipelines/fetch_assets.py fetch all                  # network; fully open + ready only
python pipelines/fetch_assets.py normalise all              # no network
python ci/verify.py                                         # fail closed
python reproduce.py check --json reproduction.json          # offline; prints summary_sha256
python -m unittest discover -s tests -v
```

Two people who run `reproduce.py check` on the same commit get the same
`summary_sha256`. That digest is a content hash, not an identity signature. To sign
it, use `ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n file reproduction.json`.

## CI

- **`.github/workflows/test-assets.yml`** runs on push and pull request. It has two
  legs:
  - `requirements` installs `requirements.txt` (openpyxl) and may not skip the
    spreadsheet test, because `ASSETS_REQUIRE_DEPS=1`;
  - `bare` proves that fetch, verify and reproduce need only the standard library.

  Each leg runs pyflakes, the unit tests, `ci/verify.py` and `reproduce.py check`.
- **`.github/workflows/fetch-assets.yml`** runs only on manual dispatch, and there is
  **no schedule**. The sources update at different rates:
  - DUKES: once a year;
  - WRI: frozen since 2021;
  - OSM and Wikidata: continuously;
  - registers: monthly or ad hoc.

  A changed pin is a decision for someone who reads the diff. The run fetches,
  normalises, verifies and reproduces, uploads the snapshot as an artifact and, if
  asked, commits it to a new branch `fetch/<run id>`. It never pushes to `main`. A
  per-source schedule can be added once a source has a published cadence and a named
  reviewer.

## Status of the sources (at scaffold time)

Every fetcher is a placeholder until its lane (DC-A1..A6) delivers it. Licences
marked `unverified` stay unverified until someone quotes the exact terms with their
URL. See `datacentres/NOTICE` and `conventional/NOTICE`.
