#!/usr/bin/env python3
"""DC-A4: operational UK data centres from OpenStreetMap (ODbL) and Wikidata (CC0).

Standard library only. Subcommands:

  fetch-osm       POST the pinned Overpass query to the pinned endpoint; store the raw JSON + SHA-256.
  fetch-wikidata  GET the pinned SPARQL query from query.wikidata.org; store the raw JSON + SHA-256.
  build           Normalise whatever raw snapshots exist (live Overpass, legacy globalgrid2050
                  datacentres.geojson, Wikidata), de-duplicate into sites, reconcile against the
                  GridAtlas offtakers class C, and write dc_operational.json + dc_operational.geojson
                  + counts.json. Deterministic: same inputs -> byte-identical outputs (no wall clock).
  selftest        Run the build pipeline on small synthetic Overpass + Wikidata fixtures.

Honesty rules baked in:
  * status is "operational" only in the sense "mapped as an existing data centre"; status_basis says so.
  * IT load MW and grid connection MW are separate fields and are null unless a source states which one it
    is. Neither OSM nor Wikidata says which, so any MW found goes to mw_unclassified with its raw tag/property.
  * unknown is null, never 0. OSM placeholder strings ("Unknown Data Centre", "Unknown Operator") become null.
"""
import argparse, hashlib, json, math, os, re, sys, urllib.parse, urllib.request
from collections import Counter, OrderedDict

# ---------------------------------------------------------------- pinned sources
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# UK = OSM relation 62149 -> Overpass area 3600062149 (same area as globalgrid2050 scripts/fetch_datacentres.py).
OVERPASS_QUERY = """[out:json][timeout:300];
area(id:3600062149)->.uk;
(
  nwr["telecom"="data_center"](area.uk);
  nwr["building"~"^data_cent(er|re)$"](area.uk);
  nwr["building:use"~"^data_cent(er|re)$"](area.uk);
  nwr["industrial"~"^data_cent(er|re)$"](area.uk);
);
out center tags;"""

WIKIDATA_URL = "https://query.wikidata.org/sparql"
# Q671224 = data center (class); P31/P279* takes every subclass. Q145 = United Kingdom.
# P2109 = nominal power output, P2791 = power consumed (property semantics for data centres: unverified).
WIKIDATA_QUERY = """SELECT ?item ?itemLabel ?class ?classLabel ?coord ?operator ?operatorLabel ?owner ?ownerLabel
       ?inception ?dissolved ?stateOfUse ?stateOfUseLabel ?website
       ?powerOut ?powerOutUnit ?powerUse ?powerUseUnit WHERE {
  ?item wdt:P31 ?class . ?class wdt:P279* wd:Q671224 .
  ?item wdt:P17 wd:Q145 .
  OPTIONAL { ?item wdt:P625 ?coord }
  OPTIONAL { ?item wdt:P137 ?operator }
  OPTIONAL { ?item wdt:P127 ?owner }
  OPTIONAL { ?item wdt:P571 ?inception }
  OPTIONAL { ?item wdt:P576 ?dissolved }
  OPTIONAL { ?item wdt:P5817 ?stateOfUse }
  OPTIONAL { ?item wdt:P856 ?website }
  OPTIONAL { ?item p:P2109/psv:P2109 [ wikibase:quantityAmount ?powerOut ; wikibase:quantityUnit ?powerOutUnit ] }
  OPTIONAL { ?item p:P2791/psv:P2791 [ wikibase:quantityAmount ?powerUse ; wikibase:quantityUnit ?powerUseUnit ] }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
ORDER BY ?item"""
USER_AGENT = "GridAtlas-DC-pipeline/0.1 (https://github.com/Ventusltd/gridatlas/issues/73)"

LEGACY_GG2050 = dict(repo="https://github.com/Ventusltd/globalgrid2050", path="datacentres.geojson",
                     sha256="a686a2e00253bbf30c9f5262439bc2332524e8d5cb34481529c6a711c73acab4",
                     builder="scripts/fetch_datacentres.py (Overpass: telecom|building=data_center in area 3600062149, out center)",
                     snapshot_date="unknown; present in globalgrid2050 history at da7d8f6e (2026-10-13)")

