"""planning.data.gov.uk planning applications mentioning data centres.

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: datacentres/sources.json id "planning-data-gov-uk" (open: unverified, licence: unverified: OGL-3.0 stated for the platform; check each dataset's own licence).
"""

SOURCE_ID = "planning-data-gov-uk"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("planning-data-gov-uk: fetcher not yet delivered by lane TBC (DC-A1..A6)")
