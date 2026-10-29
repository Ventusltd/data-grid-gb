#!/usr/bin/env python3
"""Dedupe / merge engine for the UK data-centre pipeline (GridAtlas #73 step 4).

Input: a list of source-level records (dc_pipeline.schema.json, usually one source each).
Output: merged site records, one per physical site, sorted by stable id, with every
source kept and every disagreement flagged in conflicts[].

Matching rules (pairwise, strongest first):
  R1 planning_ref     same normalised (authority, reference)
  R2 connection_ref   same normalised queue/register reference
  R3 name_distance    within the distance threshold and name similarity >= 0.85
  R4 operator_distance within the threshold, same operator, name similarity >= 0.50
  R5 name_operator_exact  identical normalised name AND operator (for coarse/no location)
Distance threshold = that of the COARSER precision of the pair (THRESHOLD_KM);
'region'/'unknown' precision never matches on distance.
Guards (cannot-link, applied to whole clusters, so no chaining across them):
  * names carrying different designators with digits (LD4 vs LD5, "Building 2" vs "3");
  * two records from the same dataset need R1/R2 or name similarity >= 0.95.
Links are applied strongest-first in a fixed order, so the output is deterministic.

Never: compare IT load with grid connection MW; turn unknown into zero; treat a
pipeline entry as a commitment.
"""
import difflib
import hashlib
import math
import re

STATUS_ORDER = ["operational", "under_construction", "consented", "in_planning",
                "connection_request", "announced", "refused_or_withdrawn", "unknown"]
PRECISION_ORDER = ["exact_site", "address", "planning_site", "postcode", "settlement",
                   "local_authority", "region", "unknown"]
THRESHOLD_KM = {"exact_site": 0.5, "address": 1.0, "planning_site": 1.0, "postcode": 2.0,
                "settlement": 5.0, "local_authority": 15.0, "region": None, "unknown": None}
NAME_SIM_STRONG = 0.85
NAME_SIM_WEAK = 0.50
NAME_SIM_SAME_SOURCE = 0.95
RULE_RANK = {"planning_ref": 0, "connection_ref": 1, "evidence_document": 2, "name_distance": 3,
             "operator_distance": 4, "name_operator_exact": 5}
URL_KEY_MAX_RECORDS = 2  # a URL is a site key only if at most this many input records cite it
DATE_FIELDS = ["announced", "planning_submitted", "consented", "construction_start",
               "operational", "connection_date"]
MW_FIELDS = ["it_load_mw", "grid_connection_mw", "unclassified_mw"]

_NAME_STOP = {"data", "centre", "center", "centres", "centers", "datacentre", "datacenter",
              "datacentres", "datacenters", "dc", "campus", "ltd", "limited", "plc", "llp",
              "llc", "inc", "the", "uk", "site", "facility", "of", "and", "unnamed"}
_OP_STOP = {"ltd", "limited", "plc", "llp", "llc", "inc", "uk", "group", "holdings", "holding",
            "europe", "emea", "data", "centre", "centres", "center", "centers", "services",
            "the", "co", "company", "gb", "international", "and"}
_AUTH_STOP = {"council", "borough", "district", "city", "county", "of", "the", "metropolitan",
              "royal", "and", "lpa", "planning", "authority"}

# Source tiers (lower = more authoritative for status / MW / dates). Matched on
# lower-cased dataset + url; first hit wins.
TIERS = [
    (1, ("neso", "national grid eso", "tec register", "transmission entry", "connection register",
         "connections register", "queue", "embedded capacity", "ecr", "ukpn", "ssen", "nged",
         "northern powergrid", "enwl", "electricity north west", "spen", "sp energy")),
    (2, ("planning", "planit", "nsip", "pins", "dco")),
    (3, ("dsit", "gov.uk", "ai growth zone", "treasury", "desnz", "parliament")),
    (4, ("company", "disclosure", "press", "investor", "annual report")),
    (5, ("wikidata",)),
    (6, ("openstreetmap", "osm", "overpass")),
]


def source_tier(src):
    hay = ("%s %s" % (src.get("dataset", ""), src.get("url", ""))).lower()
    for tier, keys in TIERS:
        if any(k in hay for k in keys):
            return tier
    return 7


