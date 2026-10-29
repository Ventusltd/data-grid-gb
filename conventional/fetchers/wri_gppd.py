"""WRI Global Power Plant Database, GB rows (v1.3.0).

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: conventional/sources.json id "wri-gppd" (open: yes, licence: CC-BY-4.0).
"""

SOURCE_ID = "wri-gppd"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("wri-gppd: fetcher not yet delivered by lane TBC (DC-A1..A6)")
