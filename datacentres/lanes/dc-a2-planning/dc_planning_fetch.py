#!/usr/bin/env python3
"""DC-A2: data-centre planning applications from FULLY OPEN planning sources.

Standard library only. Deterministic: pinned URLs, SHA-256 of every raw
snapshot, sorted output, no wall-clock values inside the GeoJSON.

    python3 -I dc_planning_fetch.py --out OUTDIR [--cafile PEM] [--offline]
    python3 -I dc_planning_fetch.py --out OUTDIR --verify   # re-hash raw/ against manifest

Honesty rules baked in:
  * a keyword hit in a planning description is a CANDIDATE, not a data centre;
  * MW is only filled when the text states it; IT-load MW, grid/connection
    MW (MW or MVA) and on-site generation MW are kept in separate fields;
    an unqualified "NN MW" goes to mw_unqualified and is never assumed to be
    either. Missing MW is null (unknown), never 0;
  * a source that cannot be reached is reported as unreachable, not empty.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

OGL3 = ("OGL-UK-3.0",
        "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/")

# Pinned sources. 'adapter' picks the row parser. 'licence_evidence' says where
# the licence statement was read (see reports/DC-A2.md).
SOURCES = [
    {
        "id": "pdgu-planning-application",
        "title": "planning.data.gov.uk planning-application (England, national aggregate)",
        "url": "https://files.planning.data.gov.uk/dataset/planning-application.csv",
        "format": "csv", "adapter": "pdgu", "licence": OGL3,
        "licence_evidence": "dataset page: 'Licensed under the Open Government Licence v.3.0'; "
                            "specification content/dataset/planning-application.md licence: ogl3",
        "coverage_note": "alpha, environment=staging; page says 'incomplete and not yet ready for use'; "
                         "only 4 LPAs configured (Camden, Adur, Worthing, Doncaster)",
    },
    {
        "id": "pdgu-infrastructure-project",
        "title": "planning.data.gov.uk infrastructure-project (NSIPs, from PINS)",
        "url": "https://files.planning.data.gov.uk/dataset/infrastructure-project.csv",
        "format": "csv", "adapter": "pdgu_nsip", "licence": OGL3,
        "licence_evidence": "specification content/dataset/infrastructure-project.md licence: ogl3; "
                            "config source.csv licence ogl3",
        "coverage_note": "England + Wales NSIPs (Planning Act 2008)",
    },
    {
        "id": "pins-nsip-applications",
        "title": "Planning Inspectorate NSIP applications download",
        "url": "https://national-infrastructure-consenting.planninginspectorate.gov.uk/api/applications-download",
        "format": "csv", "adapter": "pins", "licence": OGL3,
        "licence_evidence": "site footer: 'All content is available under the Open Government Licence v3.0, "
                            "except where otherwise stated'",
        "coverage_note": "all NSIPs incl. data centres directed in under s35 (since Jan 2026)",
    },
    {
        "id": "digital-land-nsip-snapshot",
        "title": "digital-land infrastructure-project-collection nsip.csv (GitHub snapshot)",
        "url": "https://raw.githubusercontent.com/digital-land/infrastructure-project-collection/main/data/nsip.csv",
        "format": "csv", "adapter": "pdgu_nsip", "licence": OGL3,
        "licence_evidence": "derived from PINS (OGL v3); digital-land endpoint retired 2026-10-02 -> STALE",
        "coverage_note": "stale scrape of PINS projects map; superseded by pins-nsip-applications",
    },
    {
        "id": "doncaster-2024",
        "title": "Doncaster 2024 decided applications (digital-land planning-condition-data)",
        "url": "https://raw.githubusercontent.com/digital-land/planning-condition-data/refs/heads/main/data/doncaster-2024-planning-applications.csv",
        "format": "csv", "adapter": "pdgu", "licence": OGL3,
        "licence_evidence": "digital-land config collection/planning-application/source.csv licence ogl3",
        "coverage_note": "one LPA, decided applications in 2024 only, no coordinates",
    },
    {
        "id": "doncaster-2023",
        "title": "Doncaster 2023 decided applications (digital-land planning-condition-data)",
        "url": "https://raw.githubusercontent.com/digital-land/planning-condition-data/refs/heads/main/data/doncaster-conditions-data-2023-planning-application.csv",
        "format": "csv", "adapter": "pdgu", "licence": OGL3,
        "licence_evidence": "digital-land config collection/planning-application/source.csv licence ogl3",
        "coverage_note": "one LPA, decided applications in 2023 only, no coordinates",
    },
    {
        "id": "adur",
        "title": "Adur DC planning applications (LPA open data feed)",
        "url": "https://www.adur-worthing.gov.uk/datasets/planning/adur-planning.csv",
        "format": "csv", "adapter": "lga", "licence": OGL3,
        "licence_evidence": "digital-land config source.csv licence ogl3 (publisher page not re-read: unverified)",
        "coverage_note": "one LPA",
    },
    {
        "id": "worthing",
        "title": "Worthing BC planning applications (LPA open data feed)",
        "url": "https://www.adur-worthing.gov.uk/datasets/planning/worthing-planning.csv",
        "format": "csv", "adapter": "lga", "licence": OGL3,
        "licence_evidence": "digital-land config source.csv licence ogl3 (publisher page not re-read: unverified)",
        "coverage_note": "one LPA",
    },
    {
        "id": "camden",
        "title": "Camden planning applications (Socrata open data)",
        "url": "https://opendata.camden.gov.uk/resource/2eiu-s2cw.csv?$limit=200000&$order=application_number",
        "format": "csv", "adapter": "camden", "licence": OGL3,
        "licence_evidence": "digital-land config source.csv licence ogl3 (publisher page not re-read: unverified)",
        "coverage_note": "one LPA",
    },
]

# Section 35 directions for data centres (gov.uk publications, OGL v3).
# Hand-curated because no machine-readable feed was reachable; no coordinates
# are invented: geometry stays null unless a source gives one.
S35_SEED = [
    {
        "reference": "EN0110030", "name": "SDC M40 Campus (Wapseys Wood)",
        "lpa": "Buckinghamshire", "applicant": "Slough Holdings UK Limited",
        "route": "NSIP s35 direction", "stage_raw": "Pre-application (application expected Jan 2027)",
        "status": "in planning",
        "it_load_mw": 300.0, "grid_connection_mw": None, "facility_power_mw": None,
        "generation_mw": None, "mw_unqualified": None,
        "mw_note": "PINS page: IT load up to c.300MW; on-site gas energy centre c.250-350MW "
                   "generating capacity; gas connection up to c.900MW (gas, not electrical grid)",
        "evidence_url": "https://national-infrastructure-consenting.planninginspectorate.gov.uk/projects/EN0110030",
        "evidence_url_2": "https://www.gov.uk/government/publications/data-centre-campus-wapseys-wood-buckinghamshire-section-35-direction-planning-act-2008",
    },
    {
        "reference": "s35-2026-06-15-questpit", "name": "Data Centre Campus, Ampthill Road, Bedford",
        "lpa": "Central Bedfordshire", "applicant": "QuestPit Limited",
        "route": "NSIP s35 direction (15 Jun 2026)", "stage_raw": "s35 direction granted",
        "status": "in planning", "it_load_mw": 720.0, "grid_connection_mw": 200.0,
        "facility_power_mw": None, "generation_mw": None, "mw_unqualified": None,
        "mw_note": "Request statement s4.4.1 '1GW AI ready data centre (720MW IT load)' (Volterra s3.4: "
                   "'indicative and subject to revision'); s5.3 'an initial connection of 200MW'",
        "evidence_url_2": "https://assets.publishing.service.gov.uk/media/6a2fc930c39255c595b5084f/Request_Statement_on_behalf_of_Questpit_Limited.pdf",
        "evidence_url": "https://www.gov.uk/government/publications/data-centre-campus-ampthill-road-bedford-in-central-bedfordshire-section-35-direction-planning-act-2008",
    },
    {
        "reference": "s35-2026-07-01-cse52", "name": "Ebbsfleet Datacentre Campus, New Barn Road, Dartford",
        "lpa": "Dartford", "applicant": "CSE52 Limited",
        "route": "NSIP s35 direction (1 Jul 2026)", "stage_raw": "s35 direction granted",
        "status": "in planning", "it_load_mw": 240.0, "grid_connection_mw": None,
        "facility_power_mw": 300.0, "generation_mw": None, "mw_unqualified": None,
        "mw_note": "Response to additional information request: 'total power load of up to 300MW', "
                   "'IT load of up to 240MW'; total power load is facility demand, not a grid connection figure",
        "evidence_url_2": "https://assets.publishing.service.gov.uk/media/6a451766e50f952c9b89e2aa/Response_to_additional_information_request.pdf",
        "evidence_url": "https://www.gov.uk/government/publications/data-centre-campus-new-barn-road-dartford-section-35-direction-planning-act-2008",
    },
]

# --------------------------------------------------------------- classify
DC_STRONG = re.compile(
    r"\bdata[\s\-]?cent(?:re|er)s?\b|\bdatacent(?:re|er)s?\b|\bhyper[\s\-]?scale\b|"
    r"\bdata[\s\-]?halls?\b|\bserver[\s\-]?halls?\b|\bdata[\s\-]?processing[\s\-]?(?:facility|centre|center)\b|"
    r"\bco[\s\-]?location[\s\-]?(?:facility|data)\b|\bAI[\s\-]?(?:campus|factory|compute)\b|"
    r"\bdigital[\s\-]?infrastructure[\s\-]?campus\b",
    re.I)
# A strong hit is dropped when the phrase is clearly minor/ancillary.
DC_NEGATIVE = re.compile(
    r"\b(?:comms?|telecom\w*|street|equipment)\s+cabinet|\bdata\s+cent(?:re|er)\s+(?:cabinet|room)\b|"
    r"\bserver\s+room\b|\bmobile\s+(?:phone\s+)?mast\b",
    re.I)
USE_CLASS = re.compile(r"\b(?:use\s+class\s+)?(B8|E\s?\(g\)(?:\s?\((?:i|ii|iii)\))?|B2|sui\s+generis)\b", re.I)

MW_RE = re.compile(r"(\d{1,4}(?:[.,]\d+)?)\s*(MW|MVA|megawatts?)\b", re.I)
IT_CTX = re.compile(r"\bIT\b|\bIT[\s\-]load\b|critical|compute|server", re.I)
GRID_CTX = re.compile(r"grid|connection|import|substation|\bkV\b|MVA|supply", re.I)
GEN_CTX = re.compile(r"generat|energy\s+centre|engines?|turbine|battery|BESS|solar|backup|standby", re.I)


def classify(text):
    t = text or ""
    strong = sorted({m.group(0).lower() for m in DC_STRONG.finditer(t)})
    neg = bool(DC_NEGATIVE.search(t))
    uses = sorted({re.sub(r"\s+", "", m.group(1)).upper() for m in USE_CLASS.finditer(t)})
    if strong and not neg:
        conf = "keyword"
    elif strong and neg:
        conf = "keyword-ancillary"  # reported, excluded from candidate counts
    else:
        conf = None
    return conf, strong, uses


def extract_mw(text):
    """Return (it_load_mw, grid_mw, generation_mw, unqualified_mw, snippets)."""
    out = {"it": [], "grid": [], "gen": [], "unq": []}
    snippets = []
    for m in MW_RE.finditer(text or ""):
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2).upper()
        a, b = max(0, m.start() - 60), min(len(text), m.end() + 40)
        ctx = text[a:b]
        snippets.append(ctx.strip())
        if unit == "MVA":
            out["grid"].append(val)
            continue
        # nearest qualifier wins: first the words just after the figure, then just before
        after = text[m.end():m.end() + 25]
        before = text[max(0, m.start() - 40):m.start()]
        for win in (after, before):
            hit = None
            for key, rx in (("it", IT_CTX), ("gen", GEN_CTX), ("grid", GRID_CTX)):
                mm = rx.search(win)
                if mm and (hit is None or (mm.start() < hit[1] if win is after else mm.start() > hit[1])):
                    hit = (key, mm.start())
            if hit:
                out[hit[0]].append(val)
                break
        else:
            out["unq"].append(val)
    pick = lambda xs: max(xs) if xs else None  # noqa: E731
    return pick(out["it"]), pick(out["grid"]), pick(out["gen"]), pick(out["unq"]), snippets


# --------------------------------------------------------------- status
def map_status(*raw):
    """Map raw status / decision strings to the pipeline vocabulary."""
    s = " | ".join(x for x in raw if x).lower()
    if not s.strip():
        return "unknown"
    if "withdrawn" in s:
        return "withdrawn"
    if re.search(r"refus|reject|dismiss", s):
        return "refused"
    if re.search(r"grant|approv|permit|consent(?!ation)|allowed|conditions|prior approval not required", s):
        return "consented"
    if re.search(r"pending|registered|valid|received|under consideration|in progress|awaiting|"
                 r"pre[\s\-]?app|acceptance|pre[\s\-]?exam|examination|recommendation|"
                 r"current|consultation|appeal lodged", s):
        return "in planning"
    if s.strip() in ("decided", "decision", "determined", "closed", "finalised", "final"):
        return "decided (outcome not stated)"
    return "unknown"


# --------------------------------------------------------------- geometry
WKT_POINT = re.compile(r"POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)", re.I)


def point_from(row):
    for k in ("point", "WKT", "wkt", "geometry", "the_geom"):
        v = row.get(k) or ""
        m = WKT_POINT.search(v)
        if m:
            return [round(float(m.group(1)), 6), round(float(m.group(2)), 6)], "application point"
    lat = row.get("latitude") or row.get("lat") or row.get("Latitude")
    lon = row.get("longitude") or row.get("lon") or row.get("Longitude")
    try:
        if lat and lon:
            return [round(float(lon), 6), round(float(lat), 6)], "application point"
    except ValueError:
        pass
    return None, "none"


# --------------------------------------------------------------- adapters
def g(row, *keys):
    low = {k.lower().strip(): v for k, v in row.items() if k}
    for k in keys:
        v = low.get(k.lower())
        if v:
            return v.strip()
    return ""


def norm_pdgu(row):
    return {
        "reference": g(row, "reference"), "name": g(row, "name"),
        "description": g(row, "description"), "address": g(row, "address-text"),
        "organisation": g(row, "organisation"),
        "status_raw": [g(row, "planning-application-status"), g(row, "planning-decision")],
        "date_received": g(row, "start-date"), "date_decided": g(row, "decision-date"),
        "url": g(row, "documentation-url"), "row": row,
    }


def norm_pdgu_nsip(row):
    return {
        "reference": g(row, "reference"), "name": g(row, "name"),
        "description": g(row, "description", "name"), "address": "",
        "organisation": g(row, "organisation") or "government-organisation:EA39",
        "status_raw": [g(row, "infrastructure-project-decision")],
        "date_received": g(row, "start-date"), "date_decided": g(row, "decision-date"),
        "url": g(row, "documentation-url"), "row": row,
        "type": g(row, "infrastructure-project-type"),
    }


def norm_pins(row):
    return {
        "reference": g(row, "project reference", "reference", "caseReference"),
        "name": g(row, "project name", "name"),
        "description": " ".join(x for x in (g(row, "project name"), g(row, "summary", "description",
                                                                         "project description")) if x),
        "address": g(row, "location", "project location"),
        "organisation": "government-organisation:EA39",
        "status_raw": [g(row, "stage"), g(row, "decision")],
        "date_received": g(row, "date of application"), "date_decided": g(row, "date of decision"),
        "url": "https://national-infrastructure-consenting.planninginspectorate.gov.uk/projects/"
               + g(row, "project reference", "reference"),
        "row": row, "type": g(row, "application type"),
    }


def norm_lga(row):
    return {
        "reference": g(row, "CaseReference"), "name": "",
        "description": g(row, "CaseText"), "address": g(row, "LocationText"),
        "organisation": g(row, "OrganisationLabel", "OrganisationURI"),
        "status_raw": [g(row, "Status"), g(row, "Decision")],
        "date_received": g(row, "CaseDate"), "date_decided": g(row, "DecisionDate"),
        "url": g(row, "CaseURL"), "row": row,
    }


def norm_camden(row):
    return {
        "reference": g(row, "application_number"), "name": "",
        "description": g(row, "development_description"), "address": g(row, "development_address"),
        "organisation": "local-authority:CMD",
        "status_raw": [g(row, "system_status"), g(row, "decision_type")],
        "date_received": g(row, "registered_date"), "date_decided": g(row, "decision_date"),
        "url": g(row, "full_application"), "row": row,
    }


ADAPTERS = {"pdgu": norm_pdgu, "pdgu_nsip": norm_pdgu_nsip, "pins": norm_pins,
            "lga": norm_lga, "camden": norm_camden}


# --------------------------------------------------------------- fetch
def fetch(url, cafile, timeout=120):
    ctx = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "gridatlas-dc-a2/1 (+https://github.com/Ventusltd/gridatlas/issues/73)"})
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                         urllib.request.ProxyHandler())
    with opener.open(req, timeout=timeout) as r:
        return r.read()


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def run(args):
    out = os.path.abspath(args.out)
    raw_dir = os.path.join(out, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    cafile = args.cafile or os.environ.get("SSL_CERT_FILE") or (
        "/root/.ccr/ca-bundle.crt" if os.path.exists("/root/.ccr/ca-bundle.crt") else None)
    manifest = {"schema": "dc-a2/1", "sources": []}
    features = []
    counts = {}
    for src in SOURCES:
        rawp = os.path.join(raw_dir, src["id"] + "." + src["format"])
        entry = {k: src[k] for k in ("id", "title", "url", "coverage_note", "licence_evidence")}
        entry["licence"], entry["licence_url"] = src["licence"]
        data = None
        if args.offline:
            if os.path.exists(rawp):
                data = open(rawp, "rb").read()
                entry["fetch"] = "offline-cache"
            else:
                entry["fetch"] = "missing-offline"
        else:
            try:
                data = fetch(src["url"], cafile)
                with open(rawp, "wb") as f:
                    f.write(data)
                entry["fetch"] = "ok"
            except urllib.error.HTTPError as e:
                entry["fetch"] = "http-%d" % e.code
            except (urllib.error.URLError, OSError, ssl.SSLError) as e:
                msg = str(getattr(e, "reason", e))
                entry["fetch"] = "unreachable: " + msg[:160]
        c = {"rows": None, "candidates": 0, "ancillary_hits": 0, "use_class_only": 0,
             "with_location": 0, "with_any_mw": 0, "by_status": {}}
        if data is not None:
            entry["sha256"] = sha256(data)
            entry["bytes"] = len(data)
            text = data.decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(text)))
            c["rows"] = len(rows)
            for row in rows:
                n = ADAPTERS[src["adapter"]](row)
                blob = " ".join(x for x in (n["name"], n["description"]) if x)
                conf, kws, uses = classify(blob)
                if conf is None:
                    if uses and re.search(r"\bsui\s+generis\b", blob, re.I) and re.search(r"\bserver|digital|comput", blob, re.I):
                        c["use_class_only"] += 1
                    continue
                if conf == "keyword-ancillary":
                    c["ancillary_hits"] += 1
                    continue
                it, grid, gen, unq, snips = extract_mw(blob)
                coords, precision = point_from(row)
                status = map_status(*n["status_raw"])
                c["candidates"] += 1
                c["by_status"][status] = c["by_status"].get(status, 0) + 1
                c["with_location"] += coords is not None
                c["with_any_mw"] += any(v is not None for v in (it, grid, gen, unq))
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": coords} if coords else None,
                    "properties": {
                        "id": "dc-a2:%s:%s" % (src["id"], n["reference"]),
                        "name": n["name"] or (n["description"][:120] if n["description"] else n["reference"]),
                        "operator": None, "applicant": None,
                        "status": status, "status_raw": " | ".join(x for x in n["status_raw"] if x) or None,
                        "route": "NSIP" if src["adapter"] in ("pins", "pdgu_nsip") else "TCPA",
                        "it_load_mw": it, "grid_connection_mw": grid, "facility_power_mw": None,
                        "onsite_generation_mw": gen, "mw_unqualified": unq,
                        "mw_evidence": snips or None,
                        "date_received": n["date_received"] or None, "date_decided": n["date_decided"] or None,
                        "organisation": n["organisation"] or None, "address": n["address"] or None,
                        "description": n["description"] or None,
                        "keywords": kws, "use_classes": uses, "match": conf,
                        "location_precision": precision,
                        "source": src["id"], "licence": src["licence"][0], "licence_url": src["licence"][1],
                        "evidence_url": n["url"] or src["url"],
                    },
                })
        entry["counts"] = c
        counts[src["id"]] = c
        manifest["sources"].append(entry)

    # s35 seed (not fetched; gov.uk/PINS pages read during research)
    seed_counts = {"candidates": 0, "by_status": {}, "with_location": 0, "with_any_mw": 0}
    for s in S35_SEED:
        seed_counts["candidates"] += 1
        seed_counts["by_status"][s["status"]] = seed_counts["by_status"].get(s["status"], 0) + 1
        seed_counts["with_any_mw"] += any(s[k] is not None for k in ("it_load_mw", "grid_connection_mw", "facility_power_mw"))
        features.append({"type": "Feature", "geometry": None, "properties": {
            "id": "dc-a2:s35:" + s["reference"], "name": s["name"], "operator": None,
            "applicant": s["applicant"], "status": s["status"], "status_raw": s["stage_raw"],
            "route": s["route"], "it_load_mw": s["it_load_mw"], "grid_connection_mw": s["grid_connection_mw"],
            "facility_power_mw": s["facility_power_mw"],
            "onsite_generation_mw": s["generation_mw"], "mw_unqualified": s["mw_unqualified"],
            "mw_evidence": [s["mw_note"]], "date_received": None, "date_decided": None,
            "organisation": s["lpa"], "address": None, "description": None, "keywords": ["data centre"],
            "use_classes": [], "match": "s35-direction", "location_precision": "named site (no coordinates)",
            "source": "s35-seed", "licence": OGL3[0], "licence_url": OGL3[1],
            "evidence_url": s["evidence_url"], "evidence_url_2": s.get("evidence_url_2")}})
    counts["s35-seed"] = seed_counts
    manifest["sources"].append({"id": "s35-seed", "title": "Data-centre s35 directions (hand-curated from gov.uk / PINS pages)",
                                "fetch": "curated", "licence": OGL3[0], "licence_url": OGL3[1],
                                "counts": seed_counts})

    features.sort(key=lambda f: f["properties"]["id"])
    total = {"candidates": 0, "by_status": {}}
    for k, c in counts.items():
        total["candidates"] += c["candidates"]
        for s, v in c["by_status"].items():
            total["by_status"][s] = total["by_status"].get(s, 0) + v
    manifest["totals"] = total
    with open(os.path.join(out, "dc_planning.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, indent=1, sort_keys=True)
        f.write("\n")
    with open(os.path.join(out, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
        f.write("\n")
    return manifest


def verify(args):
    out = os.path.abspath(args.out)
    m = json.load(open(os.path.join(out, "manifest.json")))
    bad = 0
    for s in m["sources"]:
        if "sha256" not in s:
            continue
        p = os.path.join(out, "raw", s["id"] + ".csv")
        ok = os.path.exists(p) and sha256(open(p, "rb").read()) == s["sha256"]
        print(("OK  " if ok else "BAD ") + s["id"])
        bad += not ok
    return bad


def selftest():
    assert classify("Erection of a data centre (Use Class B8) with substation")[0] == "keyword"
    assert classify("Installation of telecoms cabinet to serve data centre")[0] == "keyword-ancillary"
    assert classify("Erection of 3 dwellings")[0] is None
    it, grid, gen, unq, _ = extract_mw("data centre with 90MW IT load and a 132kV substation of 150 MVA")
    assert it == 90.0 and grid == 150.0 and gen is None, (it, grid, gen)
    it, grid, gen, unq, _ = extract_mw("hyperscale data centre of 40 MW")
    assert unq == 40.0 and it is None and grid is None
    it, grid, gen, unq, _ = extract_mw("data centre with 20MW of standby generators")
    assert gen == 20.0
    assert map_status("Decided", "Application Permitted") == "consented"
    assert map_status("Withdrawn") == "withdrawn"
    assert map_status("Refused") == "refused"
    assert map_status("Pre Application") == "in planning"
    assert map_status("Decided") == "decided (outcome not stated)"
    assert map_status("") == "unknown"
    print("selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--cafile")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit(0)
    if a.verify:
        sys.exit(1 if verify(a) else 0)
    m = run(a)
    for s in m["sources"]:
        c = s["counts"]
        print("%-28s %-40s rows=%-7s cand=%-4s %s" % (s["id"], s["fetch"][:40], c.get("rows"),
                                                     c["candidates"], json.dumps(c["by_status"], sort_keys=True)))
    print("TOTAL", json.dumps(m["totals"], sort_keys=True))
