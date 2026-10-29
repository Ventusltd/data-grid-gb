"""OpenStreetMap power=plant with a conventional plant:source.

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: conventional/sources.json id "osm-power-plants" (open: yes, licence: ODbL-1.0).
"""

SOURCE_ID = "osm-power-plants"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("osm-power-plants: fetcher not yet delivered by lane TBC (DC-A1..A6)")
