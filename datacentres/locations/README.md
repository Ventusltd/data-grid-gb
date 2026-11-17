# Data-centre addresses and screening locations

This is an additive address stage for the existing DC-A4 / Pipeline source data.
It does not replace `datacentres/lanes/dc-a4-osm-wikidata/dc_a4.py`, the original
OSM collector in `Ventusltd/data-centres-gb`, or the GridAtlas screening engines.
The original immutable Pipeline cohort remains unchanged.

The input configuration pins the existing 100-record Pipeline cohort and the
existing GridAtlas offtakers and DC-A4 mapped-site products by commit and SHA-256.
Collection reads those original Wikidata Q identities through the Wikidata API,
retaining entity revision, statement ID, rank, address language and reference
URLs for P6375/P969 street addresses, P281 postcodes and P625 coordinates.
Original and newly observed revisions remain distinct. Linked operator pages,
news articles, pictures and directory text are not fetched or copied.

## Collect, build and reproduce

From the repository root, with Python 3.11+:

```sh
python -m pip install duckdb==1.3.2
python -B -m unittest discover -s tests -p 'test_dc_address_locations.py' -v
python -B -m datacentres.locations.pipeline collect --output /new/generation --parquet
python -B -m datacentres.locations.pipeline build --snapshot /new/generation --output /new/replay --parquet
```

`collect` is the only network stage. It permits only the pinned raw-GitHub
inputs, structured Wikidata API, and postcodes.io. It allows at most 32 requests,
8 MiB per response and 32 MiB total; failures do not replace an existing
generation. Output paths must be new. The offline stage rechecks every captured
resource's actual byte count and digest, plus cohort/reference pins, before
building. A final manifest marks a completed generation. Source hashes establish
byte identity, not source accuracy or independent rights/evidence acceptance.

The included generation `data/20261117T002200Z` was actually collected and
independently reviewed: 100 original IDs, 38 street-address records, 39 Wikidata
points, 25 approximate postcode centroids, and 36 null geometries. Its 49
cross-references are name candidates only. The compact source archive contains
every original input/response needed to reproduce it offline, including the
already-owned reference feeds; expanded copies are not duplicated in Git.
Check the archive against `manifest.json`, unpack into a new scratch folder,
and pass that folder as `--snapshot`. The test suite performs this real-data
replay and compares the actual GeoJSON and Parquet bytes.

`datacentre-locations.geojson` keeps every source record, including null geometry.
The optional ZSTD Parquet contains every full feature as well as typed ID,
location and precision columns. Every row is compared after readback, and two
separate compiles must produce identical bytes. Browser consumers should pin the
small GeoJSON projection and its manifest; do not download the entire archive.

The manual **Data centre address locations** GitHub workflow runs the same tests,
collects, rebuilds offline, compares actual GeoJSON/Parquet bytes and uploads the
complete source bundle as an artifact. Pull requests run offline tests only.
The job has read-only repository permissions; it never commits, merges, changes
an application pointer or installs a schedule. Dataset promotion still uses the
data owner's existing review and source-rights gates.

## Precision, reuse and unknowns

- A single usable nondeprecated/preferred Wikidata Earth-coordinate statement
  retains its stated precision. Conflicting coordinate statements remain
  unmapped rather than being silently replaced by an address guess.
- A single full GB postcode from the source address/postcode statements can
  resolve through postcodes.io. This produces
  `POSTCODE_CENTROID_APPROXIMATE`, not a building entrance, verified facility
  location or arbitrary-radius accuracy promise. Missing geocodes remain null.
- Multiple postcodes require review. Northern Ireland BT postcode geocoding is
  withheld because the provider documents different commercial reuse terms.
  Existing CC0 Wikidata coordinates are a separate source and remain attributable.
- Wikidata structured statements use CC0. GB postcode data carries the provider's
  OS/Royal Mail/National Statistics/NRS attribution. The source terms are recorded
  at <https://postcodes.io/docs/licences/>. OSM-derived reference relationships
  retain OpenStreetMap attribution and ODbL share-alike; their existing per-source
  terms and repository acceptance requirements remain authoritative.

Cross-references keep the dataset namespace and original record ID. An explicit
facility Wikidata ID is a source-ID join, not independent facility verification.
Operator Wikidata IDs are deliberately excluded. Exact-name matches are
`CANDIDATE` only; their coordinates never fill an unknown location. Distance
between two sourced points is recorded for review and never grants identity.
UK reference feeds are not matched to other countries by a shared name.

Legacy GridAtlas `osm_dc-N`/`gg2050-dc-N` links are snapshot-specific array keys,
not OSM node numbers. `OT-C-*`, `DC-OW-*`, `DCGB-OSM-*` and `dcuk-*` remain
separate namespaces. True typed `osm:node|way|relation:id` source elements, where
retained by `data-centres-gb`, must not be reconstructed from legacy suffixes.

## Existing screening and architecture

Every usable point has a coordinate URL to the existing GridAtlas arrival
screening. It passes longitude, latitude, a source label and technology only;
no invented REPD ID, capacity or status. The receiver computes an `ARRIVAL_LINK`
with identity kind `location`. Label the action **Screen¹**, and put the shared
footnote next to the table: **¹ Screening only; not a grid-capacity assessment.**
Postcode centroids must remain visibly approximate in a location column or
action title. The API does not establish a postcode-only arrival route.

The richer existing 30-substation/30-REPD/30-conventional vicinity panel is owned
by `gridatlas/tools/offtakers/src/06b-dc-engine.js` and its assembled overlay. Its
external arbitrary-data-centre selection API is not established; this stage does
not claim its coordinate URL opens that richer card. No second grid engine or
chart framework is introduced.

Architecture inspected before implementation:

| Owner | Inspected commit / responsibility |
|---|---|
| `data-grid-gb` | `01a4491dc66ec4e6319a05a394926043b9d992f9`: DC-A4/DC-A7 producer and source rights |
| `data-centres-gb` | `12c24a653e9d4eb3507857fd6e6d169b2dd1ff63`: OSM element-level Parquet/raw address tags |
| `gridatlas` | `1de35f9eca360a5d80d81542230fef546917f5c5`: layer host, coordinate arrival and existing vicinity UI |
| `ventus-grid-engine` | `5b5d11db47e1fbbe1823f8b570a0131fb4f1a8f9`: pure grid computation owner |
| `spiders` | `d9326b9fb64256a72830bd2c1461d6da20044d0f`: observed dependency graph versus planned plugin boundaries |

Spiders' ATLAS-13/lifecycle and ATLAS-39/chart plans are plans, not implemented
APIs. Original electricity charts remain in `gb-electricity-ui`, with price data
owned by `data-gb-electricity`. New proximity, cable routes, capacity, ownership,
connection, current-operation and behind-meter claims are not inferred here.
