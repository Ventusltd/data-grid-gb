# Indicative corridor engine: method, service contract and gates

Status: proposed v2 design, 2026-09-03. The straight-line GridAtlas feature is
retained unchanged. This document specifies an additional screening mode.

## Product contract

Call the output an **indicative screening corridor**. Do not call it a cable
route, recommended route, feasible route, least-cost route or connection route.
Every response and every UI card must carry this boundary:

> Screening geometry only. It is not an engineering design or a connection
> assessment and does not establish feasibility, capacity, commitment, land
> rights, consent, construction method, cost, commercial terms or connection
> date.

The service reports measurements and assumptions. It never reports that a
project can connect to a substation. Circuit counts, ratings, impedances and
fault levels remain published network facts, not available connection capacity.

## Why this is not road satnav

A cable may cross open land, align with an existing corridor, cross a road at a
high angle, pass under a railway or river by a separately engineered method, or
detour around a constraint. A shortest-path search over roads alone cannot
represent that choice and could be more misleading than the straight line.

Use a hybrid graph:

1. An off-road least-cost surface represents traversable land and explicit
   exclusions at a declared resolution.
2. Road and suitable existing-infrastructure edges are optional alignment
   corridors, not mandatory travel lanes.
3. Typed crossing portals connect the surface across main/ordinary water,
   railway, strategic road, flood-defence and known-utility barriers.
4. Separate connector classes represent open cut, HDD and tunnel screening.
   A connector is a scenario, never a construction decision.
5. A K-shortest or Pareto-label search retains materially different options
   rather than hiding every consideration inside one arbitrary scalar score.

All geometric operations use a suitable British National Grid working CRS
(EPSG:27700) and return WGS84 GeoJSON for display. Geodesic distance remains the
unchanged baseline for comparison.

## Required inputs and provenance

| input | minimum routing attributes | status before release |
|---|---|---|
| project and substation endpoints | stable id, location source, match method, uncertainty | ETYS geometry coverage and ambiguous joins exposed |
| roads | stable node/link ids, class, access, direction where relevant, bridge, tunnel, layer, source date/licence | reacquire a routable product; v8 display files fail this gate |
| rail | owner, operational/type status, bridge, tunnel, layer, source date/licence | reacquire and validate topology |
| water | main/ordinary/type, width where known, culvert/bridge relation, jurisdiction | England, Wales and Scotland handled separately |
| terrain and ground | elevation/slope, superficial and bedrock geology, made ground where available | coverage and resolution published |
| environmental constraints | designation type, legal regime, date, permitted use | penalties/exclusions separately configurable |
| flood and coast | zone/type, defence geometry, tidal status, date | jurisdiction and uncertainty retained |
| existing utilities/corridors | class, status, access/licence, geometry accuracy | never assume colocation rights |

Every active input is pinned by content digest or immutable release identifier.
The response exposes the exact dataset bundle and ruleset version used. A layer
that lacks sufficient authority, licence or geometry metadata may be displayed
but cannot influence routing.

## Measurements, not a magic score

For every candidate publish the full vector:

- total length, straight-line length and detour ratio;
- open-land, road-aligned and other-corridor length;
- main-river, ordinary-watercourse, rail and road crossings by type/owner/class;
- compound crossing groups and indicative HDD/open-cut/tunnel connector length;
- protected-site, flood-zone, woodland, settlement and other constraint overlap;
- elevation gain, maximum/percentile slope and geology coverage;
- estimated joint/access count as an explicitly sourced screening assumption;
- missing-layer, low-resolution and endpoint-location uncertainty; and
- the weight profile used to rank the candidate.

Weights are named, versioned scenarios. A default cannot erase the raw vector.
Recommended initial profiles are:

| profile id | map colour | objective |
|---|---|---|
| `baseline_geodesic` | magenta | existing straight-line lower bound; never removed |
| `shortest_hybrid` | violet | shortest traversable hybrid corridor |
| `road_alignment` | cyan | favour suitable road/existing-corridor alignment |
| `fewer_complex_crossings` | lime | reduce rail, major-water and strategic-road events |
| `lower_environmental_overlap` | amber | reduce overlap with declared environmental constraints |

Colours identify scenarios, not quality grades. The card must allow the user to
compare or hide options and inspect the measurements that caused the difference.

## Service boundary

Keep route computation outside the browser. Precompute the common project to
nearby-substation matrix in CI, publish a compact immutable product, and add a
dynamic endpoint service only after the same test suite can govern it.

An illustrative request is:

```json
{
  "origin": {"type": "repd_project", "id": "..."},
  "destinations": [{"type": "etys_site", "id": "..."}],
  "profiles": ["shortest_hybrid", "road_alignment", "fewer_complex_crossings"],
  "dataset_bundle": "gb-corridor-inputs.2026-09-03",
  "ruleset": "gb-corridor-screening.v1"
}
```

The response is a GeoJSON FeatureCollection plus:

- immutable request, engine, dataset and ruleset ids;
- candidate rank within each profile and a stable geometry hash;
- the complete measurement vector and typed `crossings[]` events;
- source ids and source dates for each contributing layer;
- assumptions, data gaps, fallbacks and search diagnostics; and
- `not_an_inference_of` containing at least `connection_feasibility`,
  `available_capacity`, `queue_position`, `commitment`, `consent`,
  `constructability`, `construction_method`, `cost`, `commercial_terms` and
  `connection_date`.