OSM_LICENCE = dict(name="ODbL 1.0", url="https://opendatacommons.org/licenses/odbl/1-0/",
                   attribution="© OpenStreetMap contributors", attribution_url="https://www.openstreetmap.org/copyright",
                   share_alike="Derived databases must be offered under ODbL; produced works must carry the attribution.",
                   fully_open="yes (ODbL with attribution is on the owner's list)")
WD_LICENCE = dict(name="CC0 1.0", url="https://creativecommons.org/publicdomain/zero/1.0/",
                  attribution="Wikidata (CC0; attribution not required, given as courtesy)",
                  attribution_url="https://www.wikidata.org/wiki/Wikidata:Licensing", fully_open="yes")

UNIT_TO_MW = {"http://www.wikidata.org/entity/Q6982035": 1.0,       # megawatt
              "http://www.wikidata.org/entity/Q25250": 1e-6}        # watt
UK_BBOX = (-8.75, 49.8, 1.9, 61.0)
SITE_RADIUS_M = 250      # same grouping radius as the offtakers filter (ot_filter.py step 4)
XSRC_RADIUS_M = 500      # OSM <-> Wikidata match radius
PLACEHOLDER = re.compile(r"^\s*(unknown( data cent(er|re)| operator)?|n/?a|none|-)?\s*$", re.I)
CABLE = re.compile(r"cable landing|landing station", re.I)
MW_TAG = re.compile(r"(^|:)(power|capacity|electricity|output|it_load|load)(:|$)", re.I)


# ---------------------------------------------------------------- helpers
def sha256_bytes(b): return hashlib.sha256(b).hexdigest()
def sha256_file(p):
    with open(p, "rb") as fh: return sha256_bytes(fh.read())
def clean(s):
    if s is None: return None
    s = str(s).strip()
    return None if PLACEHOLDER.match(s) else s
def norm(s): return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
def tokens(s): return {t for t in norm(s).split() if t not in {"data", "centre", "center", "ltd", "limited", "the", "dc", "uk", "plc"} and len(t) > 1}
def hav_m(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))
def in_uk(lon, lat): return UK_BBOX[0] <= lon <= UK_BBOX[2] and UK_BBOX[1] <= lat <= UK_BBOX[3]
def dump(obj, path):
    data = (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=False) + "\n").encode("utf-8")
    with open(path, "wb") as fh: fh.write(data)
    return sha256_bytes(data)
def nation(lon, lat):
    # coarse geometric fallback, always flagged approximate (same rule as ot_filter.py)
    if lon < -5.3 and 53.9 < lat < 55.4: return "Northern Ireland (approx)"
    if lat > 55.0 and not (lon > -2.5 and lat < 55.82): return "Scotland (approx)"
    if lon < -2.65 and 51.35 < lat < 53.45 and not (lon < -4.6 and lat < 51.75): return "Wales (approx)"
    return "England (approx)"


# ---------------------------------------------------------------- fetchers
def _http(req, out_dir, stem, meta):
    os.makedirs(out_dir, exist_ok=True)
    try:
        with urllib.request.urlopen(req, timeout=360) as r: body = r.read(); status = r.status
    except Exception as e:  # recorded, not hidden
        meta.update(ok=False, error=f"{type(e).__name__}: {e}")
        dump(meta, os.path.join(out_dir, stem + ".fetch.json")); print("FETCH FAILED:", meta["error"]); return 2
    p = os.path.join(out_dir, stem + ".json")
    with open(p, "wb") as fh: fh.write(body)
    meta.update(ok=status == 200, http_status=status, bytes=len(body), sha256=sha256_bytes(body), raw=os.path.basename(p))
    dump(meta, os.path.join(out_dir, stem + ".fetch.json")); print("saved", p, meta["sha256"]); return 0

