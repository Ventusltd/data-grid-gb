#!/usr/bin/env python3
"""DC-A6 (issue #73 step 5): open UK CONVENTIONAL power assets for the Grid Engine's 30-nearest query.

Standard library only, deterministic (no clock, sorted output, fixed float rounding). Run with `python3 -I`.

Inputs (all pinned by sha256; the build refuses any other bytes):
  offtakers  GridAtlas data/offtakers_filtered_202610290945.geojson, class B "Power station" (358 sites).
             Built by OT-filter + R-otdata: NAEI 2023 point sources (OGL v3.0), OSM power=plant (ODbL 1.0),
             WRI GPPD v1.3.0 (CC BY 4.0), with R-otdata's verified closure/closing/conversion statuses.
  gppd       WRI Global Power Plant Database v1.3.0 CSV (CC BY 4.0). Only GBR rows whose `source` is NOT the
             UK REPD and whose fuel is thermal/nuclear are used (84 rows; 73 cite BEIS DUKES).
  status     R-otdata out/status_curated.csv (each row's page fetched 29 Oct 2026), applied to GPPD rows that
             the offtakers class does not already carry.
  repd       GridAtlas data/repd_browser_registry_202608290716.json (REPD Q2 2026, DESNZ, OGL v3.0), used ONLY
             for the exclusion rule below; no REPD row is ever copied into this set.

REPD rule (no double counting with the Grid Engine's 30-nearest REPD list):
  A site whose current fuel is renewable-scope (biomass, waste/EfW, landfill gas, biogas) is EXCLUDED when a REPD
  bioenergy/EfW record (Biomass (dedicated|co-firing), EfW Incineration, Landfill Gas, Anaerobic Digestion, Sewage
  Sludge Digestion, Advanced Conversion Technologies) lies within 1 km and is in the same technology group (thermal:
  biomass/EfW/ACT; gas: landfill/AD/sewage), or lies within 5 km and shares a distinctive name token (REPD points are
  often postcode-level, so a 2-3 km offset for the same plant is common). A record of the other group counts only within
  0.5 km with a shared name token (e.g. an NAEI 'EfW' row that is a REPD landfill-gas scheme of the same name). For a site that is operating/unknown/converted only an OPERATIONAL REPD record counts
  (a planned REPD project at the same place is a different asset). The excluded sites are listed with the REPD ref in excluded_repd.json. A renewable-scope site with no
  such REPD record is KEPT and flagged repd_rule="renewable-scope, no REPD match" (it may be below the REPD
  threshold, closed and so absent from the 4-status registry, or a coordinate miss). Fossil, nuclear and
  fossil-CHP sites are never excluded (REPD carries none).
"""
import csv, hashlib, json, math, re, sys, os, collections

HERE = os.path.dirname(os.path.abspath(__file__))
HARD = os.path.dirname(os.path.dirname(HERE))
GA = "/home/claude/gridatlas/data"
INPUTS = {
    "offtakers": (os.path.join(GA, "offtakers_filtered_202610290945.geojson"),
                  "6fc20e04fdcaadc55d723354848bcf77d3871e9d088a29cbf7ea3a76890b6730"),
    "gppd": (os.path.join(HARD, "evidence/OT-filter/data/gppd/gppd.csv"),
             "4b1f93e0fd93664f18684d9b05d0a52ed9658c6a8cf0d21ff2520791379ba7fc"),
    "status": (os.path.join(HARD, "evidence/R-otdata/out/status_curated.csv"),
               "04c8f83acd5f8e14a2e4f8a28bc04535b3951bf21e8ce13c11e74a23ecdf58a6"),
    "repd": (os.path.join(GA, "repd_browser_registry_202608290716.json"),
             "c8a5c59be878c52014a272eb0e4d09af06a0d301d10a8d6b5d0b116b5d1bb6bc"),
}
GENERATION = "DC-A6-202610292000"
OUT = os.path.join(HERE, "out")
# data-grid-gb vendoring (DC-A8): inputs and output dir may be redirected; the sha256 pins above are unchanged.
if os.environ.get("DC_A6_INPUT_DIR"):
    INPUTS = {k: (os.path.join(os.environ["DC_A6_INPUT_DIR"], os.path.basename(p)), h) for k, (p, h) in INPUTS.items()}