No route is returned when required data is missing or the search fails. The
service must distinguish `no_path_in_model`, `coverage_missing`,
`endpoint_unlocated`, `timeout` and `unsupported_jurisdiction`; none means that
a real route is impossible.

## Interaction contract

The route feature must be discoverable on every input method:

- Keep ordinary project/substation click and the existing five straight neon
  lines unchanged.
- Add a clearly labelled `ROUTE OPTIONS` or `TRACE` action to the selected
  project's information card. On touch it is a minimum 44 px control and may
  open a bottom sheet; on desktop the same action is present in the card.
- A desktop right-click on a straight line may be a convenience shortcut to
  the same command, but it cannot be the only way to discover the feature.
- Long-press is optional and cannot be the only phone interaction because it
  conflicts with pan, selection and browser behaviour.
- While running, preserve straight lines, show progress and offer cancellation.
  On success, add route overlays in a separate layer group with a legend and
  per-option visibility. On failure, preserve the baseline and report the
  machine-readable failure reason in plain language.

Do not overload the existing Measure control or the editable SLD polyline.
Those answer different questions and should remain independently usable.

## Acceptance gates

### P0: truth and input integrity

- Fix and regress the same-site transformer double count before using site
  equipment counts as route-card context.
- Resolve the two-hop adjacency discrepancy with a fixture and declared edge
  policy.
- Pin the consumer product rather than fetching a mutable `main` URL.
- Reacquire road, rail and water inputs from named authorities with licence,
  attribution, schema, refresh and content-integrity records.
- Add B/local access where the chosen product supports it; record all discarded
  routing attributes and why.
- Publish endpoint coverage and ambiguous/unlocated ETYS joins. Never fabricate
  a path to an unlocated point.

### P1: algorithm correctness

- Unit-test metric CRS, snapping, topology, bridge/tunnel/layer separation,
  intersection classification and compound crossing grouping.
- Compare the route solver against an independent Dijkstra/A* oracle on small
  graphs and exhaustive toy layouts.
- Make results deterministic across runs and machines for a pinned bundle.
- Prove that changing one profile cannot change another profile's raw metrics.
- Test jurisdiction borders and every failure state.

### P2: evidence calibration

- Treat the historical 95-row result as an exploratory cohort, not a holdout.
  It was obtained only after requiring mapped/joined coordinates at both ends,
  positive `cable_km`, distinct endpoint sites and a straight separation above
  1 km. Those 95 circuit rows represented only 59 endpoint pairs in the
  pre-remediation product; parallel rows are not independent route geometries.
- The wider pre-remediation population was 135 positive-length,
  coordinate-joined, inter-site cable rows over 79 endpoint pairs. The 1 km
  threshold removed 40 rows and was not a source-published quality flag.
- Rebuild the cohort after every location-join change. Freeze train and holdout
  sets by endpoint pair (never by individual parallel circuit row), fit only on
  the training pairs, and report circuit-weighted and pair-weighted results
  separately. Do not train and test on the same pair.
- After the Phase-0 fail-closed join repair, the same `> 1 km` predicate happens
  still to select 95 rows, but now covers 60 pairs and different members. On
  this corrected in-sample cohort, fixed `k = 1.245` gives 8.58% median error
  and 68/95 rows within 15%. Pair-weighting by mean published length gives
  9.39% median error and 40/60 pairs within 15%. The coincident row count does
  not make the two cohorts equivalent. The Phase-0 oracle exports member lists
  and SHA-256 membership digests; counts alone do not freeze a cohort.
- Record geodesic and every candidate error against published cable length;
  investigate outliers instead of silently removing them.
- Treat `cable_km` as length evidence, not as surveyed alignment truth.
- Use the Glaslyn scheme as the first detailed route/crossing fixture.
- Run sensitivity analysis for resolution, snapping and every profile weight.

Provisional release gates, to be confirmed after a frozen pair-grouped baseline
run: median absolute percentage length error below 15%, improvement over
straight-line length on at least 80% of held-out endpoint pairs, 100% provenance
coverage for every active layer, and zero silent fallbacks. These are
engineering targets, not claims already achieved. The exploratory factor
`1.245` was fitted and scored on the same 95 rows; its 8.45% circuit-weighted
median error and 69/95 rows within 15% are descriptive in-sample measurements,
not release accuracy. Pair-weighting those same rows (mean published length per
pair) gives 9.30% median error and 40/59 pairs within 15%.

## Delivery sequence

1. Freeze the claims, schemas, licences and benchmark split.
2. Build a source-normalisation package that emits topology plus provenance;
   keep it independent of GridAtlas rendering.
3. Implement the hybrid search and independent oracle tests.
4. Publish a versioned project-to-substation candidate product.
5. Add the universal route action and overlay renderer in the UI repository.
6. Run visual, accessibility, mobile-gesture and performance acceptance checks.
7. Only then consider arbitrary-click dynamic routing or cost scenarios.

No executable weight table is committed with this research note. Choosing
weights before the authoritative input bundle and benchmark are frozen would
turn literature observations into unsupported product facts.