def fetch_osm(a):
    body = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=body, headers={"User-Agent": USER_AGENT})
    meta = OrderedDict(source="OpenStreetMap via Overpass", endpoint=OVERPASS_URL, query=OVERPASS_QUERY,
                       query_sha256=sha256_bytes(OVERPASS_QUERY.encode()), licence=OSM_LICENCE, retrieved=a.retrieved)
    return _http(req, a.raw, "osm_overpass", meta)

def fetch_wikidata(a):
    url = WIKIDATA_URL + "?" + urllib.parse.urlencode({"query": WIKIDATA_QUERY, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"})
    meta = OrderedDict(source="Wikidata Query Service", endpoint=WIKIDATA_URL, query=WIKIDATA_QUERY,
                       query_sha256=sha256_bytes(WIKIDATA_QUERY.encode()), licence=WD_LICENCE, retrieved=a.retrieved)
    return _http(req, a.raw, "wikidata_sparql", meta)


# ---------------------------------------------------------------- loaders -> source records
def osm_records(raw_path, retrieved):
    d = json.load(open(raw_path, encoding="utf-8"))
    out = []
    for e in d.get("elements", []):
        t = e.get("tags") or {}
        if e["type"] == "node": lon, lat, prec = e.get("lon"), e.get("lat"), "point: OSM node"
        elif "center" in e: lon, lat, prec = e["center"]["lon"], e["center"]["lat"], f"centroid of OSM {e['type']} (Overpass out center; footprint not kept)"
        else: continue
        if lon is None or not in_uk(lon, lat): continue
        mw = [dict(tag=k, raw=v, flag="unclassified: OSM tag does not say IT load or grid connection")
              for k, v in sorted(t.items()) if MW_TAG.search(k) and re.search(r"\d", v) and re.search(r"w\b|watt", v, re.I)]
        matched = sorted(k + "=" + t[k] for k in ("telecom", "building", "building:use", "industrial") if k in t and re.match(r"data_cent", t[k]))
        lifecycle = sorted(k for k in t if re.match(r"(disused|abandoned|demolished|construction|proposed|was|razed)(:|$)", k)
                           or (k in ("disused", "abandoned") and t[k] == "yes"))
        out.append(dict(src="osm", sid=f"osm:{e['type']}/{e['id']}", name=clean(t.get("name")),
                        operator=clean(t.get("operator")), brand=clean(t.get("brand")), lon=round(lon, 7), lat=round(lat, 7),
                        precision=prec, evidence_url=f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                        start_date=t.get("start_date"), mw=mw, matched_tags=matched, lifecycle_tags=lifecycle,
                        website=t.get("website") or t.get("contact:website"), retrieved=retrieved))
    return out

def legacy_records(path):
    h = sha256_file(path)
    d = json.load(open(path, encoding="utf-8"))
    out = []
    for i, f in enumerate(d["features"]):
        p = f.get("properties") or {}; c = (f.get("geometry") or {}).get("coordinates")
        if not c or not in_uk(c[0], c[1]): continue
        out.append(dict(src="osm_legacy", sid=f"gg2050-dc-{i}", offtakers_ref=f"osm_dc-{i}", name=clean(p.get("name")),
                        operator=clean(p.get("operator")), brand=None, lon=round(c[0], 7), lat=round(c[1], 7),
                        precision="point: OSM node or footprint centre (element type and id not kept by the globalgrid2050 builder)",
                        evidence_url=None, start_date=None, mw=[], matched_tags=["telecom|building=data_center (builder query; per-element tag not kept)"],
                        lifecycle_tags=[], website=None, retrieved=LEGACY_GG2050["snapshot_date"], file_sha256=h))
    return out

def wd_records(raw_path, retrieved):
    d = json.load(open(raw_path, encoding="utf-8"))
    items = OrderedDict()
    for b in d["results"]["bindings"]:
        v = {k: x["value"] for k, x in b.items()}
        q = v["item"].rsplit("/", 1)[-1]
        it = items.setdefault(q, dict(src="wikidata", sid=f"wd:{q}", name=None, operator=set(), owner=set(), classes=set(),
                                      coords=set(), inception=set(), dissolved=set(), state=set(), website=set(), mw=[], _seen=set()))
        if v.get("itemLabel") and v["itemLabel"] != q: it["name"] = v["itemLabel"]
        for k, key in (("operatorLabel", "operator"), ("ownerLabel", "owner"), ("classLabel", "classes"), ("coord", "coords"),
                       ("inception", "inception"), ("dissolved", "dissolved"), ("stateOfUseLabel", "state"), ("website", "website")):
            if v.get(k): it[key].add(v[k])
        for amt, unit, prop in (("powerOut", "powerOutUnit", "P2109 nominal power output"), ("powerUse", "powerUseUnit", "P2791 power consumed")):
            if v.get(amt):
                key = (prop, v[amt], v.get(unit))
                if key in it["_seen"]: continue
                it["_seen"].add(key)
                f = UNIT_TO_MW.get(v.get(unit))
                it["mw"].append(dict(property=prop, raw=v[amt], unit=v.get(unit), mw=(float(v[amt]) * f if f else None),
                                     flag="unclassified: Wikidata does not say IT load or grid connection"))
    out = []
    for q, it in items.items():
        pts = []
        for c in sorted(it["coords"]):
            m = re.match(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", c)
            if m: pts.append((float(m.group(1)), float(m.group(2))))
        lon, lat = pts[0] if pts else (None, None)
        out.append(dict(src="wikidata", sid=it["sid"], name=clean(it["name"]),
                        operator=clean("; ".join(sorted(it["operator"]))) if it["operator"] else None, brand=None,
                        owner=sorted(it["owner"]), classes=sorted(it["classes"]), lon=lon, lat=lat,
                        coord_conflict=len(pts) > 1,
                        precision="point: Wikidata P625 (precision as entered; often approximate)" if pts else "none: no P625 coordinate",
                        evidence_url=f"https://www.wikidata.org/wiki/{q}", start_date=min(it["inception"]) if it["inception"] else None,
                        dissolved=sorted(it["dissolved"]), state_of_use=sorted(it["state"]), mw=it["mw"],
                        website=min(it["website"]) if it["website"] else None, lifecycle_tags=[], retrieved=retrieved))
    return out


# ---------------------------------------------------------------- de-duplication
def op_compatible(a, b):
    return a is None or b is None or norm(a) == norm(b) or bool(tokens(a) & tokens(b))

def group_osm(recs):
    """Greedy, deterministic (sorted by sid): attach to the first site whose seed is within SITE_RADIUS_M and whose
    operator is compatible (same rule as ot_filter.py step 4, so legacy grouping is comparable with the overlay)."""
    sites = []
    for r in sorted(recs, key=lambda r: (r["src"], r["sid"] if r["src"] == "osm" else int(r["sid"].rsplit("-", 1)[1]))):
        g = None
        for s in sites:
            if hav_m((s["lon"], s["lat"]), (r["lon"], r["lat"])) <= SITE_RADIUS_M and op_compatible(s["operator"], r["operator"]):
                g = s; break
        if g is None:
            sites.append(dict(lon=r["lon"], lat=r["lat"], name=r["name"], operator=r["operator"], members=[r]))
        else:
            g["members"].append(r)
            if g["name"] is None and r["name"]: g["name"] = r["name"]
            if g["operator"] is None and r["operator"]: g["operator"] = r["operator"]
    return sites

def match_wikidata(sites, wds):
    """Attach each Wikidata item to the nearest OSM site within XSRC_RADIUS_M whose name or operator shares a token;
    otherwise it becomes its own site. Wikidata items without coordinates are listed, never placed."""
    unplaced, added = [], []
    for w in wds:
        if w["lon"] is None: unplaced.append(w); continue
        best = None
        for s in sites:
            d = hav_m((s["lon"], s["lat"]), (w["lon"], w["lat"]))
            if d > XSRC_RADIUS_M: continue
            names = {x["name"] for x in s["members"] if x["name"]} | {x["operator"] for x in s["members"] if x["operator"]}
            ok = any(tokens(n) & (tokens(w["name"]) | tokens(w["operator"])) for n in names)
            if ok and (best is None or d < best[0]): best = (d, s)
        if best: best[1]["members"].append(dict(w, match_distance_m=round(best[0], 1)))
        else: added.append(dict(lon=w["lon"], lat=w["lat"], name=w["name"], operator=w["operator"], members=[w]))
    return sites + added, unplaced


# ---------------------------------------------------------------- normalise to the #73 schema
def source_entry(m):
    lic = WD_LICENCE if m["src"] == "wikidata" else OSM_LICENCE
    e = OrderedDict(source={"osm": "OpenStreetMap (Overpass)", "osm_legacy": "OpenStreetMap via globalgrid2050 datacentres.geojson",
                            "wikidata": "Wikidata"}[m["src"]],
                    source_id=m["sid"], name=m["name"], operator=m["operator"], lon=m["lon"], lat=m["lat"],
                    location_precision=m["precision"], evidence_url=m["evidence_url"], retrieved=m["retrieved"],
                    licence=lic["name"], licence_url=lic["url"], attribution=lic["attribution"], attribution_url=lic["attribution_url"])
    if m["src"] == "osm_legacy": e.update(offtakers_ref=m["offtakers_ref"], snapshot_file=LEGACY_GG2050["repo"] + "/blob/main/" + LEGACY_GG2050["path"], snapshot_sha256=m["file_sha256"])
    if m.get("match_distance_m") is not None: e["match_distance_m"] = m["match_distance_m"]
    if m["src"] == "osm": e["matched_tags"] = m["matched_tags"]
    return e

def build_site(s, idx):
    ms = s["members"]
    srcs = sorted({m["src"] for m in ms})
    names = sorted({m["name"] for m in ms if m["name"]}); ops = sorted({m["operator"] for m in ms if m["operator"]})
    conflicts = []
    if len({norm(n) for n in names}) > 1: conflicts.append(dict(field="name", values=names,
        note="several names inside one grouped site: a multi-building campus, or two operators merged because one operator is unknown; check"))
    if len({norm(o) for o in ops}) > 1: conflicts.append(dict(field="operator", values=ops))
    lifecycle = sorted({t for m in ms for t in m.get("lifecycle_tags", [])})
    dissolved = sorted({d for m in ms for d in m.get("dissolved", [])}); state = sorted({x for m in ms for x in m.get("state_of_use", [])})
    if lifecycle: conflicts.append(dict(field="status", values=["OSM lifecycle tags: " + ", ".join(lifecycle)]))
    if dissolved or state: conflicts.append(dict(field="status", values=[f"Wikidata dissolved={dissolved} state_of_use={state}"]))
    for m in ms:
        if m.get("coord_conflict"): conflicts.append(dict(field="location", values=[f"{m['sid']} has several P625 values"]))
    cable = any(CABLE.search(m["name"] or "") for m in ms)
    mw = [dict(m2, source_id=m["sid"]) for m in ms for m2 in m["mw"]]
    lon = round(sum(m["lon"] for m in ms if m["lon"] is not None) / sum(1 for m in ms if m["lon"] is not None), 6)
    lat = round(sum(m["lat"] for m in ms if m["lat"] is not None) / sum(1 for m in ms if m["lat"] is not None), 6)
    precs = sorted({m["precision"] for m in ms})
    kind = "cable_landing_station" if cable else ("data_centre_campus" if sum(1 for m in ms if m["src"] != "wikidata") >= 2 else "data_centre")
    return OrderedDict(
        id=f"DC-OW-{idx:04d}",
        name=s["name"], operator=s["operator"], operator_alternatives=[o for o in ops if o != s["operator"]],
        site_kind=kind,
        status="operational",
        status_basis=("mapped in OpenStreetMap/Wikidata as an existing data centre; not independently verified, "
                      "no commissioning or energisation evidence; not a pipeline entry"),
        it_load_mw=OrderedDict(value=None, basis="unknown: no open source states IT load for this site"),
        grid_connection_mw=OrderedDict(value=None, basis="unknown: no open source states a grid connection capacity for this site"),
        mw_unclassified=mw,
        dates=OrderedDict(status_date=None, start_date=min((m["start_date"] for m in ms if m.get("start_date")), default=None),
                          first_seen_in_source=None),
        lon=lon, lat=lat, nation=nation(lon, lat),
        location_precision=("site = %d source object(s) within %d m; " % (len(ms), SITE_RADIUS_M)) + " | ".join(precs),
        evidence_url=next((m["evidence_url"] for m in ms if m["evidence_url"]), None),
        sources=[source_entry(m) for m in sorted(ms, key=lambda m: (m["src"], m["sid"]))],
        source_names=srcs,
        licence="ODbL 1.0 (OSM-derived database; share-alike)" if any(x.startswith("osm") for x in srcs) else "CC0 1.0",
        attribution="© OpenStreetMap contributors (ODbL)" if any(x.startswith("osm") for x in srcs) else "Wikidata (CC0)",
        conflicts=conflicts,
        offtakers_reconciliation=None,
        screening_note="operational-site list from open data: not a demand, contract, capacity or connection statement",
    )


# ---------------------------------------------------------------- reconciliation with GridAtlas offtakers class C
def reconcile(sites, offtakers_path, legacy_used):
    d = json.load(open(offtakers_path, encoding="utf-8"))
    C = [f for f in d["features"] if f["properties"]["class"] == "C"]
    ref_to_ot = {}
    for f in C:
        p = f["properties"]
        for r in p.get("records", []) + p.get("osm_corroboration", []):
            if r.get("source") == "OSM": ref_to_ot[r["id"]] = p["id"]
    ot_by_id = {f["properties"]["id"]: f for f in C}
    hit_ot = Counter()
    for s in sites:
        refs = sorted({src["source_id"].replace("gg2050-dc-", "osm_dc-") for src in s["sources"] if src["source_id"].startswith("gg2050-dc-")})
        ots = sorted({ref_to_ot[r] for r in refs if r in ref_to_ot})
        how = "exact legacy ref (osm_dc-N = feature N of the same globalgrid2050 file)"
        if not ots:  # live OSM or Wikidata: nearest class C within 250 m
            best = min(((hav_m((s["lon"], s["lat"]), f["geometry"]["coordinates"]), f["properties"]["id"]) for f in C), default=None)
            if best and best[0] <= SITE_RADIUS_M: ots = [best[1]]; how = f"nearest class C within {SITE_RADIUS_M} m ({best[0]:.0f} m)"
        for o in ots: hit_ot[o] += 1
        if ots:
            s["offtakers_reconciliation"] = OrderedDict(offtakers_ids=ots, match=how,
                                                        offtakers_primary=[("NAEI" if ot_by_id[o]["properties"]["records"][0]["source"] == "NAEI" else "OSM") for o in ots])
        else:
            s["offtakers_reconciliation"] = OrderedDict(offtakers_ids=[], match="not in offtakers class C" + (
                " (cable landing station: excluded by ot_filter.py)" if s["site_kind"] == "cable_landing_station" else ""))
    missing = []
    for f in C:
        p = f["properties"]
        if p["id"] not in hit_ot:
            missing.append(OrderedDict(offtakers_id=p["id"], name=p["name"], primary=p["records"][0]["source"],
                                       has_osm=bool([r for r in p["records"] + p.get("osm_corroboration", []) if r["source"] == "OSM"])))
    split = {o: n for o, n in hit_ot.items() if n > 1}
    return dict(offtakers_file=os.path.basename(offtakers_path), offtakers_sha256=sha256_file(offtakers_path),
                offtakers_class_c=len(C),
                class_c_osm_primary=sum(1 for f in C if f["properties"]["records"][0]["source"] == "OSM"),
                class_c_naei_primary=sum(1 for f in C if f["properties"]["records"][0]["source"] == "NAEI"),
                class_c_matched=len(hit_ot), class_c_unmatched=missing,
                class_c_split_across_sites=split)


# ---------------------------------------------------------------- build
def build(a):
    recs, inputs = [], OrderedDict()
    osm_raw = os.path.join(a.raw, "osm_overpass.json")
    if os.path.exists(osm_raw):
        recs += osm_records(osm_raw, a.retrieved); inputs["osm_overpass"] = sha256_file(osm_raw); legacy = False
    else:
        if not a.legacy or not os.path.exists(a.legacy): sys.exit("no live Overpass snapshot and no --legacy file")
        h = sha256_file(a.legacy)
        if h != LEGACY_GG2050["sha256"]: print("WARNING: legacy file hash differs from pinned", h)
        recs += legacy_records(a.legacy); inputs["gg2050_datacentres.geojson"] = h; legacy = True
    wd_raw = os.path.join(a.raw, "wikidata_sparql.json")
    wds = []
    if os.path.exists(wd_raw): wds = wd_records(wd_raw, a.retrieved); inputs["wikidata_sparql"] = sha256_file(wd_raw)
    n_in = len(recs)
    sites = group_osm(recs)
    sites, wd_unplaced = match_wikidata(sites, wds)
    sites.sort(key=lambda s: (round(s["lat"], 6), round(s["lon"], 6), s["members"][0]["sid"]))
    out = [build_site(s, i) for i, s in enumerate(sites)]
    rec = reconcile(out, a.offtakers, legacy) if a.offtakers else None
    if a.offtakers: inputs[os.path.basename(a.offtakers)] = rec["offtakers_sha256"]
    kinds = Counter(s["site_kind"] for s in out)
    counts = OrderedDict(
        inputs=inputs, mode="legacy globalgrid2050 snapshot (Overpass and Wikidata unreachable from this session)" if legacy else "live Overpass",
        source_objects=OrderedDict(osm=sum(1 for r in recs if r["src"] in ("osm", "osm_legacy")), wikidata_items=len(wds),
                                   wikidata_without_coords=len(wd_unplaced)),
        sites_total=len(out), sites_by_kind=dict(sorted(kinds.items())),
        operational_data_centre_sites_excl_cable_landing=sum(n for k, n in kinds.items() if k != "cable_landing_station"),
        duplicates_merged=n_in + len(wds) - len(wd_unplaced) - len(out),
        sites_multi_source=sum(1 for s in out if len(s["source_names"]) > 1),
        sites_with_conflicts=sum(1 for s in out if s["conflicts"]),
        sites_with_name=sum(1 for s in out if s["name"]), sites_with_operator=sum(1 for s in out if s["operator"]),
        sites_with_it_load_mw=sum(1 for s in out if s["it_load_mw"]["value"] is not None),
        sites_with_grid_connection_mw=sum(1 for s in out if s["grid_connection_mw"]["value"] is not None),
        sites_with_unclassified_mw=sum(1 for s in out if s["mw_unclassified"]),
        reconciliation=rec, params=dict(site_radius_m=SITE_RADIUS_M, cross_source_radius_m=XSRC_RADIUS_M))
    os.makedirs(a.out, exist_ok=True)
    doc = OrderedDict(schema="gridatlas-dc-pipeline/0.1 (issue #73 step 3)", generated_from=inputs,
                      licence=OSM_LICENCE if any(r["src"].startswith("osm") for r in recs) else WD_LICENCE,
                      notes=["status 'operational' = mapped as existing; not verified, not a commitment",
                             "it_load_mw and grid_connection_mw are never derived from each other; null = unknown, not zero",
                             "Wikidata items without coordinates are listed in wikidata_unplaced, not mapped"],
                      records=out, wikidata_unplaced=[source_entry(w) for w in wd_unplaced])
    h1 = dump(doc, os.path.join(a.out, "dc_operational.json"))
    gj = dict(type="FeatureCollection", attribution="© OpenStreetMap contributors (ODbL); Wikidata (CC0)", features=[
        dict(type="Feature", geometry=dict(type="Point", coordinates=[s["lon"], s["lat"]]),
             properties={k: v for k, v in s.items() if k not in ("lon", "lat")}) for s in out])
    h2 = dump(gj, os.path.join(a.out, "dc_operational.geojson"))
    counts["outputs"] = {"dc_operational.json": h1, "dc_operational.geojson": h2}
    dump(counts, os.path.join(a.out, "counts.json"))
    print(json.dumps({k: v for k, v in counts.items() if k != "reconciliation"}, indent=1))
    if rec: print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in rec.items()}, indent=1))
    return 0


