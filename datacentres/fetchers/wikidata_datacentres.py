"""Wikidata items that are instances of data center in the UK.

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: datacentres/sources.json id "wikidata-datacentres" (open: yes, licence: CC0-1.0).
"""

SOURCE_ID = "wikidata-datacentres"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("wikidata-datacentres: fetcher not yet delivered by lane TBC (DC-A1..A6)")
