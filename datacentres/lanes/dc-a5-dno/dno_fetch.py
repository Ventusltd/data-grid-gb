#!/usr/bin/env python3
"""DC-A5: DNO open-data fetcher for demand / data-centre evidence (standard library only).

What it does
  * Downloads pinned DNO open-data resources (raw snapshot + SHA-256 into manifest.json).
  * Parses CSV resources and screens Embedded Capacity Register (ECR) rows for
    data-centre operator / "data centre" names in customer_name / customer_site.
  * Writes normalised GeoJSON of the screened ECR rows (Record tier: a name match
    is a CANDIDATE, not a confirmed data centre; ECR scope is generation, storage
    and DSR >= 50 kW, so absence from the ECR says nothing about a site).
  * Prints counts per source.

Licence gate
  Only sources whose licence is verified as fully open (CC BY 4.0, OGL v3 or an
  OGL-v3-derived DNO licence) AND which need no login are in OPEN_SOURCES.
  Login-gated sources (UKPN, SPEN, ENWL, NPg records-gated) are listed in
  GATED_SOURCES and are fetched only if the matching API-key env var is set and
  --include-gated is passed. They are NOT fully open under the GridAtlas rule
  until their dataset-level licence is verified.

Usage
  python3 -I dno_fetch.py --out DIR            # fetch open sources
  python3 -I dno_fetch.py --out DIR --verify   # re-fetch and compare SHA-256 to manifest
  python3 -I dno_fetch.py --selftest           # offline parser test (no network)

Honesty: proximity is not connection; an ECR import capacity is a connection
figure (MW/MVA), never IT load; unknown is not zero.
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "gridatlas-dc-a5/1.0 (+https://github.com/Ventusltd/gridatlas/issues/73)"

# ---------------------------------------------------------------- sources ---
# Each source: id, dno, licence (verbatim name + URL), kind, url(s), notes.
# kind "csv" = direct pinned file; "ckan_latest_csv" = resolve the newest CSV
# resource of a CKAN package whose name matches a regex (URL recorded in manifest).
OPEN_SOURCES = [
    {
        "id": "ssen_ecr_part1_1mw",
        "dno": "SSEN Distribution",
        "licence": "Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/)",
        "licence_evidence": "https://data-api.ssen.co.uk/dataset/embedded_capacity_register",
        "kind": "csv",
        "url": "https://data-api.ssen.co.uk/dataset/655eb750-7b3f-4c55-93b6-9bc4c26a57ab/resource/bbae6797-364a-4b2d-a01a-8395e21bee76/download/ssen_ecr_part_1_1mw_august_2026_csv.csv",
        "ecr": True,
    },
    {
        "id": "ssen_ecr_part1_50kw",
        "dno": "SSEN Distribution",
        "licence": "Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/)",
        "licence_evidence": "https://data-api.ssen.co.uk/dataset/embedded_capacity_register",
        "kind": "csv",
        "url": "https://data-api.ssen.co.uk/dataset/655eb750-7b3f-4c55-93b6-9bc4c26a57ab/resource/d258bd7b-22db-4d32-9450-b3783591b66d/download/ssen_ecr_part_1_50kw_august_2026_csv.csv",
        "ecr": True,
    },
    {
        "id": "ssen_ecr_attributes",
        "dno": "SSEN Distribution",
        "licence": "Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/)",
        "licence_evidence": "https://data-api.ssen.co.uk/dataset/embedded_capacity_register",
        "kind": "csv",
        "url": "https://data-api.ssen.co.uk/dataset/655eb750-7b3f-4c55-93b6-9bc4c26a57ab/resource/16bc7142-6f53-4b5e-8c2c-2a1168519ffe/download/embedded-capacity-register-attributes.csv",
        "ecr": False,
    },
    {
        "id": "ssen_heatmap_package",
        "dno": "SSEN Distribution",
        "licence": "Creative Commons Attribution 4.0 (resource-level; dataset-level field blank: UNVERIFIED for every resource)",
        "licence_evidence": "https://data-api.ssen.co.uk/dataset/93f6890a-4bd4-4b75-9955-6deace56decb/resource/106e7230-9b2d-445b-a010-be255416968c",
        "kind": "ckan_package",
        "api": "https://data-api.ssen.co.uk/api/3/action/package_show?id=93f6890a-4bd4-4b75-9955-6deace56decb",
        "resource_name_re": r"(Demand Heat ?Map|Primary Substation|BSP|GSP)",
        "ecr": False,
    },
    {
        "id": "nged_ecr_latest",
        "dno": "National Grid Electricity Distribution",
        "licence": "NGED Open Data Licence (OGL v3.0-derived; WPD Open Data Licence Dec 2020 text) - dataset-level Rights field UNVERIFIED",
        "licence_evidence": "https://open.windriver.com/info/uni-license-list/licenses/ogl-wpd-3.0.html",
        "kind": "ckan_latest_csv",
        "api": "https://connecteddata.nationalgrid.co.uk/api/3/action/package_show?id=embedded-capacity-register",
        "resource_name_re": r"(ECR|Embedded Capacity Register)",
        "ecr": True,
    },
    {
        "id": "nged_connections_reform_register",
        "dno": "National Grid Electricity Distribution",
        "licence": "NGED Open Data Licence (Rights field on dataset page)",
        "licence_evidence": "https://connecteddata.nationalgrid.co.uk/dataset/nged-connections-reform-register",
        "kind": "csv",
        "url": "https://connecteddata.nationalgrid.co.uk/dataset/a1b2a409-3c43-4551-b623-311c5ca7b665/resource/88906436-9c30-465f-a5f5-6800263e8d05/download/251212_cdpdata_nged-connections-reform-register.csv",
        "ecr": False,
    },
    {
        "id": "nged_clearview_queue",
        "dno": "National Grid Electricity Distribution",
        "licence": "WPD Open Data Licence (Rights field; Data Classification: Open)",
        "licence_evidence": "https://connecteddata.nationalgrid.co.uk/eu/dataset/clearview-connect-queue-positions",
        "kind": "csv",
        "url": "https://connecteddata.nationalgrid.co.uk/dataset/2fbdb63c-999e-417d-b255-609cd3c79ebe/resource/0933c369-9b05-4afc-982d-26f07fc5c4d6/download/clearviewconnect_connection_pipeline_data_2025_05.csv",
        "ecr": False,
    },
    {
        "id": "npg_ndp_demand_headroom",
        "dno": "Northern Powergrid",
        "licence": "Northern Powergrid Open Data Licence v1.0 (text at /p/opendatalicence/ NOT read: UNVERIFIED)",
        "licence_evidence": "https://northernpowergrid.opendatasoft.com/explore/dataset/npg_ndp_generation_headroom",
        "kind": "ods_export",
        "url": "https://northernpowergrid.opendatasoft.com/api/explore/v2.1/catalog/datasets/npg_ndp_demand_headroom/exports/csv?delimiter=%2C",
        "ecr": False,
    },
]

# Login-gated: fetched only with --include-gated and an API key. NOT fully open.
GATED_SOURCES = [
    {"id": "ukpn_data_centres_by_local_authority", "dno": "UK Power Networks", "env": "UKPN_API_KEY",
     "url": "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-data-centres-by-local-authority/exports/csv?delimiter=%2C",
     "licence": "UNVERIFIED (portal offers CC BY / OGL / Shared Dataset Agreement per dataset); login required", "ecr": False},
    {"id": "ukpn_large_demand_list", "dno": "UK Power Networks", "env": "UKPN_API_KEY",
     "url": "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-large-demand-list/exports/csv?delimiter=%2C",
     "licence": "UNVERIFIED; login required", "ecr": False},
    {"id": "ukpn_data_centre_demand_profiles", "dno": "UK Power Networks", "env": "UKPN_API_KEY",
     "url": "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-data-centre-demand-profiles/exports/csv?delimiter=%2C",
     "licence": "UNVERIFIED; login required", "ecr": False},
    {"id": "ukpn_ecr_1mw_plus", "dno": "UK Power Networks", "env": "UKPN_API_KEY",
     "url": "https://ukpowernetworks.opendatasoft.com/api/explore/v2.1/catalog/datasets/ukpn-embedded-capacity-register-1-1mw-and-above/exports/csv?delimiter=%2C",
     "licence": "CC BY 4.0 (2021 statement; dataset id and current licence UNVERIFIED); login required", "ecr": True},
    {"id": "enwl_gsp_connection_queue", "dno": "Electricity North West (SP Electricity North West)", "env": "ENWL_API_KEY",
     "url": "https://electricitynorthwest.opendatasoft.com/api/explore/v2.1/catalog/datasets/enwl-gsp-connection-queue/exports/csv?delimiter=%2C",
     "licence": "Portal default CC BY 4.0 'unless otherwise stated'; registered users only", "ecr": False},
    {"id": "enwl_ecr_part3_dsr", "dno": "Electricity North West (SP Electricity North West)", "env": "ENWL_API_KEY",
     "url": "https://electricitynorthwest.opendatasoft.com/api/explore/v2.1/catalog/datasets/enwl-embedded-capacity-register-3-dsr/exports/csv?delimiter=%2C",
     "licence": "Portal default CC BY 4.0; registered users only; dataset id UNVERIFIED", "ecr": True},
]

# ------------------------------------------------------- data-centre screen ---
DC_PATTERNS = [
    r"\bdata ?cent(re|er)s?\b", r"\bdatacent(re|er)s?\b", r"\bequinix\b", r"\bdigital realty\b",
    r"\bvirtus\b", r"\bark data\b", r"\bpure ?dc\b", r"\bkao data\b", r"\bcyrusone\b",
    r"\bvantage data\b", r"\bntt\b", r"\bglobal switch\b", r"\btelehouse\b", r"\biron mountain\b",
    r"\byondr\b", r"\bechelon\b", r"\bserverfarm\b", r"\blatos\b", r"\bnscale\b", r"\bcoreweave\b",
    r"\bproximity data\b", r"\bcolt (dcs|data)\b", r"\bnext ?gen(eration)? data\b", r"\bpulsant\b",
    r"\bcentersquare\b", r"\bstack infrastructure\b", r"\bblackstone\b.*\bdata\b", r"\bqts\b",
    r"\bmicrosoft\b", r"\bgoogle\b", r"\bamazon data\b", r"\bamazon web services\b", r"\baws\b",
    r"\bmeta platforms\b", r"\boracle\b", r"\bdatum\b", r"\b4d data\b", r"\bnorthern data\b",
]
DC_RE = re.compile("|".join(DC_PATTERNS), re.I)
NAME_FIELDS_RE = re.compile(r"customer[_ ]?(name|site)|site[_ ]?name|project[_ ]?name", re.I)


def dc_hits(row):
    out = []
    for k, v in row.items():
        if k and v and NAME_FIELDS_RE.search(k):
            m = DC_RE.search(v)
            if m:
                out.append((k, m.group(0)))
    return out


# --------------------------------------------------------- OSGB36 -> WGS84 ---
def osgb_to_wgs84(E, N):
    """Ordnance Survey National Grid E/N -> WGS84 lon/lat (Helmert, ~5 m)."""
    a, b = 6377563.396, 6356256.909
    F0, lat0, lon0 = 0.9996012717, math.radians(49), math.radians(-2)
    N0, E0 = -100000, 400000
    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    lat, M = lat0, 0
    while N - N0 - M >= 1e-5:
        lat = (N - N0 - M) / (a * F0) + lat
        Ma = (1 + n + 1.25 * n**2 + 1.25 * n**3) * (lat - lat0)
        Mb = (3 * n + 3 * n**2 + 21 / 8 * n**3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        Mc = (15 / 8 * n**2 + 15 / 8 * n**3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        Md = 35 / 24 * n**3 * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
        M = b * F0 * (Ma - Mb + Mc - Md)
    s, c, t = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * F0 / math.sqrt(1 - e2 * s * s)
    rho = a * F0 * (1 - e2) / (1 - e2 * s * s) ** 1.5
    eta2 = nu / rho - 1
    VII = t / (2 * rho * nu)
    VIII = t / (24 * rho * nu**3) * (5 + 3 * t * t + eta2 - 9 * t * t * eta2)
    IX = t / (720 * rho * nu**5) * (61 + 90 * t * t + 45 * t**4)
    X = 1 / (c * nu)
    XI = 1 / (c * 6 * nu**3) * (nu / rho + 2 * t * t)
    XII = 1 / (c * 120 * nu**5) * (5 + 28 * t * t + 24 * t**4)
    XIIA = 1 / (c * 5040 * nu**7) * (61 + 662 * t * t + 1320 * t**4 + 720 * t**6)
    dE = E - E0
    lat = lat - VII * dE**2 + VIII * dE**4 - IX * dE**6
    lon = lon0 + X * dE - XI * dE**3 + XII * dE**5 - XIIA * dE**7
    # Airy 1830 -> GRS80 via Helmert
    nu = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    x = nu * math.cos(lat) * math.cos(lon)
    y = nu * math.cos(lat) * math.sin(lon)
    z = (1 - e2) * nu * math.sin(lat)
    tx, ty, tz = 446.448, -125.157, 542.060
    sc = -20.4894e-6
    rx, ry, rz = (math.radians(v / 3600) for v in (0.1502, 0.2470, 0.8421))
    x2 = tx + (1 + sc) * x - rz * y + ry * z
    y2 = ty + rz * x + (1 + sc) * y - rx * z
    z2 = tz - ry * x + rx * y + (1 + sc) * z
    a2, b2 = 6378137.0, 6356752.3141
    e22 = 1 - (b2 * b2) / (a2 * a2)
    p = math.hypot(x2, y2)
    lat2 = math.atan2(z2, p * (1 - e22))
    for _ in range(10):
        nu2 = a2 / math.sqrt(1 - e22 * math.sin(lat2) ** 2)
        lat2 = math.atan2(z2 + e22 * nu2 * math.sin(lat2), p)
    return round(math.degrees(math.atan2(y2, x2)), 6), round(math.degrees(lat2), 6)


def _num(v):
    try:
        f = float(str(v).replace(",", "").strip())
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _pick(row, *needles):
    for k, v in row.items():
        kl = (k or "").lower()
        if all(n in kl for n in needles):
            return v
    return None


def row_point(row):
    lon, lat = _num(_pick(row, "lon")), _num(_pick(row, "lat"))
    if lon is not None and lat is not None and -9 < lon < 3 and 49 < lat < 61:
        return [lon, lat], "lonlat"
    E = _num(_pick(row, "x-coordinate")) or _num(_pick(row, "easting"))
    N = _num(_pick(row, "y-coordinate")) or _num(_pick(row, "northing"))
    if E and N and 0 < E < 700000 and 0 < N < 1300000:
        return list(osgb_to_wgs84(E, N)), "osgb_en"
    return None, None


# ----------------------------------------------------------------- network ---
def http_get(url, headers=None, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # follows 302 (SSEN -> R2)
        return r.read(), r.geturl()


def resolve(src):
    """Return list of (name, url) for the source."""
    if src["kind"] in ("csv", "ods_export"):
        return [(src["id"], src["url"])]
    body, _ = http_get(src["api"])
    pkg = json.loads(body)["result"]
    rx = re.compile(src["resource_name_re"], re.I)
    res = [r for r in pkg.get("resources", []) if rx.search(r.get("name") or "")
           and (r.get("format") or "").upper() in ("CSV", "XLSX")]
    if src["kind"] == "ckan_latest_csv":
        res = [r for r in res if (r.get("format") or "").upper() == "CSV"]
        res.sort(key=lambda r: r.get("last_modified") or r.get("created") or "", reverse=True)
        res = res[:1]
    return [(r.get("name") or r["id"], r["url"]) for r in res]


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:120]


def parse_csv_bytes(b):
    text = b.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def process(src, files, outdir):
    """files: list of (fname, bytes). Returns stats + features."""
    stats = {"rows": 0, "dc_name_candidates": 0, "with_import_capacity": 0, "files": len(files)}
    feats = []
    for fname, b in files:
        if not fname.lower().endswith(".csv") and not b[:200].count(b","):
            continue
        rows = parse_csv_bytes(b)
        stats["rows"] += len(rows)
        if not src.get("ecr"):
            continue
        for i, row in enumerate(rows):
            imp = _num(_pick(row, "import", "capacity", "mw"))
            if imp:
                stats["with_import_capacity"] += 1
            hits = dc_hits(row)
            if not hits:
                continue
            stats["dc_name_candidates"] += 1
            pt, how = row_point(row)
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": pt} if pt else None,
                "properties": {
                    "source_id": src["id"], "dno": src["dno"], "file": fname, "row": i,
                    "customer_name": _pick(row, "customer", "name"),
                    "customer_site": _pick(row, "customer", "site"),
                    "match": "; ".join(f"{k}~{m}" for k, m in hits),
                    "energy_source_1": _pick(row, "energy", "source", "1"),
                    "connection_status": _pick(row, "status"),
                    "import_capacity_mw_connection": imp,
                    "grid_connection_mw_note": "ECR import capacity is a grid connection figure, NOT IT load",
                    "location_method": how,
                    "tier": "Record (name match = candidate only)",
                    "licence": src["licence"],
                },
            })
    return stats, feats


def run(args):
    out = os.path.abspath(args.out)
    os.makedirs(os.path.join(out, "raw"), exist_ok=True)
    man_path = os.path.join(out, "manifest.json")
    old = {}
    if os.path.exists(man_path):
        with open(man_path) as f:
            old = {e["url"]: e for s in json.load(f)["sources"] for e in s.get("files", [])}
    sources = list(OPEN_SOURCES)
    if args.include_gated:
        for g in GATED_SOURCES:
            if os.environ.get(g["env"]):
                sources.append({**g, "kind": "ods_export", "gated": True})
    manifest = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "sources": []}
    all_feats, summary, mismatches = [], {}, []
    for src in sources:
        entry = {"id": src["id"], "dno": src["dno"], "licence": src["licence"], "files": []}
        try:
            targets = resolve(src)
            files = []
            for name, url in targets:
                hdr = {}
                if src.get("gated"):
                    hdr = {"Authorization": "Apikey " + os.environ[src["env"]]}
                b, final = http_get(url, hdr)
                sha = hashlib.sha256(b).hexdigest()
                fname = safe_name(os.path.basename(urllib.parse.urlparse(url).path) or name)
                d = os.path.join(out, "raw", src["id"])
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, fname), "wb") as f:
                    f.write(b)
                entry["files"].append({"url": url, "file": f"raw/{src['id']}/{fname}", "bytes": len(b), "sha256": sha})
                if args.verify and url in old and old[url]["sha256"] != sha:
                    mismatches.append(url)
                files.append((fname, b))
            stats, feats = process(src, files, out)
            entry["stats"] = stats
            all_feats.extend(feats)
            summary[src["id"]] = stats
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            summary[src["id"]] = {"error": entry["error"]}
        manifest["sources"].append(entry)
    all_feats.sort(key=lambda f: (f["properties"]["source_id"], f["properties"]["row"]))
    with open(os.path.join(out, "dno_ecr_dc_candidates.geojson"), "w") as f:
        json.dump({"type": "FeatureCollection", "features": all_feats}, f, indent=1, sort_keys=True)
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1, sort_keys=True))
    if mismatches:
        print("SHA-256 changed for:", *mismatches, sep="\n  ")
        return 2
    return 0


# ---------------------------------------------------------------- selftest ---
FIXTURE = (
    "customer_name,customer_site,energy_source_1,connection_status,"
    "connected_maximum_import_capacity(mw),location(x-coordinate):_eastings_(where_data_is_held),"
    "location(y-coordinate):_northings_(where_data_is_held)\n"
    "Example Data Centre Ltd,Slough Campus,Fossil - Gas oil,Connected,40,497000,180000\n"
    "Farm Solar Ltd,Field A,Solar,Accepted not yet connected,0.1,400000,300000\n"
    "NTT Global Data Centers EMEA UK,Dagenham,Storage,Connected,,548000,183000\n"
)


def selftest():
    src = {"id": "fixture", "dno": "test", "licence": "n/a", "ecr": True}
    stats, feats = process(src, [("fixture.csv", FIXTURE.encode())], None)
    assert stats == {"rows": 3, "dc_name_candidates": 2, "with_import_capacity": 2, "files": 1}, stats
    lon, lat = feats[0]["geometry"]["coordinates"]
    assert abs(lon - (-0.6)) < 0.05 and abs(lat - 51.5) < 0.05, (lon, lat)
    # OS worked example (Caister water tower): E 651409.903 N 313177.270 is OSGB36
    # 52d39'27.2531"N 1d43'4.5177"E (52.657570, 1.717922); the Helmert shift to
    # WGS84 is about +0.0004 lat / -0.0019 lon -> ~52.65798, 1.71605.
    lon2, lat2 = osgb_to_wgs84(651409.903, 313177.270)
    assert abs(lon2 - 1.71605) < 0.0002 and abs(lat2 - 52.65798) < 0.0002, (lon2, lat2)
    assert not DC_RE.search("Farm Solar Ltd")
    print("selftest ok", stats)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--include-gated", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    return selftest() if a.selftest else run(a)


if __name__ == "__main__":
    sys.exit(main())
