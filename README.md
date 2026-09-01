# data-grid-gb

The GB electricity **network**, as its operator publishes it, turned into
data products a browser can read.

The estate already owns prices in
[data-gb-electricity](https://github.com/Ventusltd/data-gb-electricity).
It did not own the network, and the consequence showed on the map: the
Atlas measured projects against OpenStreetMap substations, which know a
name and a location and nothing else. They do not know a circuit's
impedance, a node's fault level, a substation's seasonal rating, or which
circuits the system operator has already published a plan to change.

All of that is public. NESO publishes it every year in the Electricity Ten
Year Statement appendices. This repository fetches those appendices, pins
them by SHA-256, derives clean products, and verifies them before anything
is allowed to read them.

## The products

### `derived/connection-points.v3.json` — browser-sized

Every transmission substation NESO names at **132 kV and above** (886 of
them), each carrying what the operator publishes about it:

| field | what it is |
|---|---|
| `voltages_kv` | the voltages present at that site |
| `circuits`, `transformers` | how many meet there |
| `circuit_winter_rating_mva` | the range of winter ratings on those circuits |
| `reactive_compensation` | installed units and their MVAr generation / absorption |
| `fault_current` | explicitly site-wide envelopes for all eight separately named Appendix D metrics; may combine buses and voltages |
| `fault_current_by_voltage` | the same published rows separated by voltage before any envelope is calculated |
| `planned_changes`, `planned_change_years` | changes already published for 2026/27 → 2033/34 |
| `location` | coordinates, where a join to mapped geometry exists, and **how** it was matched |

502 of the 886 carry coordinates. The 384 that do not are **published
without them rather than dropped** — a consumer that needs to know a node
exists should not be told it does not merely because nobody has mapped it.

### `derived/gb-transmission-network.v1.json` — topology and equipment parameters

921 sites · 2,679 nodes · 1,392 circuits with R/X/B on a 100 MVA base and
seasonal ratings · 1,472 transformers · **2,230 planned changes** · 573
reactive compensation units · 11 interconnectors · the labelled Appendix D
fault-current scenarios. This is not a runnable or solved load-flow case.

## What these products are not

**Nothing here says a project can or cannot connect at a node.** A rating
is a rating and a fault level is a fault level. Queue position, committed
connections, consent and commercial terms decide connection, and no
published appendix contains them. Both products carry that sentence in
their own payload, and the verifier fails if it is ever removed.

## Two things worth knowing before you use it

**Node codes are decoded, and the decoding is derived.** A node is a site
code (up to four characters), a digit for the voltage level, then a busbar
or bay suffix — `COTT41` is Cottam, 400 kV, busbar 1. The digit convention
is not documented in the appendix, so the build derives it by counting how
each digit co-occurs with the voltages its site declares, publishes those
counts in the product, and counts the nodes whose inferred voltage their
site does not declare rather than silently correcting them.

**ETYS names substations; it does not locate them.** Coordinates come from
the OpenStreetMap-derived payload published with the GridAtlas release,
joined on a normalised name and then constrained by the site's highest
published voltage. Text equality alone is not identity: ambiguous exact or
token matches are withheld. Every tier is counted in v3 (`exact_name` 461,
`distinctive_tokens` 41, `ambiguous_exact_name` 25,
`ambiguous_distinctive_tokens` 47, `unlocated` 384) and every located point
records `matched_by`, so a consumer can decide how much to trust any join.

v2 remains immutable because a deployed consumer requires its schema. It
contains site-wide fault-current envelopes and the earlier name-only location
join; new consumers must use v3.

## Running it

```bash
python pipelines/fetch_sources.py          # pull and pin the public sources
python pipelines/build_network_model.py    # ETYS Appendix B + D -> the model
python derived/build_connection_points.py  # -> the browser product
python derived/verify_connection_points.py # fails closed
```

## Sources

NESO, *Electricity Ten Year Statement 2025* (published 30 June 2026):
Appendix B system technical data, Appendix D fault levels (peak and
minimum). Appendices A (system schematics) and C (power flow diagrams) are
fetched for reference with `--with-reference` and are not parsed.
Substation geometry: OpenStreetMap contributors, via the GridAtlas
release. Document ids and SHA-256 digests are recorded in
`sources/sources-manifest.json`.
