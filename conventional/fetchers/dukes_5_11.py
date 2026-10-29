"""DUKES Table 5.11: Power stations in the United Kingdom.

PLACEHOLDER. Lane DC-A6 owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: conventional/sources.json id "dukes-5-11" (open: yes, licence: OGL-3.0).
"""

SOURCE_ID = "dukes-5-11"
LANE = "DC-A6"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("dukes-5-11: fetcher not yet delivered by lane DC-A6")