OUT = os.environ.get("DC_A6_OUT", OUT)

LIC = {
    "NAEI": ("NAEI point sources 2023 (Defra/DESNZ)", "https://naei.energysecurity.gov.uk/data/maps/emissions-point-sources",
             "OGL v3.0: 'published by Defra, DESNZ and the Devolved Governments under the Open Government Licence'"),
    "OSM": ("OpenStreetMap power=plant", "https://www.openstreetmap.org/copyright",
            "ODbL 1.0, (c) OpenStreetMap contributors (attribution + share-alike)"),
    "GPPD": ("WRI Global Power Plant Database v1.3.0", "https://github.com/wri/global-power-plant-database",
             "CC BY 4.0: 'available ... under a Creative Commons-Attribution 4.0 (CC BY 4.0) license'"),
    "STATUS": ("R-otdata verified status table (29 Oct 2026)", None, "facts cited from the per-row source URL"),
}
BIO_REPD = {"Biomass (dedicated)", "Biomass (co-firing)", "EfW Incineration", "Landfill Gas", "Anaerobic Digestion",
            "Sewage Sludge Digestion", "Advanced Conversion Technologies"}
RENEWABLE_SCOPE = {"biomass", "waste", "landfill_gas", "biogas"}
THERMAL_GROUP = {"Biomass (dedicated)", "Biomass (co-firing)", "EfW Incineration", "Advanced Conversion Technologies"}
GAS_GROUP = {"Landfill Gas", "Anaerobic Digestion", "Sewage Sludge Digestion"}
COMPAT = {"biomass": THERMAL_GROUP, "waste": THERMAL_GROUP, "landfill_gas": GAS_GROUP, "biogas": GAS_GROUP}
NEAR_KM, NAME_KM, CROSS_KM = 1.0, 5.0, 0.5
STOP = set("""power station stations plant plants energy biomass limited ltd renewable renewables works site generation
generating centre center facility road unit units phase the park recovery waste efw chp heat combined and of uk
plc company services service gas fired thermal electricity electric industrial estate lane street new north south
east west great green project scheme district facility""".split())


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hav_km(lat1, lon1, lat2, lon2):
    r = math.radians
    a = math.sin(r(lat2 - lat1) / 2) ** 2 + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) >= 4 and t not in STOP}


def fuel_tech(name, operator, subclass, gppd_fuels, osm_fuels):
    """Return (fuel, technology, basis). Never guesses a fuel the sources do not state."""
    n = (name or "").lower()
    g = [f.lower() for f in gppd_fuels]
    if subclass == "nuclear":
        if "hinkley point c" in n:
            return "nuclear", "nuclear (EPR, under construction)", "NAEI construction-site row; R-otdata"
        return "nuclear", ("nuclear (PWR)" if "sizewell b" in n else "nuclear (AGR)"), "subclass + site name"
    if subclass == "coal":
        return "coal", "coal steam", "GPPD fuel=Coal"
    if subclass == "oil":
        return "oil", "oil (GT/engine/diesel split not stated)", "GPPD fuel=Oil"
    if subclass == "biomass":
        return "biomass", "biomass steam", "subclass"
    if subclass == "energy_from_waste":
        return "waste", "EfW", "subclass"
    if subclass == "landfill_biogas":
        return ("biogas" if re.search(r"digest|biogas|sewage", n) else "landfill_gas"), "gas engines (landfill/biogas)", "subclass + name"
    if subclass == "chp_energy_centre":
        if "cogeneration" in g or "gas" in g or "gas" in osm_fuels:
            return "gas", "CHP", "subclass + GPPD/OSM fuel"
        return "unstated", "CHP (fuel not stated)", "subclass"
    if subclass == "gas_ccgt_large":
        return "gas", "CCGT", "subclass (NAEI/GPPD large gas)"
    if re.search(r"\bdiesel\b", n):
        return "diesel", "diesel engines", "name says diesel"
    if re.search(r"\bocgt\b|\bgt\b|gas turbine", n):
        return "gas", "OCGT", "name says GT/OCGT"
    if re.search(r"\bccgt\b", n):
        return "gas", "CCGT", "name says CCGT"
    if subclass == "peaking_reserve":
        if "gas" in g or "gas" in osm_fuels:
            return "gas", "peaking/reserve (engines or OCGT; type not stated)", "subclass + GPPD/OSM fuel=gas"
        return "unstated", "peaking/reserve (engines or OCGT; fuel not stated)", "subclass"
    if subclass == "gas_other":
        return "gas", "gas (CCGT/OCGT/engines not stated)", "GPPD/OSM fuel=gas"
    if subclass == "thermal_unspecified":
        if "gas" in g or "cogeneration" in g:
            return "gas", "gas (CCGT/OCGT/engines not stated)", "GPPD fuel"
        if "oil" in g:
            return "oil", "oil", "GPPD fuel"
        return "unstated", "thermal (fuel and type not stated)", "NAEI power-producer sector only"
    return "unstated", "thermal (fuel and type not stated)", "fallback"


