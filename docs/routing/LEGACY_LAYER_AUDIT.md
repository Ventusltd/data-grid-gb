# GridAtlas legacy layer and UI audit

Audit date: 2026-09-03. This is a read-only inspection of the local estate; it
does not approve any legacy file as a routing input.

## Which implementation exists

The proposed path `C:\Users\vikra\OneDrive\Documents\GitHub\grid_atlasv8`
does not exist in the local checkout. The historical v8 oracle is at
`globalgrid2050/repd_grid_atlasv8`. The maintained application is the separate
`gridatlas` repository. Its ledger had advanced to v9.78 when this audit was
finalised on 2026-09-03; that is a timestamped observation, not an input or a
claim that the independently maintained composition will remain at that version.

The historical v8 shell registers 400, 275, 220, 132 and 66 kV line and
substation layers; mainline railway and Eurostar layers; and motorway, trunk and
primary road layers. It does not register a secondary/B-road layer or a river
or ordinary-watercourse layer. Its London Underground fetcher acquires station
points, not track geometry. Eurostar route-relation geometry should not be
assumed to be a unique, topologically noded physical railway graph.

## Road-file measurement

The three retained road GeoJSON files were counted independently by treating
each consecutive coordinate pair in a LineString as one segment:

| file | features | coordinate-pair segments |
|---|---:|---:|
| `uk_motorways.geojson` | 17,713 | 133,642 |
| `uk_trunk_roads.geojson` | 130,228 | 848,251 |
| `uk_primary_roads.geojson` | 163,790 | 1,104,914 |
| **combined** | **311,731** | **2,086,807** |

An exact-coordinate endpoint graph contains 2,039,684 distinct vertices and 21
connected components; the largest contains 94.6% of vertices. This establishes
that there is substantial reusable geometry. It does not establish routability:
geometrically crossing lines may not be noded, bridges and tunnels may appear to
intersect at grade, access constraints may be missing, and endpoints still need
auditable snapping.

The combined graph is also too large to parse and route repeatedly in a phone
browser. Common project/substation candidates should be precomputed, or a
service should return only compact result geometry.

## Why the files remain display-only

The retained OSM acquisition scripts emit coordinate LineStrings and a limited
tag subset. The data-plane transplant contract omits route-critical properties,
including durable node/link identity, `ref`, `oneway`, `access`, `bridge`,
`tunnel`, `layer` and surface information. Its quarantine records also mark the
authority/licence position unverified and the assets not publishable as product
inputs.

Those are not paperwork-only defects. Without level/bridge/tunnel information,
a pathfinder can create a false turn between physically separated roads or
between road and rail. Without access and road reference it cannot correctly
measure or explain the corridor it favours. Without durable source identity it
cannot reproduce or refresh a result safely.

Use the existing files to preserve the visual language and as a non-authoritative
development fixture. Reacquire a topology-preserving, licence-cleared network
before producing route findings. OS Open Roads is the primary candidate for the
GB road base; water and rail need their own authoritative, jurisdiction-aware
products.

## Current interaction baseline

The maintained GridAtlas selection workflow is newer than v8. An ordinary
project click currently:

1. selects substations at or above 33 kV;
2. calculates Haversine distance;
3. retains five results within 40 km;
4. draws a direct two-coordinate neon line to each; and
5. warns that the result is not a cable route or crossing assessment.

The current SLD tool separately supports a manually editable polyline and shows
routed versus straight length. Generated corridor geometry must stay in a
separate source/layer/state group: a detailed graph path cannot safely become
thousands of draggable SLD pins.

The least disruptive extension point is a universal project-card action labelled
`Explore route corridors`. A desktop context-menu command can call the same
action as a shortcut, but it is not a discoverable or accessible primary control
and has no dependable phone equivalent. On touch, use the same at-least-44-pixel
card button and a candidate/alternative bottom sheet. Straight lines remain
visible as the baseline while separately named neon corridor layers are loaded,
compared, hidden or cleared.

Implementation must retain stable project and substation ids. The current
substation display reduction keeps name, voltage, operator and coordinates but
drops feature id; joining a route response back by name would reintroduce the
known collision problem.

## Ownership boundary observed

No `gridatlas`, `globalgrid2050` or `data-gridatlas` file was changed during this
audit. The live composition and cartridge lane remains Claude-owned under the
current handover. This repository contains only the independent evidence,
product contract and acceptance gates that the implementation can consume.
