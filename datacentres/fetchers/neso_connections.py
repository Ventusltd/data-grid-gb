"""NESO Transmission Entry Capacity register and connection queue data (demand rows).

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: datacentres/sources.json id "neso-connections" (open: unverified, licence: unverified: NESO Open Data Licence).
"""

SOURCE_ID = "neso-connections"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("neso-connections: fetcher not yet delivered by lane TBC (DC-A1..A6)")
