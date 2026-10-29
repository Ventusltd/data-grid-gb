#!/usr/bin/env python3
"""DC-A3: government-published UK data-centre sites (issue #73, step 3 schema).

Standard library only. Three modes:

  build   write gov_sites.json (and gov_sites.geojson) from the RECORDS below, deterministically
  check   validate gov_sites.json against the #73 step-3 rules (enums, IT vs connection
          kept apart, licence present, citation on an OGL government domain)
  fetch   snapshot every cited source. GOV.UK pages go through the machine-readable
          Content API (https://www.gov.uk/api/content/<path>); other sources are fetched as raw bytes.
          Writes raw/<sha256>.<ext> plus raw/manifest.json with the URL, HTTP status, SHA-256,
          bytes and GOV.UK public_updated_at. With --verify it also reports whether each
          record's quoted strings still appear in the fetched text.

Facts only: every MW, date and status value is what the cited page says, as of the
date in status_as_of. Stated targets go in 'stated_targets' and are NEVER commitments.
Coordinates come from DC-A3, never from the source, unless location_precision == "exact"
(then they come from a grid reference that the source prints).

Usage:  python3 -I gov_sites.py build|check|fetch [--verify]
"""
import hashlib
import json
import math
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, "gov_sites.json")
OUT_GEOJSON = os.path.join(HERE, "gov_sites.geojson")
RAW = os.path.join(HERE, "raw")

OGL3 = {
    "id": "OGL-UK-3.0",
    "url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
}
STATUS = {"operational", "under construction", "consented", "in planning",
          "connection request", "announced", None}
PRECISION = {"exact", "town", "region"}
OGL_DOMAINS = ("www.gov.uk", "assets.publishing.service.gov.uk", "www.gov.scot",
               "www.gov.wales", "businesswales.gov.wales",
               "national-infrastructure-consenting.planninginspectorate.gov.uk",
               "www.find-tender.service.gov.uk")
LICENCE_NOTES = {
    "www.gov.uk": "GOV.UK: 'All content is available under the Open Government Licence v3.0, except where otherwise stated'",
    "assets.publishing.service.gov.uk": "GOV.UK attachment; OGL v3.0 as printed on the document's copyright page where checked, otherwise 'unverified'",
    "www.gov.scot": "gov.scot footer: 'All content is available under the Open Government Licence v3.0, except for graphic assets and where otherwise stated.' https://www.gov.scot/crown-copyright/",
    "www.gov.wales": "gov.wales copyright statement: 'under the terms of the Open Government Licence' linking to OGL v3 https://www.gov.wales/copyright-statement",
    "businesswales.gov.wales": "Welsh Government service; OGL v3 assumed from gov.wales copyright statement: unverified",
    "national-infrastructure-consenting.planninginspectorate.gov.uk": "Planning Inspectorate service; OGL v3: unverified",
    "www.find-tender.service.gov.uk": "Find a Tender (Cabinet Office); OGL v3: unverified",
}


