#!/usr/bin/env python3
"""Ingest lane outputs (hardening/evidence/DC-A1..A5/*.json|*.geojson), normalise to
dc_pipeline.schema.json, validate, merge, and write uk_datacentre_pipeline.geojson.

Lane formats are not fixed, so the adapter is tolerant but conservative:
  * accepts a FeatureCollection, a list of records, or {records|sites|items|features: [...]};
  * reads licence / url / dataset from the record, else from file-level metadata;
  * a record with no fully-open licence, no URL, or no name is REJECTED (logged), never guessed;
  * a bare "mw"/"capacity_mw" with no IT-vs-grid label goes to unclassified_mw, never to either;
  * unknown status -> 'unknown'; unknown MW -> null (never 0).

Usage: python3 dcingest.py [--lanes DIR ...] [--out FILE]
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dcmerge  # noqa: E402
import dcschema  # noqa: E402

EVIDENCE = os.path.dirname(HERE)
DEFAULT_LANES = [os.path.join(EVIDENCE, "DC-A%d" % i) for i in range(1, 6)]
SKIP_DIRS = {"raw", "wt", "worktree", "node_modules", ".git", "fixtures", "tmp"}
DEFAULT_OUT = os.path.join(HERE, "uk_datacentre_pipeline.geojson")

LICENCES = {
    "ogl": "OGL-UK-3.0", "ogl-uk-3.0": "OGL-UK-3.0", "ogl v3": "OGL-UK-3.0", "ogl3": "OGL-UK-3.0",
    "open government licence": "OGL-UK-3.0", "open government licence v3.0": "OGL-UK-3.0",
    "cc-by-4.0": "CC-BY-4.0", "cc by 4.0": "CC-BY-4.0", "cc-by": "CC-BY-4.0", "cc by": "CC-BY-4.0",
    "cc-by-3.0": "CC-BY-3.0", "cc0": "CC0-1.0", "cc0-1.0": "CC0-1.0", "cc0 1.0": "CC0-1.0",
    "pddl": "PDDL-1.0", "pddl-1.0": "PDDL-1.0", "public domain": "public-domain", "pd": "public-domain",
    "public-domain": "public-domain", "odbl": "ODbL-1.0", "odbl-1.0": "ODbL-1.0", "odbl 1.0": "ODbL-1.0",
    "neso open data licence": "NESO-Open-Data-Licence", "neso open licence": "NESO-Open-Data-Licence",
    "neso-open-data-licence": "NESO-Open-Data-Licence",
    "open parliament licence": "Open-Parliament-Licence-3.0", "open-parliament-licence-3.0": "Open-Parliament-Licence-3.0",
}
LICENCE_URLS = {
    "OGL-UK-3.0": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "PDDL-1.0": "https://opendatacommons.org/licenses/pddl/1-0/",
    "public-domain": "https://creativecommons.org/publicdomain/mark/1.0/",
    "ODbL-1.0": "https://opendatacommons.org/licenses/odbl/1-0/",
    "NESO-Open-Data-Licence": "https://www.neso.energy/data-portal/neso-open-licence",
    "Open-Parliament-Licence-3.0": "https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
}
STATUS_MAP = [
    (r"refus|withdraw|cancel|abandon|dismiss", "refused_or_withdrawn"),
    (r"under.?construction|construct|building|being built", "under_construction"),
    (r"operat|live|existing|open|in.?service|energi[sz]ed|connected|built", "operational"),
    (r"consent|approv|granted|permitted|permission", "consented"),
    (r"planning|submitted|application|pending|validated|scoping|pre.?app|eia", "in_planning"),
    (r"connection|queue|tec|contracted|offer", "connection_request"),
    (r"announc|proposed|propos|plan(ned)?|concept|ai growth zone|aigz|pipeline", "announced"),
]
PRECISION_MAP = {
    "exact": "exact_site", "exact_site": "exact_site", "site": "exact_site", "building": "exact_site",
    "polygon": "exact_site", "centroid": "exact_site", "osm": "exact_site",
    "address": "address", "planning_site": "planning_site", "planning": "planning_site",
    "postcode": "postcode", "settlement": "settlement", "town": "settlement", "city": "settlement",
    "village": "settlement", "local_authority": "local_authority", "la": "local_authority",
    "lpa": "local_authority", "district": "local_authority", "region": "region", "county": "region",
    "unknown": "unknown", "approx": "settlement", "approximate": "settlement",
}

IT_KEYS = ["it_load_mw", "it_mw", "it_capacity_mw", "critical_it_mw", "it_load"]
GRID_KEYS = ["grid_connection_mw", "connection_mw", "grid_mw", "import_capacity_mw", "tec_mw",
             "connection_capacity_mw", "mw_connection", "demand_mw", "max_import_mw"]
UNCL_KEYS = ["unclassified_mw", "mw_unclassified", "other_mw", "mw_unqualified", "facility_power_mw", "mw", "capacity_mw", "capacity", "power_mw", "size_mw"]


def _get(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, "", []):
            return d[k], k
    return None, None


def _num(v):
    if isinstance(v, list):  # e.g. [{"mw": 12.0, "raw": ..., "source_id": ...}]: first stated MW
        v = next((x for x in v if isinstance(x, dict) and _num(x.get("mw", x.get("value"))) is not None), None)
        return _num(v.get("mw", v.get("value"))) if v else None
    if isinstance(v, dict):
        v = v.get("value")
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(\.\d+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _mw_src(v):
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, dict) and _num(x.get("mw", x.get("value"))) is not None), None)
    return v.get("source_id") if isinstance(v, dict) else None


def norm_licence(s):
    if not s:
        return None
    if isinstance(s, dict):
        s = s.get("id") or s.get("name") or s.get("licence")
    key = str(s).strip().lower()
    if re.search(r"(^|[^a-z])(nc|nd|sa)([^a-z]|$)|non.?commercial|no.?deriv|share.?alike|by-sa", key) and "odbl" not in key:
        return None  # not fully open under the owner rule (ODbL is the one allowed share-alike)
    if key in LICENCES:
        return LICENCES[key]
    for k, v in sorted(LICENCES.items(), key=lambda kv: -len(kv[0])):
        if len(k) > 3 and k in key:
            return v
    if str(s) in LICENCE_URLS:
        return str(s)
    return None


def norm_status(s):
    if not s:
        return "unknown"
    s = str(s).strip().lower()
    if s in dcmerge.STATUS_ORDER:
        return s
    s2 = s.replace(" ", "_")
    if s2 in dcmerge.STATUS_ORDER:
        return s2
    for pat, st in STATUS_MAP:
        if re.search(pat, s):
            return st
    return "unknown"


PRECISION_KEYWORDS = [
    ("exact_site", ("exact", "osm node", "footprint", "building", "polygon", "way centre", "centroid")),
    ("address", ("address",)),
    ("planning_site", ("planning", "red line", "application site")),
    ("postcode", ("postcode",)),
    ("settlement", ("settlement", "town", "city", "village", "approx", "wikidata", "p625")),
    ("local_authority", ("local authority", "local_authority", "district", "lpa", "council")),
    ("region", ("region", "county", "nation")),
]


def norm_precision(v):
    if not v:
        return None
    k = str(v).strip().lower()
    if k in PRECISION_MAP:
        return PRECISION_MAP[k]
    for prec, keys in PRECISION_KEYWORDS:
        if any(x in k for x in keys):
            return prec
    return None


def norm_date(v):
    if not v:
        return None
    m = re.match(r"^(\d{4})(-\d{2})?(-\d{2})?", str(v))
    return "".join(g for g in m.groups() if g) if m else None


def _records_from(doc):
    """Yield (record_dict, geometry_or_None, file_meta)."""
    meta = {}
    if isinstance(doc, dict):
        meta = {k: v for k, v in doc.items() if k not in ("features", "records", "sites", "items")}
        for k in ("metadata", "meta", "source", "provenance"):
            if isinstance(doc.get(k), dict):
                meta.update(doc[k])
        if doc.get("type") == "FeatureCollection" or "features" in doc:
            for f in doc.get("features") or []:
                yield dict(f.get("properties") or {}), f.get("geometry"), meta, f.get("id")
            return
        for k in ("records", "sites", "items", "data"):
            if isinstance(doc.get(k), list):
                for r in doc[k]:
                    if isinstance(r, dict):
                        yield r, r.get("geometry"), meta, None
                return
        return
    if isinstance(doc, list):
        for r in doc:
            if isinstance(r, dict):
                if r.get("type") == "Feature":
                    yield dict(r.get("properties") or {}), r.get("geometry"), meta, r.get("id")
                else:
                    yield r, r.get("geometry"), meta, None


def _point_of(r, geom):
    if geom and geom.get("type") == "Point":
        c = geom.get("coordinates") or []
        if len(c) >= 2:
            return float(c[1]), float(c[0])
    if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
        ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return sum(ys) / len(ys), sum(xs) / len(xs)
    loc = r.get("location") if isinstance(r.get("location"), dict) else {}
    lat, _ = _get({**r, **loc}, ["lat", "latitude", "y"])
    lon, _ = _get({**r, **loc}, ["lon", "lng", "longitude", "x"])
    if lat is not None and lon is not None:
        try:
            return float(lat), float(lon)
        except (TypeError, ValueError):
            return None
    return None


def normalise(raw, geom, meta, fid, lane, fname, file_sha):
    """One raw lane record -> (schema record, None) or (None, reason)."""
    r = raw
    name, _ = _get(r, ["name", "site_name", "title", "project_name", "project"])
    dcm = r.get("dc_match")
    if isinstance(dcm, dict) and dcm.get("is_dc_like") is False:
        return None, "lane heuristic: not data-centre-like"
    if str(r.get("licence_fully_open", "")).lower().startswith("no"):
        return None, "lane says licence not fully open"
    if isinstance(r.get("site_kind"), str) and "cable_landing" in r["site_kind"]:
        return None, "cable landing station, not a data centre (excluded as ot_filter.py does)"
    if not name:
        if r.get("operator") or _point_of(r, geom):
            name = "Unnamed data centre"  # 'unnamed' is a stop word: never name-matches
        else:
            return None, "no name"
    src_in = r.get("sources") if isinstance(r.get("sources"), list) and r.get("sources") else None
    src_in = src_in or ([r["source"]] if isinstance(r.get("source"), dict) else None)
    lic_raw = None
    srcs = []
    for s in (src_in or [{}]):
        s = s if isinstance(s, dict) else {}
        lic_raw = s.get("licence") or s.get("license") or r.get("licence") or r.get("license") \
            or meta.get("licence") or meta.get("license")
        lic = norm_licence(lic_raw)
        if not lic:
            return None, "licence not fully open or missing (%r)" % (lic_raw,)
        url = s.get("url") or s.get("evidence_url") or s.get("snapshot_file") or r.get("evidence_url") or r.get("url") \
            or r.get("source_url") or meta.get("url") or meta.get("source_url")
        if not url or not str(url).startswith(("http://", "https://")):
            return None, "no source URL"
        dataset = s.get("dataset") or (s.get("source") if isinstance(s.get("source"), str) else None) or r.get("dataset") or \
            (r.get("source") if isinstance(r.get("source"), str) else None) or meta.get("dataset") or \
            meta.get("name") or os.path.splitext(fname)[0]
        ref = s.get("record_ref") or s.get("source_id") or s.get("ref") or \
            (None if src_in and len(src_in) > 1 else (r.get("record_ref") or r.get("ref") or r.get("osm_id") or \
            r.get("wikidata") or r.get("qid") or fid or r.get("id")))
        if ref is None:
            ref = hashlib.sha256(json.dumps([raw, s], sort_keys=True, default=str).encode()).hexdigest()[:12]
        lic_url = s.get("licence_url") or s.get("license_url") or r.get("licence_url") or \
            (lic_raw.get("url") if isinstance(lic_raw, dict) else None) or \
            meta.get("licence_url") or meta.get("license_url") or LICENCE_URLS[lic]
        status_v = s.get("licence_status") or r.get("licence_status") or meta.get("licence_status")
        sha = s.get("sha256") or s.get("snapshot_sha256") or r.get("sha256") or meta.get("sha256")
        if not (isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha)):
            sha = None
        srcs.append({
            "id": "%s:%s:%s" % (lane, dataset, ref), "lane": lane, "dataset": str(dataset),
            "record_ref": str(ref), "url": str(url), "licence": lic, "licence_url": str(lic_url),
            "licence_status": "verified" if status_v == "verified" else "unverified",
            "attribution": s.get("attribution") or r.get("attribution") or meta.get("attribution"),
            "retrieved": norm_date(s.get("retrieved") or r.get("retrieved") or meta.get("retrieved")
                                   or meta.get("fetched") or meta.get("generated")),
            "sha256": sha,
        })
    sid = srcs[0]["id"]
    member_names = {str(x.get("name")) for x in (src_in or []) if isinstance(x, dict) and x.get("name")}
    notes = r.get("notes") if isinstance(r.get("notes"), list) else [r["notes"]] if r.get("notes") else []
    for k in ("status_basis", "screening_note"):
        if r.get(k):
            notes.append("%s: %s" % (k, r[k]))
    for c in r.get("conflicts") or []:  # lane-level flags kept verbatim as notes
        notes.append("lane flag: " + json.dumps(c, sort_keys=True, ensure_ascii=False))
    for k in ("mw_evidence", "onsite_generation_mw", "stated_targets"):  # context only; never an IT or grid figure
        if r.get(k):
            notes.append("%s: %s" % (k, json.dumps(r[k], ensure_ascii=False)))

    def mw(keys):
        v, k = _get(r, keys)
        n = _num(v)
        if n is None or n < 0 or n > 10000:
            return {"value": None, "source_id": None, "label": None}
        s = _mw_src(v)
        first = v
        if isinstance(v, list):
            first = next((x for x in v if isinstance(x, dict) and _num(x.get("mw", x.get("value"))) is not None), {})
        extra = ()
        if isinstance(first, dict):
            q = first.get("quote") or first.get("raw")
            extra = (first.get("measure") or first.get("property"), first.get("qualifier"),
                     q and "quote: \"%s\"" % q, first.get("evidence_url"))
        label = " | ".join(str(x) for x in (k, r.get(k + "_basis"), r.get(k + "_direction") and
                                            "direction: %s" % r.get(k + "_direction")) + extra if x)
        return {"value": n, "source_id": s if s in {x["id"] for x in srcs} else sid, "label": label}

    for k in ("other_mw", "mw_unclassified"):  # on-site generation / gas is not site demand: notes only
        if isinstance(r.get(k), list):
            gen = [x for x in r[k] if isinstance(x, dict) and re.search(r"generat|\bgas\b|export", str(x.get("measure") or x.get("property") or ""), re.I)]
            if gen:
                r = dict(r, **{k: [x for x in r[k] if x not in gen]})
                notes.append("%s excluded (generation, not demand): %s" % (k, json.dumps(gen, ensure_ascii=False)))
    it = mw(IT_KEYS)
    grid = mw(GRID_KEYS)
    uncl = mw(UNCL_KEYS)
    pt = _point_of(r, geom)
    loc_in = r.get("location") if isinstance(r.get("location"), dict) else {}
    prec_raw = loc_in.get("precision") or r.get("location_precision") or r.get("precision")
    prec = norm_precision(prec_raw)
    if pt and not (49.0 <= pt[0] <= 61.5 and -9.0 <= pt[1] <= 2.5):
        return None, "point outside UK bounds"
    if pt is None:
        prec = "unknown"
    elif prec in (None, "unknown"):
        prec = "exact_site" if geom and geom.get("type") in ("Polygon", "MultiPolygon") else "settlement"
    dates_in = r.get("dates") if isinstance(r.get("dates"), dict) else {}
    dates = {}
    aliases = {"announced": ["announced", "announced_date", "date_announced"],
               "planning_submitted": ["planning_submitted", "submitted", "application_date", "received"],
               "consented": ["consented", "decision_date", "consent_date", "approved"],
               "construction_start": ["construction_start"],
               "operational": ["operational", "operational_date", "opened", "commissioned", "launched"],
               "connection_date": ["connection_date", "mw_effective_from", "connection_year"]}
    for f, keys in aliases.items():
        v, _ = _get({**r, **dates_in}, keys)
        dates[f] = norm_date(v)
    status_raw, _ = _get(r, ["status", "stage", "development_status", "planning_status", "decision"])
    if not dates["operational"] and norm_status(status_raw) == "operational":
        # OSM start_date on an existing facility = when it came into use
        dates["operational"] = norm_date(_get({**r, **dates_in}, ["start_date"])[0])
    st_norm = norm_status(status_raw)
    if not dates["consented"] and st_norm == "consented":
        dates["consented"] = norm_date(_get({**r, **dates_in}, ["decision"])[0])
    used = {k for ks in aliases.values() for k in ks} | {"start_date", "decision"}
    for k, v in sorted(dates_in.items()):
        if v and (k not in used or (k == "decision" and st_norm != "consented")):
            notes.append("lane date %s: %s" % (k, v))
    for k in ("status_note", "status_raw", "status_as_of", "coordinates_source", "location_note", "place", "route",
              "operator_basis"):
        if r.get(k):
            notes.append("%s: %s" % (k, r[k]))
    prefs = []
    for p in r.get("planning_refs") or []:
        if isinstance(p, dict) and p.get("authority") and p.get("reference"):
            prefs.append({"authority": str(p["authority"]), "reference": str(p["reference"]), "url": p.get("url")})
    pr, _ = _get(r, ["planning_ref", "planning_reference", "application_ref", "reference"])
    pa, _ = _get(r, ["planning_authority", "lpa", "local_planning_authority", "organisation", "authority"])
    if pr and pa:
        prefs.append({"authority": str(pa), "reference": str(pr), "url": None})
    conns = [str(c) for c in (r.get("connection_refs") or []) if c]
    conn_in = r.get("connection") if isinstance(r.get("connection"), dict) else {}
    cr, _ = _get({**r, **conn_in}, ["connection_ref", "project_id", "tec_ref", "queue_ref"])
    if conn_in.get("site_name"):
        notes.append("connection site (name only, not a location): %s" % conn_in["site_name"])
    if cr:
        conns.append(str(cr))
    rec = {
        "name": str(name).strip(),
        "aliases": sorted(({str(a) for a in (r.get("aliases") or []) if a} | member_names) - {str(name).strip()}),
        "operator": (str(_get(r, ["operator", "developer", "owner", "applicant", "company"])[0]).strip() or None)
        if _get(r, ["operator", "developer", "owner", "applicant", "company"])[0] else None,
        "status": norm_status(status_raw),
        "status_source_id": sid if status_raw else None,
        "it_load_mw": it, "grid_connection_mw": grid, "unclassified_mw": uncl,
        "dates": dates,
        "location": {"lat": round(pt[0], 6) if pt else None, "lon": round(pt[1], 6) if pt else None,
                     "precision": prec, "source_id": sid if pt else None,
                     "local_authority": loc_in.get("local_authority") or r.get("local_authority") or r.get("la"),
                     "postcode": loc_in.get("postcode") or r.get("postcode")},
        "planning_refs": prefs, "connection_refs": sorted(set(conns)),
        "evidence_urls": sorted({str(u) for u in ([r.get("evidence_url"), r.get("evidence_url_2")]
                                                  + list(r.get("evidence_urls") or [])
                                                  + [x.get("evidence_url") for x in (src_in or []) if isinstance(x, dict)])
                                 if isinstance(u, str) and u.startswith(("http://", "https://"))}),
        "sources": srcs, "conflicts": [],
        "notes": sorted({str(n) for n in notes}),
    }
    if uncl["value"] is not None and uncl["value"] in (it["value"], grid["value"]):
        # the bare figure repeats a labelled one; keep only the labelled one
        rec["unclassified_mw"] = {"value": None, "source_id": None, "label": None}
    rec["site_key"] = dcmerge.geo_key(rec)
    rec["id"] = dcschema.site_id(rec["site_key"])
    errs = dcschema.validate(rec)
    if errs:
        return None, "schema: " + "; ".join(errs[:3])
    return rec, None


def load_lanes(dirs):
    recs, rejects, inputs = [], [], []
    for d in dirs:
        lane = os.path.basename(d.rstrip("/"))
        files = sorted(p for p in glob.glob(os.path.join(d, "**", "*"), recursive=True)
                       if p.endswith((".json", ".geojson")) and os.path.isfile(p)
                       and not set(os.path.relpath(p, d).split(os.sep)[:-1]) & SKIP_DIRS)
        stems = {os.path.splitext(p)[0] for p in files if p.endswith(".json")}
        files = [p for p in files if not (p.endswith(".geojson") and os.path.splitext(p)[0] in stems)]  # X.json wins over X.geojson
        seen = set()
        for path in files:
            with open(path, "rb") as f:
                raw = f.read()
            sha = hashlib.sha256(raw).hexdigest()
            fname = os.path.relpath(path, d)
            try:
                doc = json.loads(raw)
            except ValueError as e:
                rejects.append({"file": "%s/%s" % (lane, fname), "reason": "invalid JSON: %s" % e})
                continue
            n_ok = n_bad = 0
            for r, geom, meta, fid in _records_from(doc):
                rec, why = normalise(r, geom, meta, fid, lane, fname, sha)
                key = tuple(sorted(x["id"] for x in rec["sources"])) if rec else None
                if rec and key in seen:
                    n_dup = 1  # same lane record already read from a sibling file (e.g. X.json + X.geojson)
                    rejects.append({"file": "%s/%s" % (lane, fname), "reason": "duplicate of a record already read from this lane",
                                    "name": rec["name"][:80], "duplicate": True})
                    continue
                if rec:
                    seen.add(key)
                    recs.append(rec)
                    n_ok += 1
                else:
                    n_bad += 1
                    rejects.append({"file": "%s/%s" % (lane, fname), "reason": why,
                                    "name": str(r.get("name") or r.get("site_name") or "")[:80]})
            inputs.append({"file": "%s/%s" % (lane, fname), "sha256": sha, "accepted": n_ok, "rejected": n_bad})
    return recs, rejects, inputs


def _round(o):
    if isinstance(o, float):
        return round(o, 6)
    if isinstance(o, dict):
        return {k: _round(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v) for v in o]
    return o


def _reasons(rejects):
    c = {}
    for x in rejects:
        k = re.sub(r"\(.*\)$", "", x["reason"]).strip()
        c[k] = c.get(k, 0) + 1
    return dict(sorted(c.items()))


def build(dirs, out):
    recs, rejects, inputs = load_lanes(dirs)
    merged = dcmerge.merge(recs)
    feats = [_round(dcmerge.to_feature(m)) for m in merged]
    for f in feats:
        f["properties"] = _round(f["properties"])
    by_status, by_source, by_lic, conflicts = {}, {}, {}, {}
    for m in merged:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
        for ds in sorted({s["lane"] + ":" + s["dataset"] for s in m["sources"]}):
            by_source[ds] = by_source.get(ds, 0) + 1
        for lic in sorted({s["licence"] for s in m["sources"]}):
            by_lic[lic] = by_lic.get(lic, 0) + 1
        for c in m["conflicts"]:
            conflicts[c["kind"]] = conflicts.get(c["kind"], 0) + 1
    summary = {
        "input_records": len(recs), "rejected_records": sum(1 for x in rejects if not x.get("duplicate")),
        "duplicate_lane_records_skipped": sum(1 for x in rejects if x.get("duplicate")),
        "rejects_by_reason": _reasons(rejects), "sites": len(merged),
        "sites_from_several_datasets": sum(1 for m in merged if len({s["dataset"] for s in m["sources"]}) > 1),
        "sites_from_several_lanes": sum(1 for m in merged if len({s["lane"] for s in m["sources"]}) > 1),
        "sites_merged_from_several_input_records": sum(1 for m in merged if m["match"]),
        "by_status": dict(sorted(by_status.items())), "by_source": dict(sorted(by_source.items())),
        "by_licence": dict(sorted(by_lic.items())), "conflicts_by_kind": dict(sorted(conflicts.items())),
        "it_load_mw_known": sum(1 for m in merged if m["it_load_mw"]["value"] is not None),
        "grid_connection_mw_known": sum(1 for m in merged if m["grid_connection_mw"]["value"] is not None),
        "unclassified_mw_known": sum(1 for m in merged if m["unclassified_mw"]["value"] is not None),
        "sum_it_load_mw_known": round(sum(m["it_load_mw"]["value"] or 0 for m in merged), 3),
        "sum_grid_connection_mw_known": round(sum(m["grid_connection_mw"]["value"] or 0 for m in merged), 3),
    }
    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "title": "UK data-centre pipeline (merged, GridAtlas #73 steps 3-4)",
            "schema": "dc_pipeline.schema.json",
            "caveats": ["A pipeline entry is not a commitment.", "Unknown MW is null, not zero.",
                        "IT load MW and grid connection MW are separate and never derived from each other.",
                        "unclassified_mw is a source figure that does not say which it is.",
                        "Location is not connection, capacity or feasibility.",
                        "Licences marked 'unverified' have not been checked against the publisher's terms."],
            "inputs": inputs, "summary": summary,
        },
        "features": feats,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(fc, f, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    with open(os.path.splitext(out)[0] + ".rejects.json", "w", encoding="utf-8") as f:
        json.dump(sorted(rejects, key=lambda x: (x["file"], x.get("name", ""), x["reason"])), f,
                  indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    return fc, rejects


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lanes", nargs="*", default=DEFAULT_LANES)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    fc, rejects = build(a.lanes, a.out)
    print(json.dumps(fc["metadata"]["summary"], indent=1))
    errs = dcschema.validate_file(a.out)
    print("validation errors:", len(errs))
    for e in errs[:20]:
        print(" ", e)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
