"""Shared, standard-library-only machinery for the two asset pipelines.

    datacentres/   UK data-centre sites and pipeline entries (GridAtlas issue #73, steps 2-4)
    conventional/  UK conventional generation: gas, coal, oil, nuclear, biomass, EfW, diesel (step 5)

Each domain has the same shape:

    <domain>/sources.json        the source registry: URL, publisher, licence, licence URL,
                                 and whether the source is FULLY OPEN (yes / no / unverified)
    <domain>/fetchers/<id>.py    one module per source (see assetpipe.pipeline.FETCHER_CONTRACT)
    <domain>/raw/<id>/...        raw snapshots exactly as downloaded
    <domain>/raw/MANIFEST.json   url, bytes and sha256 of every raw file (the pin)
    <domain>/normalised/<id>.geojson   one deterministic FeatureCollection per source
    <domain>/normalised/INDEX.json     sha256, count and licence of every normalised output

Nothing in this package needs a third-party module. Spreadsheet sources may use
openpyxl through ``assetpipe.xlsx``, which says clearly when it is missing.
"""

DOMAINS = ("datacentres", "conventional")