# ---------------------------------------------------------------- normalisers
def _tokens(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.split()


def norm_name(s):
    return " ".join(t for t in _tokens(s) if t not in _NAME_STOP)


def norm_operator(s):
    return " ".join(t for t in _tokens(s) if t not in _OP_STOP)


def norm_authority(s):
    return " ".join(t for t in _tokens(s) if t not in _AUTH_STOP)


def norm_ref(s):
    return re.sub(r"\s+", "", (s or "").upper())


def designators(name):
    """Tokens that carry a digit (LD4, 2, phase2) -- distinguish neighbouring buildings."""
    toks = _tokens(name)
    out = set()
    for i, t in enumerate(toks):
        if any(c.isdigit() for c in t):
            prev = toks[i - 1] if i else ""
            out.add(t if not t.isdigit() or not prev or prev in _NAME_STOP else prev + t)
    return frozenset(out)


def name_similarity(a, b):
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jac = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return round(max(jac, seq), 4)


def operator_match(a, b):
    na, nb = norm_operator(a), norm_operator(b)
    if not na or not nb:
        return None  # unknown
    sa, sb = set(na.split()), set(nb.split())
    return sa == sb or sa <= sb or sb <= sa


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def coarser(p1, p2):
    return PRECISION_ORDER[max(PRECISION_ORDER.index(p1), PRECISION_ORDER.index(p2))]


def site_id(site_key):
    return "dcuk-" + hashlib.sha256(site_key.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- record helpers
def rec_key(r):
    """Deterministic identity of an input record: its sorted source ids."""
    return "|".join(sorted(s["id"] for s in r["sources"]))


def rec_tier(r):
    return min(source_tier(s) for s in r["sources"])


def rec_datasets(r):
    return {s.get("dataset") for s in r["sources"]}


def _point(r):
    loc = r.get("location") or {}
    if loc.get("lat") is None or loc.get("lon") is None:
        return None
    return loc["lat"], loc["lon"]


def _precision(r):
    return (r.get("location") or {}).get("precision") or "unknown"


def plan_keys(r):
    return {"plan:%s:%s" % (norm_authority(p["authority"]), norm_ref(p["reference"]))
            for p in r.get("planning_refs") or []}


def conn_keys(r):
    return {"conn:%s" % norm_ref(c) for c in r.get("connection_refs") or []}


def norm_url(u):
    u = (u or "").strip().split("#")[0].split("?")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u).lower()


def raw_url_keys(r):
    """Candidate document URLs: specific paths only (>= 2 path segments)."""
    out = set()
    for u in list(r.get("evidence_urls") or []) + [s.get("url") for s in r["sources"]]:
        n = norm_url(u)
        if n.count("/") >= 2:
            out.add("url:" + n)
    return out


_URL_OK = None  # set by merge(): URL keys cited by <= URL_KEY_MAX_RECORDS input records


def url_keys(r):
    ks = raw_url_keys(r)
    return ks & _URL_OK if _URL_OK is not None else set()


URL_MAX_KM = 25.0


def _lanes(r):
    return {s.get("lane") for s in r["sources"]}


def url_link(a, b, dist):
    """Shared document URL counts only across DIFFERENT lanes (a lane has already de-duplicated
    itself, so two of its records citing one press release are two sites), and never when both
    points are placed (finer than region) more than URL_MAX_KM apart."""
    if not (url_keys(a) & url_keys(b)) or (_lanes(a) & _lanes(b)):
        return False
    if dist is not None and dist > URL_MAX_KM and THRESHOLD_KM[coarser(_precision(a), _precision(b))] is not None:
        return False
    return True


def geo_key(r):
    pt = _point(r)
    lat = "%.3f" % pt[0] if pt else "na"
    lon = "%.3f" % pt[1] if pt else "na"
    return "geo:%s|%s|%s|%s" % (norm_name(r.get("name")), norm_operator(r.get("operator")), lat, lon)


# ---------------------------------------------------------------- pair logic
def compare(a, b):
    """Return (rule, name_sim, distance_km) if a and b are the same site, else None."""
    sim = name_similarity(a.get("name"), b.get("name"))
    pa, pb = _point(a), _point(b)
    dist = round(haversine_km(pa[0], pa[1], pb[0], pb[1]), 4) if pa and pb else None
    same_source = bool(rec_datasets(a) & rec_datasets(b))
    if plan_keys(a) & plan_keys(b):
        return "planning_ref", sim, dist
    if conn_keys(a) & conn_keys(b):
        return "connection_ref", sim, dist
    if url_link(a, b, dist):
        return "evidence_document", sim, dist
    da, db = designators(a.get("name")), designators(b.get("name"))
    if da and db and da != db:
        return None
    if same_source and sim < NAME_SIM_SAME_SOURCE:
        return None
    thr = THRESHOLD_KM[coarser(_precision(a), _precision(b))]
    if dist is not None and thr is not None and dist <= thr:
        if sim >= NAME_SIM_STRONG:
            return "name_distance", sim, dist
        if operator_match(a.get("operator"), b.get("operator")) and sim >= NAME_SIM_WEAK:
            return "operator_distance", sim, dist
    if (dist is None or thr is None) and sim == 1.0 and operator_match(a.get("operator"), b.get("operator")):
        return "name_operator_exact", sim, dist
    return None


def cannot_link(a, b):
    """Hard separation regardless of other links (prevents transitive chaining)."""
    pa, pb = _point(a), _point(b)
    dist = haversine_km(pa[0], pa[1], pb[0], pb[1]) if pa and pb else None
    if plan_keys(a) & plan_keys(b) or conn_keys(a) & conn_keys(b) or url_link(a, b, dist):
        return False
    da, db = designators(a.get("name")), designators(b.get("name"))
    if da and db and da != db:
        return True
    if rec_datasets(a) & rec_datasets(b) and name_similarity(a.get("name"), b.get("name")) < NAME_SIM_SAME_SOURCE:
        return True
    return False


def candidate_pairs(recs):
    """Blocking: shared refs, shared exact name, or neighbouring 0.2-degree cells."""
    pairs = set()
    index = {}
    for i, r in enumerate(recs):
        keys = set(plan_keys(r)) | set(conn_keys(r)) | url_keys(r)
        nm = norm_name(r.get("name"))
        if nm:
            keys.add("name:" + nm)
        pt = _point(r)
        if pt and THRESHOLD_KM[_precision(r)] is not None:
            keys.add("cell:%d:%d" % (math.floor(pt[0] / 0.2), math.floor(pt[1] / 0.2)))
        for k in keys:
            index.setdefault(k, []).append(i)
    for k, members in index.items():
        if k.startswith("cell:"):
            _, cy, cx = k.split(":")
            cy, cx = int(cy), int(cx)
            neigh = []
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    neigh += index.get("cell:%d:%d" % (cy + dy, cx + dx), [])
            for i in members:
                for j in neigh:
                    if i < j:
                        pairs.add((i, j))
        else:
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    i, j = members[x], members[y]
                    pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


# ---------------------------------------------------------------- clustering
def cluster(recs):
    links = []
    for i, j in candidate_pairs(recs):
        res = compare(recs[i], recs[j])
        if res:
            rule, sim, dist = res
            links.append((RULE_RANK[rule], dist if dist is not None else 1e9, i, j, rule, sim, dist))
    links.sort()
    parent = list(range(len(recs)))
    members = {i: [i] for i in range(len(recs))}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    used = {}
    for _, _, i, j, rule, sim, dist in links:
        ri, rj = find(i), find(j)
        if ri == rj:
            used.setdefault(ri, []).append((i, j, rule, sim, dist))
            continue
        if any(cannot_link(recs[x], recs[y]) for x in members[ri] for y in members[rj]):
            continue
        lo, hi = min(ri, rj), max(ri, rj)
        parent[hi] = lo
        members[lo] += members.pop(hi)
        used.setdefault(lo, []).extend(used.pop(hi, []))
        used[lo].append((i, j, rule, sim, dist))
    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(i)
    return [(sorted(g), used.get(root, [])) for root, g in sorted(groups.items())]


# ---------------------------------------------------------------- merge
def _order(recs):
    """Members by authority: tier, then location precision, then record key."""
    return sorted(recs, key=lambda r: (rec_tier(r), PRECISION_ORDER.index(_precision(r)), rec_key(r)))


def _first_src(r):
    return sorted(r["sources"], key=lambda s: (source_tier(s), s["id"]))[0]["id"]


def _mw_differ(a, b):
    return abs(a - b) > max(0.5, 0.05 * max(a, b))


def _dates_compatible(a, b):
    return a.startswith(b) or b.startswith(a)


def merge_group(recs, links):
    ordered = _order(recs)
    top = ordered[0]
    out = {}
    conflicts = []
    for r in recs:
        conflicts.extend(r.get("conflicts") or [])

    # sources
    srcs = {}
    for r in recs:
        for s in r["sources"]:
            srcs.setdefault(s["id"], s)
    out["sources"] = [srcs[k] for k in sorted(srcs)]

    # name / aliases
    out["name"] = top["name"]
    names = set()
    for r in recs:
        names.add(r["name"])
        names.update(r.get("aliases") or [])
    out["aliases"] = sorted(n for n in names if n != out["name"])

    # operator
    ops = [(r.get("operator"), _first_src(r)) for r in ordered if r.get("operator")]
    out["operator"] = ops[0][0] if ops else None
    distinct = []
    for op, sid in ops:
        if not any(operator_match(op, d[0]) for d in distinct):
            distinct.append((op, sid))
    if len(distinct) > 1:
        conflicts.append({"field": "operator", "kind": "operator_disagreement",
                          "values": [{"value": v, "source_id": s} for v, s in ops],
                          "chosen": out["operator"], "note": "highest-tier source chosen; may be a change of owner"})

    # status
    sts = [(r["status"], r.get("status_source_id") or _first_src(r)) for r in ordered if r.get("status") not in (None, "unknown")]
    out["status"] = sts[0][0] if sts else "unknown"
    out["status_source_id"] = sts[0][1] if sts else None
    if len({v for v, _ in sts}) > 1:
        conflicts.append({"field": "status", "kind": "status_disagreement",
                          "values": [{"value": v, "source_id": s} for v, s in sts],
                          "chosen": out["status"], "note": "highest-tier source chosen; other statuses kept here"})

    # MW: each field on its own; never across fields
    for f in MW_FIELDS:
        vals = [(r[f]["value"], r[f]["source_id"], r[f].get("label")) for r in ordered
                if isinstance(r.get(f), dict) and r[f].get("value") is not None]
        if vals:
            out[f] = {"value": vals[0][0], "source_id": vals[0][1], "label": vals[0][2]}
            if any(_mw_differ(vals[0][0], v[0]) for v in vals[1:]):
                conflicts.append({"field": f, "kind": "mw_disagreement",
                                  "values": [{"value": v, "source_id": s} for v, s, _ in vals],
                                  "chosen": vals[0][0], "note": "highest-tier source chosen"})
        else:
            out[f] = {"value": None, "source_id": None, "label": None}

    # dates: choose highest tier; widen to a more specific compatible value
    out["dates"] = {}
    for f in DATE_FIELDS:
        vals = [((r.get("dates") or {}).get(f), _first_src(r)) for r in ordered]
        vals = [v for v in vals if v[0]]
        if not vals:
            out["dates"][f] = None
            continue
        chosen = vals[0][0]
        for v, _ in vals[1:]:
            if _dates_compatible(chosen, v) and len(v) > len(chosen):
                chosen = v
        out["dates"][f] = chosen
        if any(not _dates_compatible(vals[0][0], v) for v, _ in vals[1:]):
            conflicts.append({"field": "dates." + f, "kind": "date_disagreement",
                              "values": [{"value": v, "source_id": s} for v, s in vals],
                              "chosen": chosen, "note": None})

    # location: best precision, then tier
    by_prec = sorted(recs, key=lambda r: (PRECISION_ORDER.index(_precision(r)), rec_tier(r), rec_key(r)))
    best = by_prec[0]
    loc = dict(best.get("location") or {})
    if loc.get("lat") is not None and not loc.get("source_id"):
        loc["source_id"] = _first_src(best)
    for k in ("local_authority", "postcode"):
        if not loc.get(k):
            loc[k] = next(((r.get("location") or {}).get(k) for r in by_prec if (r.get("location") or {}).get(k)), None)
    loc = {"lat": loc.get("lat"), "lon": loc.get("lon"), "precision": loc.get("precision") or "unknown",
           "source_id": loc.get("source_id"), "local_authority": loc.get("local_authority"),
           "postcode": loc.get("postcode")}
    out["location"] = loc
    pts = [(r, _point(r)) for r in by_prec if _point(r)]
    far = []
    for r, p in pts[1:]:
        thr = THRESHOLD_KM[coarser(_precision(best), _precision(r))]
        d = haversine_km(pts[0][1][0], pts[0][1][1], p[0], p[1])
        if thr is not None and d > thr:
            far.append((r, d))
    if far:
        conflicts.append({"field": "location", "kind": "location_disagreement",
                          "values": [{"value": [round(pts[0][1][1], 6), round(pts[0][1][0], 6)], "source_id": _first_src(pts[0][0])}]
                          + [{"value": [round(_point(r)[1], 6), round(_point(r)[0], 6)], "source_id": _first_src(r)} for r, _ in far],
                          "chosen": [loc["lon"], loc["lat"]],
                          "note": "matched by reference but points are %.2f km apart (max)" % max(d for _, d in far)})

    # refs
    prefs = {}
    for r in recs:
        for p in r.get("planning_refs") or []:
            k = (norm_authority(p["authority"]), norm_ref(p["reference"]))
            prefs.setdefault(k, {"authority": p["authority"], "reference": p["reference"], "url": p.get("url")})
    out["planning_refs"] = [prefs[k] for k in sorted(prefs)]
    out["connection_refs"] = sorted({c for r in recs for c in r.get("connection_refs") or []})
    out["evidence_urls"] = sorted({u for r in recs for u in r.get("evidence_urls") or []})

    # same-source duplicates (two records of one dataset merged by reference)
    by_ds = {}  # dataset -> ids of the INPUT records from it (a lane's own multi-member site counts once)
    for r in recs:
        for ds in sorted(rec_datasets(r)):
            by_ds.setdefault(ds, []).append(_first_src(r))
    for ds, ids in sorted(by_ds.items()):
        if len(set(ids)) > 1:
            conflicts.append({"field": "sources", "kind": "same_source_duplicate",
                              "values": [{"value": ds, "source_id": i} for i in sorted(set(ids))],
                              "chosen": None, "note": "one dataset lists this site more than once"})

    # dedupe conflicts deterministically
    seen = {}
    for c in conflicts:
        seen.setdefault(_canon(c), c)
    out["conflicts"] = [seen[k] for k in sorted(seen)]

    keys = sorted(set().union(*[plan_keys(r) for r in recs]))
    keys = keys or sorted(set().union(*[conn_keys(r) for r in recs]))
    keys = keys or sorted(geo_key(r) for r in recs)
    out["site_key"] = keys[0]
    out["match"] = [{"a": rec_key(recs_a), "b": rec_key(recs_b), "rule": rule,
                     "name_similarity": sim, "distance_km": dist}
                    for recs_a, recs_b, rule, sim, dist in links]
    out["match"].sort(key=lambda m: (m["a"], m["b"], m["rule"]))
    out["notes"] = sorted({n for r in recs for n in r.get("notes") or []})
    return out


def _canon(obj):
    import json
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def merge(records):
    """records -> merged records sorted by id. Input order does not matter."""
    global _URL_OK
    recs = sorted(records, key=rec_key)
    freq = {}
    for r in recs:
        for k in raw_url_keys(r):
            freq[k] = freq.get(k, 0) + 1
    _URL_OK = {k for k, n in freq.items() if n <= URL_KEY_MAX_RECORDS}
    out = []
    for idxs, links in cluster(recs):
        group = [recs[i] for i in idxs]
        lk = [(recs[i], recs[j], rule, sim, dist) for i, j, rule, sim, dist in links]
        out.append(merge_group(group, lk))
    # stable ids; disambiguate identical keys deterministically
    out.sort(key=lambda m: (m["site_key"], rec_key(m)))
    counts = {}
    for m in out:
        k = m["site_key"]
        counts[k] = counts.get(k, 0) + 1
        if counts[k] > 1:
            m["site_key"] = "%s#%d" % (k, counts[k])
        m["id"] = site_id(m["site_key"])
    out.sort(key=lambda m: m["id"])
    return [_ordered_record(m) for m in out]


FIELD_ORDER = ["id", "site_key", "name", "aliases", "operator", "status", "status_source_id",
               "it_load_mw", "grid_connection_mw", "unclassified_mw", "dates", "location",
               "planning_refs", "connection_refs", "evidence_urls", "sources", "conflicts", "match", "notes"]


def _ordered_record(m):
    return {k: m[k] for k in FIELD_ORDER if k in m}


def to_feature(m):
    loc = m["location"]
    geom = None
    if loc.get("lat") is not None:
        geom = {"type": "Point", "coordinates": [loc["lon"], loc["lat"]]}
    return {"type": "Feature", "id": m["id"], "geometry": geom, "properties": m}
