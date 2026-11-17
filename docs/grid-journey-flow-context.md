# GJ-05 partial boundary forecast research context

`journey/flow_context.py` is an offline, standard-library prototype for one
source profile: NESO Day Ahead Constraint Flows and Limits, at the source's
named **constraint-group** granularity. It does not modify, fetch, enable or
publish a dataset, build a graph, infer a connection or draw a map/arrow.
No pipeline, source registry, scheduler, existing product or GJ-01 file changes.

This is a partial GJ-05 scalar/refusal contract. Positive-polarity review,
qualified absolute target times, UI integration and a release/rollback dossier
remain separate gates. UKPN primary-transformer and Elexon interconnector
profiles explicitly return WITHHELD; they are not implemented adapters and
cannot inherit boundary semantics.

## Observed bounded input

The fixtures preserve the exact provided API response bytes, metadata and local
observation receipt. They are research inputs, not a production source grant.

| Input | Bytes | SHA256 scope |
|---|---:|---|
| `neso-scotex-api.sample.v1.json` | 867 | Bounded three-row API response; **not** the raw CSV/dataset hash |
| `neso-day-ahead.metadata.v1.json` | 7,602 | Dataset metadata response |
| `neso-scotex.sample-receipt.v1.json` | 1,680 | Local observation receipt |

Pins and exact URLs are in `journey/fixtures/neso-source-context.pins.v1.json`.
The API response SHA256 is
`8d14ed0790a20555ac043e1ff5d9898537f011daab8a5d0fb6fce67535e2cee9`;
metadata SHA256 is
`fa1d3e3796e3bba6f2e3e699ae109dca9c1482828fc8b4d0ae78b548d2683b82`.
The raw CSV was not fetched or hashed by this adapter; its hash stays null.
No PDF or whole CSV is copied into this closure.

Dataset `day-ahead-constraint-flows-and-limits`, package
`cf3cbc92-2d5d-4c2b-bd29-e11a21070b26`, CSV/datastore resource
`38a18ec1-9e40-465d-93fb-301e80fd1352`, exact query key **SCOTEX**:

| Source target label (GMT/BST; UTC withheld) | Reported forecast MW | Reported limit MW |
|---|---:|---:|
| 2026-11-17T23:30:00 | 4548 | 3350 |
| 2026-11-17T23:00:00 | 4074 | 3350 |
| 2026-11-17T22:30:00 | 4115 | 3350 |

Values above the reported limit are genuine forecast observations in this
snapshot. They are retained without an actual-overload, available-headroom,
commissioning or circuit-flow inference. Negative forecast scalars retain their
reported sign; no physical positive direction has been independently accepted.

SCOTEX is not automatically B6. The adapter makes no crosswalk. An empty bounded
query reports only zero rows in that response; it does not establish dataset-wide
absence. The separately observed 32-key listing is partial, not exhaustive.

## Meaning and clocks

Resource metadata describes a day-ahead forecast after energy scheduling.
Subsequent changes are not reflected by this snapshot. It is not live measured
flow or per-circuit telemetry. The method change on **22 April 2024** stops
considering expected constraint mitigation/optimisation actions; unqualified
comparison across that break is withheld. The output points to the actual
metadata resource description, even if resource ordering changes.

Target labels in `Date_ Time GMT_BST` are retained as raw half-hour civil labels.
Their UTC instant, interval start/end convention and settlement-period mapping
remain unknown. No timezone dependency is installed and no DST algorithm is
invented. Fold/gap labels do not acquire a fabricated offset. Duplicate local
labels are refused, and the response must retain its stated descending source
query order rather than being silently sorted.

Resource `last_modified` is `2026-11-16T16:48:01.749033`, with no timezone
supplied. Package MTIME is separately preserved. Neither gains a made-up `Z`.
The local receipt supplies an explicit offset at
`2026-11-17T16:07:15.040240+00:00`, so only that observation clock can be
normalised to UTC. Publication, target labels and retrieval stay separate.

The output always has null `direction`, `polarity`, `arrow`, `headroomMw`,
`actualOverload`, `repdLink`, `etysLink` and `circuitLink`. Missing quantities stay
null; a published zero remains zero. Numeric values are precision-preserving
decimal strings with unit `MW` and source JSON type. `sourceRaw` retains a string
input or a lossless numeric value representation; the exact original JSON
notation belongs to the pinned response bytes.
There is no MW/MVA/MWh/current conversion or scalar-to-circuit projection.

The metadata references boundary diagrams, but this closure does not inspect
or accept a positive-direction diagram mapping. A downloaded-but-unreviewed
diagram is not sign evidence. UKPN inconsistent transformer polarity/current
non-directionality and Elexon series-specific import/export conventions need
their own independent source, clock, unit and rights review.

## Source rights and integrity

The dataset-specific licence declaration is in `result.licenses[].path`, not a
generic `license_id`: [NESO Open Data Licence](https://www.neso.energy/data-portal/ngeso-open-licence).
The output retains the required attribution:

> Supported by National Energy SO Open Data

The state remains `DATASET_LICENCE_DECLARED_REVIEW_REQUIRED`,
`productionApproved=false`, `publishable=false` and `productionEnabled=false`.
Structural parsing and hashes neither authenticate a reviewer nor grant rights.
This declaration does not resolve ETYS or unrelated dataset licence uncertainty.
Source values are never silently enabled through a registry change.

The caller must supply independent expected pins; the adapter does not derive
an expected digest from untrusted downloaded bytes. It checks all three byte
lengths/hash scopes, exact metadata/resource/profile, declared licence, source
authority, query, fields/units, observation clock and bounded row count. Invalid
inputs return explicit refusals. Unknown or null scalar fields remain partial
research observations rather than being replaced with zero.

## Reproduce

From the repository root, with Python's standard library:

```text
python -B -m unittest discover -s tests -p test_grid_journey_flow_context.py -v
python -B -m journey.flow_context --metadata journey/fixtures/neso-day-ahead.metadata.v1.json --sample journey/fixtures/neso-scotex-api.sample.v1.json --receipt journey/fixtures/neso-scotex.sample-receipt.v1.json --pins journey/fixtures/neso-source-context.pins.v1.json
```

The CLI writes one canonical JSON report to stdout and no files. Exit0 means
research scalars (possibly partial); Exit2 means withheld or an empty bounded
sample. Every output keeps publication/source enablement false. The context
digest is SHA256 of canonical JSON plus newline before adding `contextSha256`,
using the existing GJ-01 `encoded`/`sha256` helpers. There is no runtime clock in
the report, so identical inputs produce identical bytes.

The unchanged existing `test-assets.yml` already covers `journey/**` and
`tests/**`. No workflow is added or edited. The CLI intentionally lives under
`journey/**`: changes under `pipelines/**` would activate the existing network
refresh workflow on main and are outside this scope.

## Local verification and limits

The initial full offline discovery ran 97 tests: 95 passed, one skipped because
openpyxl was absent, and one existing reproduction test failed. Its DC-A2,
DC-A3 and DC-A7 products differed from recorded pins. The single failing test
was separately rerun in the untouched `d090de5` GJ-01 worktree and failed with
the same summary SHA256
`054d729b1a7346700b46e180b9373cac8026f3ba3d07ce3b6701b2c067b01e46`.
That baseline drift is preserved for its owner; no dependency was installed or
old product repaired here. The final **25 focused contract tests passed**,
including query ordering and malformed-authority refusals. All 18 original
GJ-01 tests passed in the full run. Hosted exact-head CI is a separate state.
