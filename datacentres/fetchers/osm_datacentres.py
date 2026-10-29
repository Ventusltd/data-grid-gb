"""OpenStreetMap features tagged as data centres (telecom=data_center, building=data_center and related).

PLACEHOLDER. Lane DC-A4 owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: datacentres/sources.json id "osm-datacentres" (open: yes, licence: ODbL-1.0).
"""

SOURCE_ID = "osm-datacentres"
LANE = "DC-A4"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("osm-datacentres: fetcher not yet delivered by lane DC-A4")
