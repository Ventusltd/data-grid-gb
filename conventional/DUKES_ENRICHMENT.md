# Issue #76 — DUKES Table 5.11 conventional-generation enrichment (review only)

Source of truth: the actual DESNZ May 2026 source workbook, [DUKES 5.11](https://assets.publishing.service.gov.uk/media/6a6a365b0ddb7e4831c629f3/DUKES_5.11.xlsx), 1,247,443 bytes; SHA-256:
\`760286f4cb3168f04a5ee1b581dbfcb35e1d65a5ea0151bb2dd4dc12dca2c72a\`.
May 2026 values were published **30 July 2026** (verified directly from the workbook's Cover Sheet).
Licensing: **OGL v3.0** (DESNZ). [OGL terms](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
The separate derived GridAtlas dataset remains **ODbL 1.0** if any source records carry OSM components; retain © OpenStreetMap contributors and ODbL share-alike; do not silently relicense the merged database as OGL.

## Verified spreadsheet schema

- Sheet \`5.11 Full list\`, column labels at row 6, first station row 7. Columns include \`Site Name\`, \`Company Name\`, \`Primary Fuel\`, \`InstalledCapacity (MW)\`, BNG \`X-Coordinate\` and \`Y-Coordinate\`, \`Postcode\`, and \`DESNZ site code\`.
- Only **major power producer stations operational as at May 2026**. It is not a complete UK register of small power stations, mothballed sites or assets in planning.
- 94 reviewed conventional units/rows: 52 Natural Gas, 17 Diesel/Gas Oil (not presumed diesel alone), 12 Biomass, 8 MSW (municipal-solid-waste energy recovery), 5 Nuclear. There are zero operating coal entries in this sheet; that does not prove that a separate closed or other class plant has no historical capacity.
- Solar, wind, hydro and pumped hydro rows are excluded from the *conventional* table and must not be silently relabelled as fossil generation.
- **Marchwood Power**: DUKES \`GAS-000354\` (sheet row 667), Natural Gas, CCGT, **898 MW installed**, data May 2026. The old WRI-derived record had 842 MW installed. Neither value is a grid-connection capacity, TEC, DNC or electricity demand.
- Historic changes sheet lists the 2023 closure of Kilroot's coal units and the subsequent 2024 opening of new gas units. Do not infer identical identity or combine active and closed fuel capacities.

## Conservative reconciliation

\`conventional/fetchers/dukes_5_11.py\` is fail-closed on source SHA, exact sheet/header layout, duplicate DESNZ code, and zero or invalid stated installed MW; retains the source's fuel name, technology, date, site code, published OSGB coordinates, operator and publication URL. No invented WGS84 coordinates.

\`conventional/dukes_reconcile.py\` joins **only** if the *entire station name* (allowing a redundant trailing "power station" / "power plant" / "generating station" / "power") and the *entire known operator* (allowing Ltd/Limited/plc suffix) uniquely identify one old site and source site. Multiple source installations with that name must share published postcode and fuel before aggregation. Do not join by radius, fuzzy name or a guessed owner. Conflicting closures/conversions or fuels stay unmatched pending human investigation. All 301 original features and historical capacities are retained as a separate, untouched baseline.

Empirical CI against the pinned 2026 source and 301-site old output (data-grid-gb Actions \`40963616914\`, requirements leg):

| Metric | Before | Review-only derived |
|---|---:|---:|
| Conventionals in dataset | 301 | 301 |
| Fuel unstated | 167 | 167 |
| Exact source-verified station/operator matches | — | 11 |
| Unmatched baseline sites | — | 290 |
| Candidate matching fuel conflicts quarantined | — | 2 |
| DUKES 2026 conventional source rows | — | 94 |
| DUKES rows not linked to an existing distinct site | — | 83 |

The 167 fuel-unstated sites remain **unknown**; DUKES MPP coverage does not justify inventing their fuel or capacities. Successful compilation is not evidence the whole GB small-generator estate is covered.

## Reproduce

1. Obtain the raw XLSX in the path \`conventional/raw/dukes-5-11/DUKES_5.11-2026-05.xlsx\` from the separate **unmerged review snapshot** PR #5; review its licence and SHA first. Do not merge the associated 78 MB planning file unreviewed.
2. Install \`requirements.txt\` (openpyxl). Run \`python conventional/dukes_reconcile.py\` to write review outputs \`conventional/derived/uk_conventional_dukes_review.geojson\` and \`dukes_match_evidence.json\`.
3. Run \`python conventional/dukes_reconcile.py --check\` for **byte-for-byte** comparison; \`python ci/verify.py\`, \`python reproduce.py check\`, and \`python -m unittest discover -s tests -v\`. The CI requirements and bare legs also check the original committed inputs with their SHA pins.
4. \`python conventional/dukes_probe.py\` reruns the source inspection for audit; it contacts the published GOV.UK source and fails if its bytes change. It is deliberately NOT a recurring CI dependency.

Real-source preliminary candidate GeoJSON SHA-256: \`d81d35826ad41d5589362d684229dabf1c72fd2706b259e89f1321f256586462\`, generated in the requirements GitHub runner, **not committed/deployed**. Candidate must be reproduced from reviewed snapshot bytes and independently accepted before usage in GridAtlas.

**Proximity is not capacity, feasibility, or a connection offer.** No published planning record establishes construction or a committed site.
