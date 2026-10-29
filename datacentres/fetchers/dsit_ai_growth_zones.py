"""DSIT AI Growth Zones announcements.

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: datacentres/sources.json id "dsit-ai-growth-zones" (open: unverified, licence: unverified: OGL-3.0 for GOV.UK text unless stated otherwise).
"""

SOURCE_ID = "dsit-ai-growth-zones"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("dsit-ai-growth-zones: fetcher not yet delivered by lane TBC (DC-A1..A6)")
