"""DUKES Table 5.11: Power stations in the United Kingdom (DESNZ, OGL v3.0).

SNAPSHOT. The fetch workflow downloads and pins the May 2026 edition. No normaliser
exists yet: the sandbox that built lane DC-A6 could not reach gov.uk, so nobody has
seen this edition's sheet layout. A parser written without seeing it would be a
guess. Once the snapshot is committed, DC-A6 (or a successor) writes ``normalise``
against the pinned bytes and sets STATUS = "ready". Its output then fills the fuel,
technology, status and installed capacity of the 167 "unstated" sites in
conventional/lanes/dc-a6.

Licence: "All content is available under the Open Government Licence v3.0" (GOV.UK
DUKES chapter 5 page, quoted by DC-A6 on 29 Oct 2026).
"""

SOURCE_ID = "dukes-5-11"
LANE = "DC-A6"
STATUS = "snapshot"

# Pinned to one edition. A new DUKES edition is a new URL and a reviewed change here.
URL = "https://assets.publishing.service.gov.uk/media/6a6a365b0ddb7e4831c629f3/DUKES_5.11.xlsx"


def raw_requests():
    return [{"file": "DUKES_5.11-2026-05.xlsx", "url": URL}]


def normalise(raw):
    raise NotImplementedError("dukes-5-11: snapshot only; write the parser against the pinned bytes")
