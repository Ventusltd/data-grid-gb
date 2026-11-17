# Grid Journey evidence contract — offline GJ-01

`journey/contract.py` validates `data-grid-gb.grid-journey-review.v1` receipts
and source-row references. `pipelines/grid_journey_crosswalk.py` creates a
deterministic inventory and empty review-ledger template from explicitly named
local input files. It performs no network fetch, collector scheduling, spatial
matching, source enablement or live write. No committed ETYS product is changed.

```powershell
python -m unittest discover -s tests -p test_grid_journey.py -v
python pipelines/grid_journey_crosswalk.py derived/gb-transmission-network.v1.json --points derived/connection-points.v3.json
```

The inventory records input hashes/counts, **zero manual identities approved**
and **zero eligible edges** by default. Automated point joins are not reviews.
An externally supplied `--ledger` can be structurally validated and classified;
the program neither authenticates reviewers nor decides that a licence applies.
Recorded acceptance is input from the independent source/rights review process.
The existing ETYS-specific licence uncertainty and OSM attribution remain intact.

An edge UID is `<exact source SHA256>:<kind>:<array index>`, where kind is
`circuit`, `transformer` or `planned_change`. Endpoint/kind/index references
must match that exact product. Two graph landings for one row are one record;
different input rows retain different UIDs even when their contents coincide.
Multiplicity needs its own recorded review. Counts are source-record counts,
not independently established physical equipment counts.

Ledger fields shared with the engine:

- `schema`, `sourceBundleSha`, `nodes`, `edges`.
- Node: `nodeId`, `sourceRef` (`url`, precise JSON/page/row `locator`),
  `identity` (`confirmed`, `unreviewed`, `ambiguous`), `rights` (`accepted`,
  `unconfirmed`, `blocked`) and optional `location`.
- Confirmed identity requires `review` with `id`, `reviewer`, UTC `reviewedAt`
  and HTTPS evidence `reference`. Accepted rights require `licenceUrl`,
  `attribution` and a separate rights `review` with the same receipt shape.
- Reviewed location requires WGS84 `[longitude,latitude]`, its own source,
  review and rights. Missing locations produce text-only unlocated steps.
- Edge additionally records `edgeUid`, `kind`, `sourceIndex`, exact `node1` /
  `node2`, `multiplicityStatus` and, when reviewed, `multiplicityReview`.
  A confirmed transformer requires an explicit `transformerRef`.
- `geometryStatus`: `verified_circuit_and_polyline`, `published_topology_only`,
  `published_node_unlocated`, `vicinity_screening_only`, or
  `ambiguous_or_licence_blocked`. Verified polylines require independent
  geometry source/review/rights and endpoints matching the reviewed nodes.

Unknown voltage, circuit voltage changes, missing identity/rights/multiplicity,
orphan nodes, planned edges and unsupported geometry are explicit refusals.
Topology-only coordinates are schematic, never cable bends. An explicit reviewed
transformer is required for a voltage transition; shared coordinates create no
edge. Existing node-code conventions and shortest-path behavior are unchanged.

GJ-02 consumes the same ledger and byte-pinned source in a separate engine.
These validators and synthetic tests do not establish a real reviewed route,
current switching state, power-flow direction, connection or available capacity.