def map_status(st, date):
    s = (st or "").lower()
    if s.startswith("operating 2023"):
        return "operational", "2023", "reported emissions to NAEI 2023 point sources; a later closure is not captured unless curated"
    if s.startswith("status unknown"):
        return "unknown", None, "listed in GPPD v1.3.0/OSM only, not in NAEI 2023 point sources; unknown is not zero and not closed"
    if s.startswith("construction"):
        return "construction", date, "NAEI 2023 construction-site row (R-otdata)"
    if s in ("closed", "closing", "converted"):
        return s, date, "R-otdata verified table (page fetched 29 Oct 2026)"
    return "unknown", None, "unmapped: " + st


def main():
    for k, (p, h) in INPUTS.items():
        got = sha256(p)
        if got != h:
            sys.exit(f"input {k} sha256 {got} != pinned {h}")
    ot = json.load(open(INPUTS["offtakers"][0]))
    B = [f for f in ot["features"] if f["properties"].get("class") == "B"]
    gppd = [r for r in csv.DictReader(open(INPUTS["gppd"][0], newline="", encoding="utf-8"))
            if r["country"] == "GBR" and r["source"] != "UK Renewable Energy Planning Database"
            and r["primary_fuel"] in ("Gas", "Coal", "Oil", "Nuclear", "Biomass", "Waste", "Cogeneration", "Petcoke", "Other")]
    gppd_by_id = {r["gppd_idnr"]: r for r in gppd}
    status_rows = [r for r in csv.DictReader(l for l in open(INPUTS["status"][0], newline="") if not l.startswith("#"))]
    repd = [r for r in json.load(open(INPUTS["repd"][0]))["records"] if r.get("repd_technology") in BIO_REPD
            and r.get("latitude") is not None and r.get("longitude") is not None]

    sites = []
    seen_gppd = set()
    for f in B:
        p = f["properties"]
        recs = p["records"]
        lon, lat = f["geometry"]["coordinates"]
        srcs = sorted({("OSM" if r["source"].startswith("OSM") else r["source"]) for r in recs})
        first = recs[0]["source"]
        prec = {"NAEI": "site point as reported to NAEI (single point per installation)",
                "GPPD": "GPPD point (geolocated by WRI from GEODB/CARMA/GEO matching; may be off by up to a few km)",
                }.get(first, "OSM power=plant node/centroid (site outline centre)")
        for r in recs:
            if r["source"] == "GPPD":
                seen_gppd.add(r["id"])
        sites.append(dict(id="CONV-" + p["id"], name=p["name"], operator=p.get("operator"), subclass=p["subclass"],
                          lat=lat, lon=lon, precision=prec, records=recs, srcs=srcs, status_raw=p["status"],
                          status_date=p.get("status_date"), status_source=p.get("status_source"),
                          origin="offtakers class B " + p["id"]))
    # GPPD (DUKES-derived) rows not carried by the offtakers class
    for gid in sorted(gppd_by_id):
        if gid in seen_gppd:
            continue
        r = gppd_by_id[gid]
        st, sd, ss = "status unknown", None, None
        for row in status_rows:
            if re.search(row["match_regex"], r["name"], re.I):
                st, sd, ss = row["status"], row["status_date"] or None, row["source_url"]
                break
        sub = {"Gas": "gas_other", "Coal": "coal", "Oil": "oil", "Nuclear": "nuclear", "Biomass": "biomass",
               "Waste": "energy_from_waste", "Cogeneration": "chp_energy_centre"}.get(r["primary_fuel"], "thermal_unspecified")
        sites.append(dict(id="CONV-GPPD-" + gid, name=r["name"], operator=r["owner"] or None, subclass=sub,
                          lat=float(r["latitude"]), lon=float(r["longitude"]),
                          precision=f"GPPD point (geolocation_source={r['geolocation_source']}; may be off by up to a few km)",
                          records=[dict(source="GPPD", id=gid, name=r["name"], operator=r["owner"], fuel=r["primary_fuel"],
                                        mw=float(r["capacity_mw"]), lon=float(r["longitude"]), lat=float(r["latitude"]))],
                          srcs=["GPPD"], status_raw=st, status_date=sd, status_source=ss, origin="GPPD row not in offtakers class B"))

    features, excluded = [], []
    for s in sites:
        gf = [r.get("fuel") for r in s["records"] if r["source"] == "GPPD" and r.get("fuel")]
        of = {(r.get("fuel") or "").lower() for r in s["records"] if r["source"].startswith("OSM")}
        fuel, tech, fbasis = fuel_tech(s["name"], s["operator"], s["subclass"], gf, of)
        status, sdate, sbasis = map_status(s["status_raw"], s["status_date"])
        if s["status_raw"] == "converted":
            # converted coal sites: the current fuel is what the station burns now (R-otdata table fuel_note)
            nm = s["name"].lower()
            if "kilroot" in nm:
                fuel, tech, fbasis = "gas", "OCGT/gas engines (coal units closed 2023)", "R-otdata: coal closed Sep 2023, gas from 2024"
            elif "drax" in nm or "lynemouth" in nm or "ashington" in nm:
                fuel, tech, fbasis = "biomass", "biomass steam (converted from coal)", "R-otdata conversion row"
        if fuel in RENEWABLE_SCOPE and gf and set(gf) <= {"Gas", "Coal", "Oil"} and s["status_raw"] != "converted":
            fuel, tech, fbasis = gf[0].lower(), gf[0].lower() + " (type not stated)", (
                f"GPPD fuel={'/'.join(sorted(set(gf)))} overrides the offtakers subclass '{s['subclass']}' (reconciliation fix)")
        if "ashington" in s["name"].lower() and "lynemouth" in (s["operator"] or "").lower():
            fuel, tech, fbasis = "biomass", "biomass steam (converted from coal 2018)", "NAEI operator Lynemouth Power Ltd"
        mw_recs = [dict(gppd_id=r["id"], name=r["name"], fuel=r.get("fuel"), mw=r["mw"]) for r in s["records"]
                   if r["source"] == "GPPD" and r.get("mw") is not None]
        if status == "converted" and mw_recs:
            # count only the records of the fuel the site burns now (Kilroot: the closed coal units are not capacity)
            cur = [r for r in mw_recs if (r["fuel"] or "").lower() == fuel]
            for r in mw_recs:
                r["counted"] = r in cur
            mw_recs_counted = cur
        else:
            for r in mw_recs:
                r["counted"] = True
            mw_recs_counted = mw_recs
        mw = round(sum(r["mw"] for r in mw_recs_counted), 3) if mw_recs_counted else None
        repd_hit = None
        if fuel in RENEWABLE_SCOPE:
            tk = tokens(s["name"]) | tokens(s["operator"])
            best = None
            for r in repd:
                if status in ("operational", "unknown", "converted") and r["status"] != "operational":
                    continue  # a planned REPD project at the same place is a different asset
                d = hav_km(s["lat"], s["lon"], r["latitude"], r["longitude"])
                if d > NAME_KM:
                    continue
                shared = sorted(tk & tokens(r["name"]))
                same = r["repd_technology"] in COMPAT[fuel]
                if (same and (d <= NEAR_KM or shared)) or (shared and d <= CROSS_KM):
                    key = (0 if same else 1, 0 if shared else 1, d, int(r["repd_ref"]))
                    if best is None or key < best[0]:
                        best = (key, r, d, shared)
            if best:
                _, r, d, shared = best
                repd_hit = dict(repd_ref=r["repd_ref"], repd_name=r["name"], repd_technology=r["repd_technology"],
                                repd_status=r["status"], distance_km=round(d, 3), shared_name_tokens=shared)
        sources = []
        for k in s["srcs"]:
            n, u, l = LIC[k]
            sources.append(dict(name=n, url=u, licence=l))
        if s["status_source"] and status in ("closed", "closing", "converted", "construction") and s["status_source"] not in [x["url"] for x in sources]:
            sources.append(dict(name="status evidence", url=s["status_source"], licence="cited fact (not redistributed data)"))
        props = dict(
            id=s["id"], name=s["name"], operator=s["operator"] or None, fuel=fuel, technology=tech, fuel_basis=fbasis,
            mw=mw, mw_basis=("installed capacity, sum of GPPD v1.3.0 records (GPPD cites BEIS DUKES 5.11; DUKES edition c. 2018-19, unverified). "
                             "Not DNC, not TEC, not a grid connection capacity. Closed sites keep their last listed capacity; converted sites count only "
                             "records of the current fuel (mw_records[].counted).") if mw is not None else "not stated in the open sources used (unknown, not zero)",
            mw_records=mw_recs, status=status, status_date=sdate, status_basis=sbasis,
            status_source=s["status_source"] if status != "unknown" else None,
            in_nearest_default=status != "closed",
            precision=s["precision"], origin=s["origin"], sources=sources,
            records=[{k: v for k, v in r.items() if k in ("source", "id", "name", "operator", "fuel", "mw", "sector")} for r in s["records"]],
            repd_rule=("renewable-scope, no REPD match (1 km same-group technology, or 5 km shared name token): kept" if fuel in RENEWABLE_SCOPE else "not renewable-scope: never excluded"),
            caveat="Listed in an open register. Proximity is not connection, capacity or feasibility.",
        )
        feat = dict(type="Feature", geometry=dict(type="Point", coordinates=[round(s["lon"], 6), round(s["lat"], 6)]), properties=props)
        if repd_hit:
            excluded.append(dict(id=s["id"], name=s["name"], fuel=fuel, status=status, mw=mw, lat=round(s["lat"], 6), lon=round(s["lon"], 6),
                                 reason="in REPD (GridAtlas registry 202608290716): served by the 30-nearest REPD list", repd=repd_hit))
        else:
            features.append(feat)
    features.sort(key=lambda f: f["properties"]["id"])
    for a in features:
        ax, ay = a["geometry"]["coordinates"]
        a["properties"]["colocated_ids"] = [b["properties"]["id"] for b in features if b is not a
                                            and hav_km(ay, ax, b["geometry"]["coordinates"][1], b["geometry"]["coordinates"][0]) <= 0.1]
    excluded.sort(key=lambda e: e["id"])

    meta = dict(
        type="FeatureCollection", generation=GENERATION,
        title="UK conventional power assets (open data) for the Grid Engine 30-nearest query",
        inputs_sha256={k: v[1] for k, v in INPUTS.items()},
        licence=("ODbL 1.0 for the database as a whole (contains OpenStreetMap data, (c) OpenStreetMap contributors, "
                 "https://www.openstreetmap.org/copyright). NAEI point sources: OGL v3.0, (c) Crown copyright, Defra/DESNZ "
                 "(https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). WRI GPPD v1.3.0: CC BY 4.0 "
                 "(https://creativecommons.org/licenses/by/4.0/). REPD (OGL v3.0) used only to exclude. Per-feature sources[] give each licence."),
        repd_rule=__doc__.split("REPD rule")[1].split('"""')[0].strip(),
        status_vocabulary=["operational", "closing", "closed", "converted", "construction", "unknown"],
        not_included=("DUKES 5.11 May 2026 xlsx (OGL v3.0) and the NESO TEC register (NESO Open Data Licence) were not reachable "
                      "from this session (proxy CONNECT 403; WebFetch not allowlisted). MW therefore comes only from GPPD "
                      "(DUKES-derived, c. 2018-19) and is null for NAEI/OSM-only sites; no 'construction' gas projects beyond Hinkley Point C."),
        caveat="Proximity is not connection, capacity or feasibility. A null MW is unknown, not zero. Status 'operational' means reported to NAEI 2023.",
        features=features,
    )
    os.makedirs(OUT, exist_ok=True)
    gj = os.path.join(OUT, "conventional_power_uk.geojson")
    with open(gj, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, sort_keys=False, separators=(",", ":"))
        fh.write("\n")
    with open(os.path.join(OUT, "excluded_repd.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(excluded, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    c_fs = collections.Counter((f["properties"]["fuel"], f["properties"]["status"]) for f in features)
    fuels = sorted({k[0] for k in c_fs})
    stats = meta["status_vocabulary"]
    mw_f = collections.defaultdict(float)
    mwn = collections.Counter()
    for f in features:
        p = f["properties"]
        if p["mw"] is not None:
            mw_f[p["fuel"]] += p["mw"]; mwn[p["fuel"]] += 1
    lines = [f"# Counts ({GENERATION})", "", f"Features: {len(features)}. Excluded by the REPD rule: {len(excluded)}.", "",
             "| fuel | " + " | ".join(stats) + " | total | with MW | MW (GPPD installed, sum) |",
             "|---|" + "---:|" * (len(stats) + 3)]
    for fu in fuels:
        row = [c_fs.get((fu, st), 0) for st in stats]
        lines.append(f"| {fu} | " + " | ".join(map(str, row)) + f" | {sum(row)} | {mwn[fu]} | {mw_f[fu]:.1f} |")
    tot = [sum(c_fs.get((fu, st), 0) for fu in fuels) for st in stats]
    lines.append("| **all** | " + " | ".join(map(str, tot)) + f" | {sum(tot)} | {sum(mwn.values())} | {sum(mw_f.values()):.1f} |")
    tc = collections.Counter(f["properties"]["technology"] for f in features)
    lines += ["", "## Technology", "", "| technology | n |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in sorted(tc.items())]
    lines += ["", "## Excluded by the REPD rule", "", "| id | name | fuel | REPD ref | REPD name | km |", "|---|---|---|---|---|---:|"]
    lines += [f"| {e['id']} | {e['name']} | {e['fuel']} | {e['repd']['repd_ref']} | {e['repd']['repd_name']} | {e['repd']['distance_km']} |" for e in excluded]
    with open(os.path.join(OUT, "counts.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    with open(os.path.join(OUT, "counts.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(dict(generation=GENERATION, features=len(features), excluded_repd=len(excluded),
                       by_fuel_status={f"{a}|{b}": n for (a, b), n in sorted(c_fs.items())},
                       technology=dict(sorted(tc.items()))), fh, indent=1)
        fh.write("\n")
    shas = {n: sha256(os.path.join(OUT, n)) for n in sorted(os.listdir(OUT)) if n != "SHA256SUMS"}
    with open(os.path.join(OUT, "SHA256SUMS"), "w", newline="\n") as fh:
        fh.writelines(f"{v}  {k}\n" for k, v in shas.items())
    print(f"features={len(features)} excluded_repd={len(excluded)} geojson_sha256={shas['conventional_power_uk.geojson']}")


if __name__ == "__main__":
    main()