# --- OSGB36 grid reference to WGS84 (OS 'Guide to coordinate systems', Helmert ~ +/-5 m) ---
def osgb_to_wgs84(E, N):
    a, b = 6377563.396, 6356256.909
    F0, lat0, lon0 = 0.9996012717, math.radians(49), math.radians(-2)
    N0, E0 = -100000, 400000
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    lat, M = lat0, 0.0
    while True:
        lat = (N - N0 - M) / (a * F0) + lat
        Ma = (1 + n + 1.25 * n**2 + 1.25 * n**3) * (lat - lat0)
        Mb = (3 * n + 3 * n**2 + 21 / 8 * n**3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (15 / 8 * n**2 + 15 / 8 * n**3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        Md = 35 / 24 * n**3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)
        if abs(N - N0 - M) < 0.00001:
            break
    s, c, t = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * F0 / math.sqrt(1 - e2 * s * s)
    rho = a * F0 * (1 - e2) / (1 - e2 * s * s) ** 1.5
    eta2 = nu / rho - 1
    VII = t / (2 * rho * nu)
    VIII = t / (24 * rho * nu**3) * (5 + 3 * t * t + eta2 - 9 * t * t * eta2)
    IX = t / (720 * rho * nu**5) * (61 + 90 * t * t + 45 * t**4)
    X = 1 / (c * nu)
    XI = 1 / (6 * c * nu**3) * (nu / rho + 2 * t * t)
    XII = 1 / (120 * c * nu**5) * (5 + 28 * t * t + 24 * t**4)
    XIIA = 1 / (5040 * c * nu**7) * (61 + 662 * t * t + 1320 * t**4 + 720 * t**6)
    dE = E - E0
    phi = lat - VII * dE**2 + VIII * dE**4 - IX * dE**6
    lam = lon0 + X * dE - XI * dE**3 + XII * dE**5 - XIIA * dE**7
    # OSGB36 -> cartesian -> Helmert -> WGS84
    s, c = math.sin(phi), math.cos(phi)
    nu = a / math.sqrt(1 - e2 * s * s)
    x, y, z = nu * c * math.cos(lam), nu * c * math.sin(lam), (1 - e2) * nu * s
    tx, ty, tz, sc = 446.448, -125.157, 542.060, -20.4894e-6
    rx, ry, rz = (math.radians(v / 3600) for v in (0.1502, 0.2470, 0.8421))
    x2 = tx + (1 + sc) * x - rz * y + ry * z
    y2 = ty + rz * x + (1 + sc) * y - rx * z
    z2 = tz - ry * x + rx * y + (1 + sc) * z
    a2, b2 = 6378137.000, 6356752.3142
    e22 = 1 - (b2 * b2) / (a2 * a2)
    p = math.hypot(x2, y2)
    phi2 = math.atan2(z2, p * (1 - e22))
    for _ in range(10):
        nu2 = a2 / math.sqrt(1 - e22 * math.sin(phi2) ** 2)
        phi2 = math.atan2(z2 + e22 * nu2 * math.sin(phi2), p)
    return round(math.degrees(phi2), 5), round(math.degrees(math.atan2(y2, x2)), 5)


def mw(value, measure, quote, url, approx=False, qualifier=None):
    d = {"value": value, "measure": measure, "quote": quote, "evidence_url": url}
    if approx:
        d["approximate"] = True
    if qualifier:
        d["qualifier"] = qualifier
    return d


G = "https://www.gov.uk/government/"
A = "https://assets.publishing.service.gov.uk/media/"
U_COVENANT = G + "publications/data-centre-covenant/the-data-centre-covenant"
U_COLLECTION = G + "collections/ai-growth-zones"
U_BLUEPRINT = G + "news/prime-minister-sets-out-blueprint-to-turbocharge-ai"
U_CULHAM_APR25 = G + "news/investors-and-local-authorities-gear-up-as-ai-growth-zone-delivery-gathers-speed"
U_CULHAM_PIN = "https://www.find-tender.service.gov.uk/Notice/005218-2025/PDF"
U_UKAEA = G + "publications/ukaea-strategy/uk-atomic-energy-authority-strategy-2026-to-2030"
U_NE = G + "news/north-east-england-set-for-billions-in-investment-and-thousands-of-jobs-as-uk-and-us-ink-tech-partnership"
U_NW = G + "news/ai-growth-zones-to-create-thousands-of-jobs-and-unlock-up-to-100-billion-in-investment-as-new-site-confirmed-for-north-wales"
U_NW_WG = "https://www.gov.wales/written-statement-wylfa-announced-site-uks-first-small-modular-reactor-smr-nuclear-power-station"
U_SW = G + "news/ai-to-power-national-renewal-as-government-announces-billions-of-additional-investment-and-new-plans-to-boost-uk-businesses-jobs-and-innovation"
U_SW_WG = "https://businesswales.gov.wales/news-and-blog/second-ai-growth-zone-wales-announced"
U_LAN = G + "news/more-than-3400-jobs-and-targeted-support-for-local-communities-to-help-tackle-the-cost-of-living-as-lanarkshire-named-latest-ai-growth-zone"
U_ANNEX = A + "69d8dae9d3e08b8871b66599/compute-evidence-annex-final.pdf"
U_ROADMAP = G + "publications/uk-compute-roadmap/uk-compute-roadmap"
U_ONEYEAR = A + "697a36873c71d838df6bd400/ai_opportunities_action_plan-one-year-on.pdf"
U_WW_PAGE = G + "publications/data-centre-campus-wapseys-wood-buckinghamshire-section-35-direction-planning-act-2008"
U_WW_REQ = A + "69b7ece3ba47c264e6c8cfba/Request_Document_-_SDC_M40_Campus_-_Section_35_Direction.pdf"
U_WW_DL = A + "69b7e853b84f01b2be53a27c/MHCLG_Decision_Letter_Data_Centre_Campus.pdf"
U_WW_PINS = "https://national-infrastructure-consenting.planninginspectorate.gov.uk/projects/EN0110030"
U_QP = A + "6a2fcaea15f2a70fac7e5f54/260615_QuestPit_Data_Centre_Campus_s35_Direction.pdf"
U_QP_REQ = A + "6a2fc930c39255c595b5084f/Request_Statement_on_behalf_of_Questpit_Limited.pdf"
U_AL_PAGE = G + "publications/recovered-appeal-land-off-bedmond-road-abbots-langley-ref-3346061-12-may-2025"
U_AL = A + "6821f977c66deec8488f7f42/Recovered_appeal_-_land_off_Bedmond_Road__Abbots_Langley.pdf"
U_CL = A + "6752d28020bcf083762a6c99/Recovered_appeal_decision_-_Court_Lane_Industrial_Estate_Court_Lane_Iver_SL0_9HL.pdf"
U_WP_PAGE = G + "publications/recovered-appeal-woodlands-park-landfill-site-land-south-of-slough-road-iver-buckinghamshire-ref-3347353-9-july-2025"
U_MF_PAGE = G + "publications/recovered-appeal-land-at-manor-farm-and-land-north-of-wraysbury-reservoir-poyle-road-slough-ref-3366043-10-june-2026"
U_MF = A + "6a292d6edb0592d809052b94/260610_Combined_DL_IR_RtoC_-_Manor_Farm_Slough_-_3366043.pdf"
U_CNI = G + "news/data-centres-to-be-given-massive-boost-and-protections-from-cyber-criminals-and-it-blackouts"
U_FDI = G + "news/tech-secretary-welcomes-foreign-investment-in-uk-data-centres-which-will-spur-economic-growth-and-ai-innovation-in-britain"
U_SCOT_DIR = "https://www.gov.scot/news/data-centre-planning-direction/"

WW_LAT, WW_LON = osgb_to_wgs84(497300, 188500)

# Each record: #73 step-3 fields + provenance. it_load_mw / grid_connection_mw hold ONLY
# figures the source labels as IT load / grid connection. Anything else goes in other_mw.
RECORDS = [
    # ---------------- DSIT AI Growth Zones (5 designated; Covenant: "five AI Growth Zones across England, Scotland and Wales") ----
    dict(id="gov-aigz-culham", name="Culham AI Growth Zone (Culham Campus)", operator="UK Atomic Energy Authority (site owner); no commercial partner named",
         kind="AI Growth Zone", gov_designation="AI Growth Zone (first; announced 13 Jan 2025)", status="announced",
         status_note="Designated AIGZ. UKAEA PIN (Feb 2025) is 'Prior information only'; no partner contract is published. UKAEA strategy lists delivery 'By September 2026' as a target, not a completion.",
         status_as_of="2026-04-28", country="England", place="Culham, Abingdon, Oxfordshire",
         lat=51.6585, lon=-1.2286, location_precision="town",
         it_load_mw=None, grid_connection_mw=None,
         other_mw=[mw(100, "site power available to a planned AI data centre ('steady state, dual resilient power'); basis IT vs connection not stated",
                      "initially deliver approximately 100MW of steady state, dual resilient power", U_CULHAM_PIN, approx=True)],
         stated_targets=["UKAEA PIN: zones scale 'up to 500MW in collaboration with network operators' (a programme-wide target, not Culham-specific)",
                         "UKAEA strategy 2026-2030: 'By September 2026: Deliver the Government's first AI Growth Zone (AIGZ) at Culham'"],
         dates={"announced": "2025-01-13", "aigz_programme_press": "2025-04-30"},
         evidence_url=U_BLUEPRINT, evidence_urls=[U_BLUEPRINT, U_CULHAM_APR25, U_COLLECTION, U_UKAEA, U_CULHAM_PIN],
         quotes=["The first of these will be in Culham, Oxfordshire"],
         publisher="DSIT / Prime Minister's Office; UKAEA"),
    dict(id="gov-aigz-north-east", name="North East AI Growth Zone", operator="Multiple (Blackstone/QTS at Blyth-Cambois; Nscale at Cobalt Park)",
         kind="AI Growth Zone", gov_designation="AI Growth Zone (announced 16 Sep 2025)", status="announced",
         status_note="Zone-level record; component sites follow as separate records.",
         status_as_of="2025-09-16", country="England", place="Blyth (Northumberland) and Cobalt Park (North Tyneside)",
         lat=55.09, lon=-1.52, location_precision="region",
         it_load_mw=None, grid_connection_mw=None,
         other_mw=[mw(1100, "zone-wide power capacity ambition; IT vs connection not stated", "increase its energy capacity to 1.1GW in the next 6 years", U_NE,
                      qualifier="stated ambition over six years; not secured power")],
         stated_targets=["1.1 GW zone capacity within six years (ambition)"],
         dates={"announced": "2025-09-16"}, evidence_url=U_NE, evidence_urls=[U_NE, U_COLLECTION],
         quotes=["1.1GW"], publisher="DSIT"),
    dict(id="gov-ne-blyth-cambois", name="QTS Cambois Data Centre Campus (Blyth)", operator="QTS (Blackstone)",
         kind="data centre campus", gov_designation="component of North East AI Growth Zone", status="announced",
         status_note="gov.uk: Blackstone has committed GBP 10bn and 'work to develop the site is underway'; the Mayor says it is 'due to open in 2028'. No MW is given.",
         status_as_of="2025-09-16", country="England", place="Cambois, Blyth, Northumberland",
         lat=55.155, lon=-1.53, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[],
         stated_targets=["opening 2028 (as quoted from the Mayor)"],
         dates={"announced": "2025-09-16"}, evidence_url=U_NE, evidence_urls=[U_NE, U_FDI], quotes=["Blyth"], publisher="DSIT"),
    dict(id="gov-ne-cobalt-park", name="Cobalt Park Data Centres", operator="Cobalt Park Data Centres; Nscale (GPU deployment); Stargate UK",
         kind="data centre campus", gov_designation="component of North East AI Growth Zone", status="operational",
         status_note="gov.uk release quotes the Mayor that Cobalt Park Data Centres are 'already established'. The Nscale/Stargate UK GPU rollout is planned. No MW is given.",
         status_as_of="2025-09-16", country="England", place="Cobalt Park, Wallsend, North Tyneside",
         lat=55.02, lon=-1.51, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[],
         stated_targets=["Stargate UK phase one: OpenAI to take 'up to 8,000 GPUs', possible growth to ~31,000 GPUs"],
         dates={"announced": "2025-09-16"}, evidence_url=U_NE, evidence_urls=[U_NE], quotes=["Cobalt Park"], publisher="DSIT"),
    dict(id="gov-aigz-north-wales", name="North Wales AI Growth Zone", operator="Consortium incl. Stena Line, Isle of Anglesey County Council, Bangor University; investment partner not yet secured",
         kind="AI Growth Zone", gov_designation="AI Growth Zone (announced 13 Nov 2025)", status="announced",
         status_note="gov.uk: 'Work is underway to secure an investment partner'; construction starts once a deal is confirmed. No MW is given.",
         status_as_of="2025-11-13", country="Wales", place="Straddles the Menai Strait: Prosperity Parc (Anglesey) and Trawsfynydd (Gwynedd)",
         lat=53.0, lon=-4.2, location_precision="region",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2025-11-13"}, evidence_url=U_NW, evidence_urls=[U_NW, U_NW_WG, U_COLLECTION],
         quotes=["Menai Strait", "Trawsfynydd"], publisher="DSIT / Wales Office / No 10; Welsh Government written statement"),
    dict(id="gov-nw-prosperity-parc", name="Prosperity Parc, Anglesey (North Wales AIGZ base)", operator="Stena Line-led consortium",
         kind="AI Growth Zone site", gov_designation="component of North Wales AI Growth Zone", status="announced",
         status_note="Named in the gov.uk release as one base of the zone. Not named in the Welsh Government written statement.",
         status_as_of="2025-11-13", country="Wales", place="Holyhead, Anglesey",
         lat=53.30, lon=-4.60, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2025-11-13"}, evidence_url=U_NW, evidence_urls=[U_NW], quotes=["Prosperity Parc"], publisher="DSIT"),
    dict(id="gov-nw-trawsfynydd", name="Trawsfynydd (North Wales AIGZ base)", operator=None,
         kind="AI Growth Zone site", gov_designation="component of North Wales AI Growth Zone", status="announced",
         status_note="The Welsh Government statement says only that the zone 'has the potential to widen AI investment across the region, including at Trawsfynydd'.",
         status_as_of="2025-11-13", country="Wales", place="Trawsfynydd, Gwynedd",
         lat=52.92, lon=-3.95, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2025-11-13"}, evidence_url=U_NW, evidence_urls=[U_NW, U_NW_WG], quotes=["Trawsfynydd"], publisher="DSIT; Welsh Government"),
    dict(id="gov-aigz-south-wales", name="South Wales AI Growth Zone (M4 corridor, Newport to Bridgend)", operator="Vantage Data Centers and Microsoft named as joining; investor not yet secured",
         kind="AI Growth Zone", gov_designation="AI Growth Zone (announced 20 Nov 2025; fourth announced)", status="announced",
         status_note="gov.uk: 'Work is now underway to secure an investor to help deliver the South Wales Growth Zone site'.",
         status_as_of="2025-11-20", country="Wales", place="M4 corridor from Newport to Bridgend",
         lat=51.55, lon=-3.25, location_precision="region",
         it_load_mw=None, grid_connection_mw=None,
         other_mw=[mw(1000, "potential power; IT vs connection not stated", "has the potential to harness over 1GW by early 2030s", U_SW,
                      qualifier="'over'; potential, not secured")],
         stated_targets=["'over 1GW by early 2030s' (potential)"],
         dates={"announced": "2025-11-20"}, evidence_url=U_SW, evidence_urls=[U_SW, U_SW_WG, U_COLLECTION],
         quotes=["from Newport to Bridgend"], publisher="DSIT / HM Treasury / Wales Office; Business Wales"),
    dict(id="gov-sw-ford-bridgend", name="Former Ford Bridgend Engine Plant (South Wales AIGZ)", operator=None,
         kind="AI Growth Zone site", gov_designation="component of South Wales AI Growth Zone", status="announced",
         status_note="Named in the gov.uk release as included in the zone. No operator or MW is given.",
         status_as_of="2025-11-20", country="Wales", place="Bridgend",
         lat=51.51, lon=-3.55, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2025-11-20"}, evidence_url=U_SW, evidence_urls=[U_SW], quotes=["former Ford Bridgend Engine Plant"], publisher="DSIT"),
    dict(id="gov-aigz-lanarkshire", name="Lanarkshire AI Growth Zone (DataVita, Airdrie)", operator="DataVita (HFD Group); CoreWeave as backer",
         kind="AI Growth Zone", gov_designation="AI Growth Zone (announced 29 Jan 2026)", status="announced",
         status_note="gov.uk: 'AI Growth Zone status is conditional on delivery against agreed milestones'. Investment/jobs figures were 'supplied by DataVita'.",
         status_as_of="2026-02-03", country="Scotland", place="Airdrie, North Lanarkshire",
         lat=55.866, lon=-3.98, location_precision="town",
         it_load_mw=None, grid_connection_mw=None,
         other_mw=[mw(500, "on-site power generation (renewables); not IT load, not grid connection", "generation of more than 500MW of on-site power", U_LAN,
                      qualifier="'more than'; planned 'in the next 4 years'")],
         stated_targets=[">500 MW on-site generation 'in the next 4 years'"],
         dates={"announced": "2026-01-29", "page_updated": "2026-02-03"}, evidence_url=U_LAN, evidence_urls=[U_LAN, U_COLLECTION],
         quotes=["Airdrie"], publisher="DSIT / Scotland Office"),

    # ---------------- Planning Act 2008 s35 directions (MHCLG) ----------------
    dict(id="gov-s35-wapseys-wood", name="SDC M40 Campus (Wapseys Wood)", operator="Slough Holdings UK Limited (SDC Capital Partners)",
         kind="data centre campus + energy centre", gov_designation="s35 direction: to be treated as NSIP (MHCLG, 16 Mar 2026)", status="in planning",
         status_note="PINS: 'This project is at the pre-application stage'; application 'expected to be submitted January 2027'.",
         status_as_of="2026-10-29", country="England", place="Wapseys Wood landfill site, north of the M40, south of the A40, Buckinghamshire",
         lat=WW_LAT, lon=WW_LON, location_precision="exact",
         location_note="OSGB 497300E 188500N as printed in the request (it locates building 'DC 02', not the campus boundary); converted to WGS84 by DC-A3 (Helmert, about 5 m).",
         it_load_mw=mw(300, "IT load", "designed for an IT load of up to c.300MW across the three buildings (c.100 MW each)", U_WW_REQ, approx=True, qualifier="up to"),
         grid_connection_mw=None,
         other_mw=[mw(350, "energy centre generating capacity (upper bound of the range)", "a generating capacity of c.270MW to 350MW", U_WW_REQ, approx=True, qualifier="range 270-350 (request); PINS page says 250-350"),
                   mw(900, "GAS connection capacity (not electrical)", "indicative gas connection capacity of up to c.900MW", U_WW_REQ, approx=True, qualifier="up to")],
         stated_targets=["DCO application expected January 2027 (PINS)"],
         conflicts=["Energy-centre generating capacity: the request says c.270-350 MW, the PINS project page says approximately 250-350 MW."],
         dates={"s35_request": "2026-01-21", "s35_direction": "2026-03-16"}, evidence_url=U_WW_PAGE, evidence_urls=[U_WW_PAGE, U_WW_DL, U_WW_REQ, U_WW_PINS],
         quotes=["Wapseys Wood"], publisher="MHCLG; Planning Inspectorate"),
    dict(id="gov-s35-questpit-bedford", name="QuestPit data centre campus, Ampthill Road", operator="Questpit Limited",
         kind="data centre campus", gov_designation="s35 direction: development consent required (MHCLG, 15 Jun 2026)", status="in planning",
         status_note="A direction is given; no DCO application found. Source gives no connection figure.",
         status_as_of="2026-06-15", country="England", place="Ampthill Road, Bedford, Central Bedfordshire",
         lat=52.09, lon=-0.49, location_precision="town",
         it_load_mw=mw(720, "IT load", "a proposed IT load of up to c.720MW across the four buildings", U_QP, approx=True, qualifier="up to"),
         grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"s35_request": "2026-04-24", "s35_direction": "2026-06-15"}, evidence_url=U_QP, evidence_urls=[U_QP, U_QP_REQ],
         quotes=["AMPTHILL ROAD, BEDFORD"], publisher="MHCLG"),

    # ---------------- Recovered appeals decided by the Secretary of State (MHCLG) ----------------
    dict(id="gov-appeal-abbots-langley", name="Land off Bedmond Road, Abbots Langley", operator="Greystoke Land Ltd (appellant)",
         kind="data centre", gov_designation="recovered appeal ALLOWED; outline permission (APP/P1940/W/24/3346061)", status="consented",
         status_note="Outline permission for 'a data centre of up to 84,000 sqm (GEA)'.",
         status_as_of="2025-05-12", country="England", place="Abbots Langley, Three Rivers, Hertfordshire",
         lat=51.709, lon=-0.41, location_precision="town",
         it_load_mw=None, grid_connection_mw=None,
         other_mw=[mw(96, "unspecified ('capacity'; IT vs connection not stated; Inspector's summary of the Council's case)", "96 MW of capacity", U_AL)],
         stated_targets=[], dates={"decision": "2025-05-12"}, evidence_url=U_AL_PAGE, evidence_urls=[U_AL_PAGE, U_AL],
         quotes=["Bedmond Road"], publisher="MHCLG"),
    dict(id="gov-appeal-court-lane-iver", name="Court Lane Industrial Estate, Iver (SL0 9HL)", operator="Affinius Capital LLC (appellant)",
         kind="data centre", gov_designation="recovered appeal ALLOWED; outline permission", status="consented",
         status_note="Outline permission for a data centre 'of up to 65,000sqm (GEA)'.",
         status_as_of="2024-12-06", country="England", place="Iver, Buckinghamshire",
         lat=51.515, lon=-0.505, location_precision="town",
         it_load_mw=None,
         grid_connection_mw=mw(140, "grid connection (reserved)", "The proposed development has a 140MW grid connection reserved.", U_CL),
         other_mw=[mw(103, "contribution to data-centre need (Inspector's report); basis not stated", "the 103 MW proposed here", U_CL)],
         stated_targets=[], dates={"decision": "2024-12-06"}, evidence_url=U_CL, evidence_urls=[U_CL],
         quotes=["COURT LANE INDUSTRIAL ESTATE"], publisher="MHCLG"),
    dict(id="gov-appeal-woodlands-park-iver", name="Woodlands Park landfill site, land south of Slough Road, Iver", operator="Greystoke Land Ltd (per secondary reports; not confirmed from gov.uk)",
         kind="data centre", gov_designation="recovered appeal decision 9 Jul 2025 (approval; see conflict)", status=None,
         status_note="gov.uk publishes the 9 Jul 2025 decision ('up to 72,000 sqm (GEA) delivered across 2 buildings'). Secondary press (The Register, 23 Jan 2026) reports that the government conceded the approval should be quashed after a legal challenge. Current status UNKNOWN.",
         status_as_of="2026-10-29", country="England", place="Iver, Buckinghamshire",
         lat=51.52, lon=-0.52, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         conflicts=["gov.uk decision (approval, 9 Jul 2025) vs reported concession to quash (Jan 2026, non-OGL secondary source). The gov.uk page metadata updated 2026-10-01; re-check."],
         dates={"decision": "2025-07-09"}, evidence_url=U_WP_PAGE, evidence_urls=[U_WP_PAGE],
         quotes=["72,000 sqm"], publisher="MHCLG"),
    dict(id="gov-appeal-manor-farm-slough", name="Land at Manor Farm and north of Wraysbury Reservoir, Poyle Road, Slough", operator="Manor Farm Propco Limited (appellant)",
         kind="data centre + BESS", gov_designation="recovered appeal ALLOWED; permission granted (ref 3366043)", status="consented",
         status_note="Data centre (Use Class B8) plus BESS; GIA about 36,368 sqm (Inspector's report).",
         status_as_of="2026-06-10", country="England", place="Poyle, Slough (SL3 0AA)",
         lat=51.474, lon=-0.515, location_precision="town",
         it_load_mw=mw(72, "IT load (design IT capacity)", "deliver an IT capacity of around 72 MW", U_MF, approx=True),
         grid_connection_mw=None,
         other_mw=[mw(100, "battery energy storage capacity (BESS); not data-centre load", "a 100 MW capacity system to store electricity", U_MF)],
         stated_targets=[], dates={"decision": "2026-06-10"}, evidence_url=U_MF_PAGE, evidence_urls=[U_MF_PAGE, U_MF],
         quotes=["Manor Farm"], publisher="MHCLG"),

    # ---------------- DSIT announcements naming specific sites ----------------
    dict(id="gov-dsit-dc01uk-hertsmere", name="DC01UK data centre, Hertsmere", operator="DC01UK",
         kind="data centre", gov_designation="named in CNI designation release", status="in planning",
         status_note="As of 12 Sep 2024: 'plans have been submitted to Hertsmere Borough Council'. Later status was not checked against an OGL source.",
         status_as_of="2024-09-12", country="England", place="Hertsmere, Hertfordshire",
         lat=51.69, lon=-0.22, location_precision="region",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         conflicts=["Investment given as 'GBP 3.75 billion' and 'nearly GBP 4 billion' in the same release."],
         dates={"announced": "2024-09-12"}, evidence_url=U_CNI, evidence_urls=[U_CNI], quotes=["DC01UK"], publisher="DSIT"),
    dict(id="gov-dsit-cloudhq-didcot", name="CloudHQ data centre, Didcot", operator="CloudHQ",
         kind="data centre", gov_designation=None, status="consented",
         status_note="As of 14 Oct 2024: 'currently in development' with planning permission secured. No MW is given.",
         status_as_of="2024-10-14", country="England", place="Didcot, Oxfordshire",
         lat=51.61, lon=-1.24, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2024-10-14"}, evidence_url=U_FDI, evidence_urls=[U_FDI], quotes=["Didcot"], publisher="DSIT"),
    dict(id="gov-dsit-nscale-loughton", name="Nscale sovereign AI data centre, Loughton", operator="Nscale",
         kind="data centre", gov_designation=None, status="announced",
         status_note="As of 13 Jan 2025: 'signed a contract to build the largest UK sovereign AI data centre in Loughton, Essex by 2026'. No MW is given.",
         status_as_of="2025-01-13", country="England", place="Loughton, Essex",
         lat=51.65, lon=0.07, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=["build 'by 2026' (stated)"],
         dates={"announced": "2025-01-13"}, evidence_url=U_BLUEPRINT, evidence_urls=[U_BLUEPRINT], quotes=["Loughton"], publisher="DSIT / No 10"),
    dict(id="gov-dsit-equinix-herts", name="Equinix high-performance data centre campus, Hertfordshire", operator="Equinix",
         kind="data centre campus", gov_designation=None, status="announced",
         status_note="gov.uk (20 Nov 2025) refers to 'Equinix's recent GBP 4 billion commitment'. No site or MW is given.",
         status_as_of="2025-11-20", country="England", place="Hertfordshire",
         lat=51.80, lon=-0.20, location_precision="region",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2025-11-20"}, evidence_url=U_SW, evidence_urls=[U_SW], quotes=["Hertfordshire"], publisher="DSIT"),
    dict(id="gov-dsit-google-waltham-cross", name="Google data centre, Waltham Cross", operator="Google",
         kind="data centre", gov_designation=None, status="operational",
         status_note="Described in the gov.uk release (13 Nov 2025) as recently opened, in a quote from Google. No MW is given.",
         status_as_of="2025-11-13", country="England", place="Waltham Cross, Hertfordshire",
         lat=51.69, lon=-0.03, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"announced": "2025-11-13"}, evidence_url=U_NW, evidence_urls=[U_NW], quotes=["Waltham Cross"], publisher="DSIT"),

    # ---------------- Compute Roadmap / evidence annex / One Year On (public compute) ----------------
    dict(id="gov-compute-isambard-ai", name="Isambard-AI supercomputer (AIRR)", operator="University of Bristol",
         kind="public AI supercomputer", gov_designation="AI Research Resource", status="operational",
         status_note="Launched July 2025 (One Year On). Evidence annex: 'Isambard-AI and Dawn currently provide less than 0.01GW of sustained compute' (combined; IT-load basis).",
         status_as_of="2026-01-29", country="England", place="Bristol",
         lat=51.45, lon=-2.59, location_precision="town",
         it_load_mw=None, grid_connection_mw=None,
         other_mw=[mw(10, "COMBINED Isambard-AI + Dawn sustained compute, IT-load basis; upper bound ('less than'); not per-site", "less than 0.01GW of sustained compute", U_ANNEX, qualifier="< (upper bound, combined)")],
         stated_targets=[], dates={"launched": "2025-07"}, evidence_url=U_ONEYEAR, evidence_urls=[U_ONEYEAR, U_ANNEX, U_ROADMAP],
         quotes=["Isambard-AI"], publisher="DSIT"),
    dict(id="gov-compute-dawn", name="DAWN supercomputer (AIRR)", operator="University of Cambridge",
         kind="public AI supercomputer", gov_designation="AI Research Resource", status="operational",
         status_note="One Year On: capacity at Cambridge to increase 'sixfold by Spring 2026' (target). See the Isambard-AI note for the combined <0.01 GW.",
         status_as_of="2026-01-29", country="England", place="Cambridge",
         lat=52.21, lon=0.12, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[],
         stated_targets=["sixfold capacity increase by Spring 2026"], dates={}, evidence_url=U_ONEYEAR, evidence_urls=[U_ONEYEAR, U_ANNEX, U_ROADMAP],
         quotes=["DAWN"], publisher="DSIT"),
    dict(id="gov-compute-edinburgh-national", name="Next national supercomputer, Edinburgh (EPCC)", operator="Edinburgh Parallel Computing Centre, University of Edinburgh",
         kind="public supercomputer", gov_designation="EPCC designated first National Supercomputing Centre", status="announced",
         status_note="Up to GBP 750m of funding; replaces ARCHER2 (operational). Planned for 2027. No MW is given.",
         status_as_of="2026-04-23", country="Scotland", place="Edinburgh",
         lat=55.95, lon=-3.19, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=["early 2027 (Compute Roadmap)"],
         dates={}, evidence_url=U_ROADMAP, evidence_urls=[U_ROADMAP, U_ONEYEAR], quotes=["Edinburgh"], publisher="DSIT / UKRI"),

    # ---------------- Scottish Government ----------------
    dict(id="scot-eia-wester-hermiston", name="Wester Hermiston Data Centre Campus", operator=None,
         kind="data centre campus", gov_designation="Scottish Ministers' EIA direction (Aug 2026)", status="in planning",
         status_note="EIA required by Ministerial direction. No MW is given.", status_as_of="2026-08-17", country="Scotland", place="Wester Hermiston, Edinburgh",
         lat=55.92, lon=-3.33, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"direction_news": "2026-08-17"}, evidence_url=U_SCOT_DIR, evidence_urls=[U_SCOT_DIR, "https://www.dpea.scotland.gov.uk/CaseDetails.aspx?ID=128694"],
         quotes=["Wester Hermiston"], publisher="Scottish Government"),
    dict(id="scot-eia-auchtertool", name="Auchtertool data centre", operator=None,
         kind="data centre", gov_designation="Scottish Ministers' EIA direction (Aug 2026)", status="in planning",
         status_note="EIA required by Ministerial direction. No MW is given.", status_as_of="2026-08-17", country="Scotland", place="Auchtertool, Fife",
         lat=56.13, lon=-3.27, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"direction_news": "2026-08-17"}, evidence_url=U_SCOT_DIR, evidence_urls=[U_SCOT_DIR, "https://www.dpea.scotland.gov.uk/CaseDetails.aspx?ID=128778"],
         quotes=["Auchtertool"], publisher="Scottish Government"),
    dict(id="scot-recall-redheughs", name="Redheughs Avenue green data centre", operator=None,
         kind="data centre", gov_designation="planning appeal recalled by Scottish Ministers (Aug 2026)", status="in planning",
         status_note="Ministers, not a Reporter, will decide the appeal. No MW is given.", status_as_of="2026-08-17", country="Scotland", place="Redheughs Avenue, Edinburgh",
         lat=55.93, lon=-3.30, location_precision="town",
         it_load_mw=None, grid_connection_mw=None, other_mw=[], stated_targets=[],
         dates={"direction_news": "2026-08-17"}, evidence_url=U_SCOT_DIR, evidence_urls=[U_SCOT_DIR, "https://www.dpea.scotland.gov.uk/CaseDetails.aspx?ID=128582"],
         quotes=["Redheughs Avenue"], publisher="Scottish Government"),
]

CONTEXT = {
    "data_centre_covenant": {
        "url": U_COVENANT, "publisher": "HM Treasury", "published": "2026-10-28", "annexes": "none (publication page lists one HTML document)",
        "named_sites": "none", "facts": [
            "'6 GW of AI data centre capacity by 2030': 'capacity' is not defined as IT load or grid connection.",
            "'five AI Growth Zones across England, Scotland and Wales' (matches the 5 AIGZ records here).",
            "New facilities designed for 'a Power Usage Effectiveness of 1.3 or better'."]},
    "compute_evidence_annex": {
        "url": U_ANNEX, "publisher": "DSIT", "licence": "OGL v3.0 (printed p.2)", "named_sites": "no individual data-centre sites with capacity; Isambard-AI, Dawn, ARCHER2 named",
        "facts": ["Units: 'measured here in gigawatts (GW) of IT load' (p.9).",
                  "'Around 1.8GW of UK's IT load (power capacity) is currently used to power around 330 data centres' (p.12).",
                  "AIGZs 'capable of delivering at least 6GW of AI-ready capacity by 2030' (p.8): a target, not a commitment."]},
    "compute_roadmap": {"url": U_ROADMAP, "publisher": "DSIT/UKRI", "published": "2025-07-17", "updated": "2026-04-23",
                        "facts": ["Each AIGZ core site to serve 'at least 500MW of demand by 2030', at least one 'scaling to more than 1GW by 2030' (targets)."]},
    "critical_national_infrastructure": {"url": U_CNI, "published": "2024-09-12", "facts": [
        "'Data centres powering the economy will be designated as Critical National Infrastructure': a sector designation. No list of designated sites is published (security-sensitive); none can be recorded."]},
    "aigz_collection": {"url": U_COLLECTION, "facts": ["Announcements: Oxfordshire 30 Apr 2025 (Culham first named 13 Jan 2025), North East 16 Sep 2025, North Wales 13 Nov 2025, South Wales 20 Nov 2025, Scotland 29 Jan 2026. One Year On (Jan 2026): 'five AI Growth Zones across Great Britain, including 2 in Wales and one in Scotland'."]},
    "desnz_strategic_demand_consultation": {"url": A + "69b2aa5bc8010d37b34e0070/accelerating-electricity-network-connections-for-strategic-demand.pdf", "published": "2026-03-11",
        "facts": ["'~140 data centres in the queue at transmission alone, representing approximately 50GW': connection requests, not sites. No sites are named, and Annex D examples are hypothetical."]},
    "scotland_green_dc_site_studies": {"urls": ["https://digitalconnectivity.campaign.gov.scot/publications/2023/06/short-list-of-datacentre-site-development-report",
                                                "https://digitalconnectivity.campaign.gov.scot/publications/2023/11/long-list-of-datacentre-site-development-report"],
        "licence": "OGL v3.0 (page footer)", "facts": ["Government-commissioned candidate-site studies (150-page PDF). NOT ingested: candidate sites are not projects. PDF not reachable from this sandbox."]},
}

EXCLUDED = [
    {"what": "Investments with no stated location (CyrusOne GBP 2.5bn, ServiceNow, CoreWeave GBP 750m, AWS GBP 8bn, Zoom, Groq 'London')", "url": U_FDI,
     "reason": "No site, or only a city; not mappable as a site. Groq (London) and AI Pathfinder (Northamptonshire, GBP 150m GPU deployment) could be added at region precision if wanted."},
    {"what": "Vantage 'one of Europe's largest data centre campuses in Wales'", "url": U_BLUEPRINT, "reason": "gov.uk names no site; likely overlaps the South Wales AIGZ (see that record's operator)."},
    {"what": "North East Combined Authority / council pages (Newcastle, Northumberland, North Lincolnshire, York & N Yorks, Havering LDO, Rushcliffe Ratcliffe-on-Soar LDO, Hertsmere)",
     "reason": "Footer '(c) North East Mayoral Strategic Authority 2026', with no OGL statement; council licences are not stated. Not fully open: excluded under the owner's data rule. All NECA facts are duplicated on gov.uk."},
    {"what": "West London Technology Park, Iver (150 MW): appeal dismissed by SoS (Gove era)", "reason": "Only a secondary source (DCD) was found; no gov.uk citation was retrieved. Dismissed, so it is not pipeline."},
]


def build():
    recs = sorted(RECORDS, key=lambda r: r["id"])
    for r in recs:
        host = urllib.parse.urlparse(r["evidence_url"]).hostname
        r["source"] = r.pop("publisher")
        r["licence"] = dict(OGL3, note=LICENCE_NOTES.get(host, "unverified"))
        r["coordinates_source"] = ("grid reference printed in source" if r["location_precision"] == "exact"
                                   else "DC-A3 approximate centroid of the named place; NOT from the source")
        r.setdefault("conflicts", [])
    doc = {
        "schema": "gridatlas#73 step-3 data-centre pipeline record (DC-A3 lane)",
        "generated": "2026-10-29",
        "lane": "DC-A3",
        "rules": ["Facts only, as stated by the cited page on status_as_of.",
                  "it_load_mw and grid_connection_mw hold ONLY figures the source labels as such; every other MW figure is in other_mw with its measure.",
                  "null means unknown, never zero. A pipeline entry is not a commitment. stated_targets are not commitments.",
                  "Proximity is not connection, capacity or feasibility."],
        "status_enum": sorted(s for s in STATUS if s) + [None],
        "location_precision_enum": sorted(PRECISION),
        "records": recs,
        "context": CONTEXT,
        "excluded": EXCLUDED,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False, sort_keys=False)
        f.write("\n")
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
              "properties": {k: v for k, v in r.items() if k not in ("lat", "lon")}} for r in recs]
    with open(OUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {len(recs)} records -> {OUT_JSON}; sha256 {sha(OUT_JSON)}")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def check():
    doc = json.load(open(OUT_JSON, encoding="utf-8"))
    errs, ids = [], set()
    for r in doc["records"]:
        i = r["id"]
        if i in ids: errs.append(f"{i}: duplicate id")
        ids.add(i)
        for k in ("name", "status", "it_load_mw", "grid_connection_mw", "dates", "source", "licence", "evidence_url", "location_precision", "lat", "lon"):
            if k not in r: errs.append(f"{i}: missing {k}")
        if r["status"] not in STATUS: errs.append(f"{i}: bad status {r['status']}")
        if r["location_precision"] not in PRECISION: errs.append(f"{i}: bad precision")
        if not (49.8 < r["lat"] < 60.9 and -8.7 < r["lon"] < 1.8): errs.append(f"{i}: outside UK bbox")
        for u in [r["evidence_url"]] + r.get("evidence_urls", []):
            h = urllib.parse.urlparse(u).hostname or ""
            if not (h in OGL_DOMAINS or h.endswith(".scotland.gov.uk")): errs.append(f"{i}: non-government citation {u}")
        if not r["evidence_url"].startswith(("https://www.gov.uk/", "https://assets.publishing.service.gov.uk/", "https://www.gov.scot/", "https://www.gov.wales/", "https://businesswales.gov.wales/")):
            errs.append(f"{i}: primary evidence_url is not gov.uk/gov.scot/gov.wales")
        for k, label in (("it_load_mw", "IT load"), ("grid_connection_mw", "grid connection")):
            v = r[k]
            if v is not None:
                if label not in v["measure"]: errs.append(f"{i}: {k} measure is '{v['measure']}', not labelled {label}")
                if not isinstance(v["value"], (int, float)) or v["value"] <= 0: errs.append(f"{i}: {k} non-positive")
        for o in r.get("other_mw", []):
            m = o["measure"].lower()
            if m.startswith("it load") or m.startswith("grid connection"):
                errs.append(f"{i}: other_mw labelled as IT/connection; move it to the dedicated field")
        if r["it_load_mw"] and r["grid_connection_mw"] and r["it_load_mw"]["value"] == r["grid_connection_mw"]["value"]:
            errs.append(f"{i}: IT == connection; check for conflation")
        if r["location_precision"] == "exact" and "grid reference" not in r.get("coordinates_source", ""):
            errs.append(f"{i}: exact precision without a source grid reference")
    print(f"{len(doc['records'])} records; {len(errs)} errors")
    for e in errs: print("  ERR", e)
    return 1 if errs else 0


def all_urls(doc):
    us = set()
    for r in doc["records"]:
        us.add(r["evidence_url"]); us.update(r.get("evidence_urls", []))
        for k in ("it_load_mw", "grid_connection_mw"):
            if r[k]: us.add(r[k]["evidence_url"])
        for o in r.get("other_mw", []): us.add(o["evidence_url"])
    return sorted(us)


def fetch(verify=False):
    doc = json.load(open(OUT_JSON, encoding="utf-8"))
    os.makedirs(RAW, exist_ok=True)
    manifest, texts = [], {}
    for u in all_urls(doc):
        p = urllib.parse.urlparse(u)
        api = f"https://www.gov.uk/api/content{p.path}" if p.hostname == "www.gov.uk" else u
        ent = {"url": u, "fetched_url": api}
        try:
            req = urllib.request.Request(api, headers={"User-Agent": "gridatlas-dc-a3/1 (+https://github.com/Ventusltd/gridatlas/issues/73)"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read(); ent["http"] = resp.status; ctype = resp.headers.get("Content-Type", "")
            digest = hashlib.sha256(body).hexdigest()
            ext = "json" if "json" in ctype else ("pdf" if "pdf" in ctype or u.endswith(".pdf") else "html")
            with open(os.path.join(RAW, f"{digest}.{ext}"), "wb") as f: f.write(body)
            ent.update(sha256=digest, bytes=len(body), content_type=ctype)
            if ext == "json":
                j = json.loads(body)
                ent.update(public_updated_at=j.get("public_updated_at"), first_published_at=j.get("first_published_at"))
                texts[u] = json.dumps(j.get("details", {}), ensure_ascii=False)
            elif ext == "html":
                texts[u] = body.decode("utf-8", "replace")
        except Exception as e:  # record failure; never invent
            ent["error"] = f"{type(e).__name__}: {e}"
        manifest.append(ent)
        print(ent.get("http", "ERR"), ent.get("sha256", ent.get("error"))[:64], u)
    with open(os.path.join(RAW, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1); f.write("\n")
    if verify:
        miss = 0
        for r in doc["records"]:
            for q in r.get("quotes", []):
                t = texts.get(r["evidence_url"])
                if t is None: print(f"  SKIP {r['id']}: primary is PDF or unfetched"); break
                if q.lower() not in t.lower(): miss += 1; print(f"  MISS {r['id']}: {q!r}")
        print(f"quote verification: {miss} missing")
    return 0 if all("sha256" in m for m in manifest) else 2


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "build": build(); sys.exit(check())
    if cmd == "check": sys.exit(check())
    if cmd == "fetch": sys.exit(fetch("--verify" in sys.argv))
    print(__doc__); sys.exit(64)