# ---------------------------------------------------------------- selftest
def selftest(a):
    import tempfile
    os.makedirs(a.out, exist_ok=True); t = tempfile.mkdtemp(prefix="dca4-selftest-", dir=a.out); raw = os.path.join(t, "raw"); os.makedirs(raw)
    json.dump({"elements": [
        {"type": "way", "id": 2, "center": {"lat": 51.5000, "lon": -0.0100}, "tags": {"building": "data_center", "operator": "Equinix", "name": "LD8"}},
        {"type": "node", "id": 1, "lat": 51.5005, "lon": -0.0102, "tags": {"telecom": "data_center", "operator": "Equinix", "name": "LD8 Docklands", "power": "40 MW"}},
        {"type": "way", "id": 3, "center": {"lat": 51.5010, "lon": -0.0110}, "tags": {"building": "data_centre", "operator": "Telehouse"}},
        {"type": "node", "id": 4, "lat": 50.1, "lon": -5.6, "tags": {"telecom": "data_center", "name": "Porthcurno Cable Landing Station"}},
        {"type": "node", "id": 5, "lat": 40.0, "lon": -3.0, "tags": {"telecom": "data_center"}}]}, open(os.path.join(raw, "osm_overpass.json"), "w"))
    json.dump({"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "itemLabel": {"value": "Equinix LD8"}, "coord": {"value": "Point(-0.0105 51.5003)"},
         "operatorLabel": {"value": "Equinix"}, "powerOut": {"value": "40"}, "powerOutUnit": {"value": "http://www.wikidata.org/entity/Q6982035"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q2"}, "itemLabel": {"value": "Far DC"}, "coord": {"value": "Point(-2 53)"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q3"}, "itemLabel": {"value": "No coords DC"}}]}}, open(os.path.join(raw, "wikidata_sparql.json"), "w"))
    ns = argparse.Namespace(raw=raw, out=os.path.join(t, "out"), legacy=None, offtakers=None, retrieved="selftest")
    build(ns)
    d = json.load(open(os.path.join(t, "out", "dc_operational.json")))
    R = d["records"]
    eq = [r for r in R if r["operator"] == "Equinix"]
    assert len(eq) == 1 and len(eq[0]["sources"]) == 3, eq                       # way + node + wikidata merged
    assert eq[0]["it_load_mw"]["value"] is None and eq[0]["grid_connection_mw"]["value"] is None
    assert len(eq[0]["mw_unclassified"]) == 2 and all("unclassified" in m["flag"] for m in eq[0]["mw_unclassified"])
    assert any(c["field"] == "name" for c in eq[0]["conflicts"])
    assert any(r["operator"] == "Telehouse" for r in R)                          # different operator, 100 m away: kept apart
    assert sum(1 for r in R if r["site_kind"] == "cable_landing_station") == 1
    assert not any(r["lat"] < 49 for r in R)                                      # out-of-UK dropped
    assert len(d["wikidata_unplaced"]) == 1 and len(R) == 4
    h = sha256_file(os.path.join(t, "out", "dc_operational.json")); build(ns)
    assert h == sha256_file(os.path.join(t, "out", "dc_operational.json")), "not deterministic"
    print("SELFTEST OK", t); return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch-osm", "fetch-wikidata", "build", "selftest"])
    ap.add_argument("--raw", default="raw"); ap.add_argument("--out", default="out")
    ap.add_argument("--legacy", default=None, help="globalgrid2050 datacentres.geojson (fallback when Overpass is unreachable)")
    ap.add_argument("--offtakers", default=None, help="gridatlas data/offtakers_filtered_*.geojson for class C reconciliation")
    ap.add_argument("--retrieved", default="unspecified", help="retrieval date stamp written into records (kept out of the clock for determinism)")
    a = ap.parse_args()
    return {"fetch-osm": fetch_osm, "fetch-wikidata": fetch_wikidata, "build": build, "selftest": selftest}[a.cmd](a)

if __name__ == "__main__":
    sys.exit(main())
