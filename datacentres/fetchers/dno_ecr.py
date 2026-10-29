"""DNO embedded capacity registers (demand >= 1 MW where published).

PLACEHOLDER. Lane TBC (DC-A1..A6) owns this source; its fetcher replaces this file when it
lands, keeping the contract in assetpipe/pipeline.py (FETCHER_CONTRACT). Until then the
pipeline skips it and never fails because of it.

Registry entry: datacentres/sources.json id "dno-ecr" (open: unverified, licence: unverified: varies by DNO).
"""

SOURCE_ID = "dno-ecr"
LANE = "TBC (DC-A1..A6)"
STATUS = "placeholder"


def raw_requests():
    return []


def normalise(raw):
    raise NotImplementedError("dno-ecr: fetcher not yet delivered by lane TBC (DC-A1..A6)")
