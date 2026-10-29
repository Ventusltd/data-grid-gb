#!/usr/bin/env python3
"""DC-A1: fetch NESO connection registers and normalise data-centre-like rows.

Issue #73, steps 1-3 (NESO part). Standard library only.

WHAT THIS DOES
--------------
1. Resolves each pinned NESO CKAN resource (dataset id + resource id are
   pinned; the CSV filename is date-stamped and changes twice weekly, so the
   current download URL is read from `resource_show`).
2. Saves the raw bytes under <out>/raw/<source>/<sha256-prefix>-<filename>
   and records url, bytes, sha256 and fetch time in <out>/manifest.json.
3. Normalises EVERY row into the #73 step-3 record and flags rows that look
   like data centres (dc_match). Writes:
     <out>/neso_connections.normalised.json   all rows, step-3 schema
     <out>/neso_dc_like.normalised.json       only dc_match rows
     <out>/neso_dc_like.geojson               same, geometry null (see below)
     <out>/counts.json                        counts per source and rule

HONESTY RULES BAKED IN
----------------------
* it_load_mw is ALWAYS null: no NESO source publishes IT load. The MW written
  is grid_connection_mw, with its basis (e.g. "TEC export capacity") and
  direction stated. They are never conflated.
* A register row is a connection agreement or application, not a commitment
  to build. Status keeps the raw NESO value in status_raw.
* No coordinates are invented. NESO gives a connection-site (substation) NAME
  only; location_precision is "connection_site_name" and geometry is null
  until a later step joins the name to a substation with evidence.
* dc_match is a name/type heuristic. A miss is "not identified", not "not a
  data centre"; a hit is "named like a data centre", not a confirmed one.

SOURCES (licence: NESO Open Data Licence, OGL v3-derived, attribution
"Supported by National Energy SO Open Data";
https://www.neso.energy/data-portal/neso-open-licence )
  TEC       Transmission Entry Capacity register (generation/storage/
            interconnectors; demand is NOT expected, rows are scanned anyway)
  EMBEDDED  Embedded Register (distribution-connected projects with a
            transmission impact; generation-focused)
Optional, OFF by default because its licence is UNVERIFIED (a NESO website
document, not a data-portal dataset):
  EA        Existing Agreements Register xlsx (Gate 2 applicants that consented
            to publication; Technology Type "Transmission Connected Demand";
            "Some Transmission Connected Demand projects have 'Data Centre' in
            their 'Project Name'" - NESO FOI/25/174). Enable with --include-ea.

USAGE
  python3 -I fetch_neso.py --out DIR                 # live fetch
  python3 -I fetch_neso.py --out DIR --include-ea    # + EA register (unverified licence)
  python3 -I fetch_neso.py --out DIR --from-file TEC=path.csv --from-file EMBEDDED=path.csv
  python3 -I fetch_neso.py --selftest                # synthetic fixture, no network
Exit code: 0 ok, 2 if any source failed to fetch (partial output still written).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

SCHEMA_VERSION = "dc-pipeline-record/0.1 (gridatlas#73 step 3)"
USER_AGENT = "gridatlas-dc-a1/0.1 (+https://github.com/Ventusltd/gridatlas/issues/73)"
CKAN = "https://api.neso.energy/api/3/action/"
NESO_LICENCE = {
    "name": "NESO Open Data Licence",
    "url": "https://www.neso.energy/data-portal/neso-open-licence",
    "basis": "based on version 3.0 of the Open Government Licence with specific amendments for NESO",
    "attribution": "Supported by National Energy SO Open Data",
    "fully_open": "yes (commercial use, attribution only); OGL-derived, not OGL v3 verbatim - owner to confirm",
}

SOURCES = {
    "TEC": {
        "title": "NESO Transmission Entry Capacity (TEC) register",
        "kind": "ckan",
        "dataset_id": "cbd45e54-e6e2-4a38-99f1-8de6fd96d7c1",
        "resource_id": "17becbab-e3e8-473f-b303-3806f43a6a10",
        "page": "https://www.neso.energy/data-portal/transmission-entry-capacity-tec-register",
        "fallback_url": "https://api.neso.energy/dataset/cbd45e54-e6e2-4a38-99f1-8de6fd96d7c1/resource/17becbab-e3e8-473f-b303-3806f43a6a10/download/tec-register-27-october-2026.csv",
        "licence": NESO_LICENCE,
        "cadence": "twice weekly",
        "mw_basis": "TEC (transmission entry capacity, export) - cumulative total capacity",
        "mw_direction": "export",
        "default": True,
    },
    "EMBEDDED": {
        "title": "NESO Embedded Register",
        "kind": "ckan",
        "dataset_id": "4b0e109c-aa23-45ac-86c1-0bda557d1aab",
        "resource_id": "68b6f3a1-e1bf-403b-9062-0269fc758d77",
        "page": "https://www.neso.energy/data-portal/embedded-register",
        "fallback_url": "https://api.neso.energy/dataset/4b0e109c-aa23-45ac-86c1-0bda557d1aab/resource/68b6f3a1-e1bf-403b-9062-0269fc758d77/download/embedded-register-27-october-2026.csv",
        "licence": NESO_LICENCE,
        "cadence": "twice weekly",
        "mw_basis": "Embedded register capacity (export) - cumulative total capacity",
        "mw_direction": "export",
        "default": True,
    },
    "EA": {
        "title": "NESO Existing Agreements (EA) Register (Gate 2 applicants, consented)",
        "kind": "document",
        "url": "https://www.neso.energy/document/373996/download",
        "page": "https://www.neso.energy/document/374976/download",  # FOI/25/174, which cites it
        "licence": {
            "name": "UNVERIFIED (NESO website document; no licence statement found)",
            "url": None,
            "fully_open": "unverified",
        },
        "cadence": "irregular (republished as Gate 2 agreements change) - unverified",
        "mw_basis": "EA register capacity column as published (import for demand rows) - column meaning unverified",
        "mw_direction": "import (demand rows) / export (generation rows) - unverified",
        "default": False,
    },
}

# ---------------------------------------------------------------- matching

DC_NAME = re.compile(
    r"data\s*-?\s*cent(?:re|er)s?|datacent|\bd\.?c\.?\s*campus|hyperscal|"
    r"\bai\s+(?:campus|growth\s+zone|factory|data)|digital\s+(?:park|campus|infrastructure)|"
    r"\bcompute\b|\bcloud\s+campus|\bcolocation\b|\bco-location\s+campus",
    re.I,
)
# Operators/developers whose core business is data centres. A customer-name hit
# on these is a weaker signal (they also hold BESS/generation agreements), so it
# is reported under its own rule and never merged with name/type hits.
DC_OPERATORS = re.compile(
    r"\b(equinix|digital\s+realty|global\s+switch|virtus|cyrusone|vantage\s+data|"
    r"ntt\s+(?:global\s+)?data|kao\s+data|pure\s*dc|yondr|qts\b|stack\s+infrastructure|"
    r"colt\s+(?:dcs|data)|ark\s+data|iron\s+mountain\s+data|nscale|coreweave|"
    r"latos\s+data|segro\s+data|green\s+mountain|echelon\s+data|edgeconnex|"
    r"princeton\s+digital|apto|blackstone\s+data|microsoft|google|amazon\s+data|aws\b|"
    r"meta\s+platforms|oracle\s+cloud|teraco|data4|cloudhq|novva|stratus\s+dc)\b",
    re.I,
)
DEMAND_TYPE = re.compile(r"\bdemand\b", re.I)

STATUS_MAP = [
    # (regex on raw status, step-3 status)
    (re.compile(r"^\s*built\b|^\s*energised|^\s*connected\b|operational", re.I), "operational"),
    (re.compile(r"under\s+construction|commissioning", re.I), "under construction"),
    (re.compile(r"consents?\s+approved|consented", re.I), "consented"),
    (re.compile(r"awaiting\s+consents?|in\s+planning|planning", re.I), "in planning"),
]


def map_status(raw: str | None) -> str:
    """Map a NESO project status to the #73 enum. Anything else in a NESO
    connection register is a connection agreement/application -> 'connection request'."""
    for rx, status in STATUS_MAP:
        if raw and rx.search(raw):
            return status
    return "connection request"


def to_mw(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if s in ("", "-", "n/a", "N/A", "TBC", "tbc"):
        return None
    try:
        return round(float(s), 3)
    except ValueError:
        return None


def to_date(value) -> str | None:
    """Return ISO date or None. NESO uses dd/mm/yyyy or yyyy-mm-dd."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s[:19] if "T" in s else s, fmt).date().isoformat()
        except ValueError:
            pass
    if re.fullmatch(r"\d{5}(\.\d+)?", s):  # Excel serial
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(float(s)))).isoformat()
    return None


def pick(row: dict, *names: str):
    """Case/space-insensitive column lookup; first non-empty match wins."""
    norm = {re.sub(r"[^a-z0-9]", "", k.lower()): k for k in row if k}
    for n in names:
        k = norm.get(re.sub(r"[^a-z0-9]", "", n.lower()))
        if k is not None and str(row[k]).strip() != "":
            return str(row[k]).strip()
    return None


def pick_like(row: dict, pattern: str):
    rx = re.compile(pattern, re.I)
    for k, v in row.items():
        if k and rx.search(k) and str(v).strip():
            return k, str(v).strip()
    return None, None


def dc_rules(name, customer, ptype) -> list[str]:
    rules = []
    if name and DC_NAME.search(name):
        rules.append("name")
    if ptype and DC_NAME.search(ptype):
        rules.append("type")
    if customer and DC_NAME.search(customer):
        rules.append("customer_name")
    if customer and DC_OPERATORS.search(customer):
        rules.append("customer_is_dc_operator")
    return rules


def normalise_row(src_key: str, src: dict, row: dict, idx: int, snap: dict) -> dict:
    name = pick(row, "Project Name", "Project", "Name")
    customer = pick(row, "Customer Name", "Customer", "Developer")
    site = pick(row, "Connection Site", "Connection Site Name", "Substation", "GSP", "Grid Supply Point")
    ptype = pick(row, "Plant Type", "Technology Type", "Technology", "Type")
    raw_status = pick(row, "Project Status", "Status")
    project_id = pick(row, "Project ID", "Project Number", "Reference")
    stage = pick(row, "Stage")
    gate = pick(row, "Gate")
    host_to = pick(row, "HOST TO", "Host TO", "TO")
    agreement = pick(row, "Agreement Type")
    mw_total = to_mw(pick(row, "Cumulative Total Capacity (MW)", "Cumulative Total Capacity"))
    mw_connected = to_mw(pick(row, "MW Connected"))
    mw_delta = to_mw(pick(row, "MW Increase / Decrease", "MW Increase/Decrease"))
    mw_col = "Cumulative Total Capacity (MW)" if mw_total is not None else None
    if mw_total is None:  # EA register / unknown layout: first capacity-like column
        mw_col, val = pick_like(row, r"(capacity|\bmw\b|demand)")
        mw_total = to_mw(val)
    eff_from = to_date(pick(row, "MW Effective From", "Connection Date", "Contracted Connection Date"))

    rules = dc_rules(name, customer, ptype)
    is_demand = bool(ptype and DEMAND_TYPE.search(ptype))
    rid = f"neso-{src_key.lower()}-" + hashlib.sha256(
        json.dumps([src_key, project_id, name, site, stage, idx if not project_id else None],
                   sort_keys=True).encode()).hexdigest()[:16]
    return {
        "id": rid,
        "name": name,
        "operator": customer,  # NESO 'Customer Name' = contracting party, not necessarily the operator
        "operator_basis": "NESO Customer Name (contract holder)",
        "status": map_status(raw_status),
        "status_raw": raw_status,
        "it_load_mw": None,  # never published by NESO; NEVER derived from grid MW
        "grid_connection_mw": mw_total,
        "grid_connection_mw_basis": src["mw_basis"] if mw_total is not None else None,
        "grid_connection_mw_direction": src["mw_direction"],
        "grid_connection_mw_column": mw_col,
        "grid_connection_mw_connected": mw_connected,
        "grid_connection_mw_change": mw_delta,
        "dates": {"mw_effective_from": eff_from},
        "connection": {
            "site_name": site, "host_to": host_to, "stage": stage, "gate": gate,
            "agreement_type": agreement, "plant_or_technology_type": ptype,
            "project_id": project_id,
        },
        "location_precision": "connection_site_name" if site else "none",
        "geometry": None,
        "source": src["title"],
        "source_key": src_key,
        "source_row": idx,
        "licence": src["licence"]["name"],
        "licence_url": src["licence"].get("url"),
        "licence_fully_open": src["licence"]["fully_open"],
        "evidence_url": src.get("page"),
        "snapshot_sha256": snap.get("sha256"),
        "snapshot_url": snap.get("url"),
        "dc_match": {"is_dc_like": bool(set(rules) & {"name", "type", "customer_name"}),
                     "rules": rules},
        "large_demand": is_demand,
        "caveats": [
            "A connection-register row is an agreement or application, not a commitment to build.",
            "grid_connection_mw is contracted grid capacity, not IT load.",
        ],
    }


# ---------------------------------------------------------------- parsing

def parse_csv(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def parse_xlsx(raw: bytes) -> list[dict]:
    """Minimal stdlib xlsx reader: first worksheet with a 'Project Name' header."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
    sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))

    def col_index(ref: str) -> int:
        letters = re.match(r"[A-Z]+", ref).group(0)
        n = 0
        for ch in letters:
            n = n * 26 + ord(ch) - 64
        return n - 1

    for sheet in sheets:
        rows = []
        for r in ET.fromstring(z.read(sheet)).iter(f"{{{ns['m']}}}row"):
            vals = {}
            for c in r.findall("m:c", ns):
                v = c.find("m:v", ns)
                t = c.get("t")
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
                elif v is None:
                    continue
                elif t == "s":
                    val = shared[int(v.text)]
                else:
                    val = v.text
                vals[col_index(c.get("r"))] = val
            if vals:
                rows.append([vals.get(i, "") for i in range(max(vals) + 1)])
        for h, header in enumerate(rows[:30]):
            if any(re.sub(r"\s+", " ", str(x)).strip().lower() == "project name" for x in header):
                keys = [str(x).strip() for x in header]
                out = []
                for r in rows[h + 1:]:
                    if any(str(x).strip() for x in r):
                        out.append({keys[i] if i < len(keys) else f"col{i}": r[i] for i in range(len(r))})
                return out
    return []


# ---------------------------------------------------------------- fetching

def http_get(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def resolve_url(src: dict) -> str:
    if src["kind"] != "ckan":
        return src["url"]
    q = urllib.parse.urlencode({"id": src["resource_id"]})
    meta = json.loads(http_get(CKAN + "resource_show?" + q, timeout=60))
    url = meta["result"]["url"]
    if src["resource_id"] not in url:
        raise RuntimeError(f"resource_show returned unexpected url {url}")
    return url


def snapshot(out: Path, key: str, raw: bytes, url: str, origin: str) -> dict:
    sha = hashlib.sha256(raw).hexdigest()
    fname = Path(urllib.parse.urlparse(url).path).name or f"{key.lower()}.bin"
    dest = out / "raw" / key / f"{sha[:12]}-{fname}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return {"source": key, "url": url, "origin": origin, "bytes": len(raw), "sha256": sha,
            "path": str(dest.relative_to(out)),
            "fetched_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()}


def run(out: Path, keys: list[str], from_files: dict[str, Path]) -> int:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": SCHEMA_VERSION, "attribution": NESO_LICENCE["attribution"],
                "snapshots": [], "failures": []}
    records = []
    for key in keys:
        src = SOURCES[key]
        try:
            if key in from_files:
                p = from_files[key]
                raw, url, origin = p.read_bytes(), p.name, f"local file {p}"
            else:
                try:
                    url = resolve_url(src)
                    origin = "ckan resource_show" if src["kind"] == "ckan" else "pinned url"
                except Exception as e:  # noqa: BLE001
                    url = src.get("fallback_url") or src["url"]
                    origin = f"pinned fallback url (resolve failed: {e.__class__.__name__}: {e})"
                raw = http_get(url)
        except (urllib.error.URLError, OSError, RuntimeError) as e:
            manifest["failures"].append({"source": key, "error": f"{e.__class__.__name__}: {e}"})
            print(f"[{key}] FETCH FAILED: {e}", file=sys.stderr)
            continue
        snap = snapshot(out, key, raw, url, origin)
        manifest["snapshots"].append(snap)
        is_xlsx = raw[:2] == b"PK"
        rows = parse_xlsx(raw) if is_xlsx else parse_csv(raw)
        snap["rows"] = len(rows)
        snap["columns"] = list(rows[0].keys()) if rows else []
        for i, row in enumerate(rows):
            records.append(normalise_row(key, src, row, i, snap))
        print(f"[{key}] {len(rows)} rows, sha256 {snap['sha256'][:16]}", file=sys.stderr)

    if not manifest["snapshots"]:
        # Nothing fetched: write only the manifest. Empty record files would read
        # as "zero data centres", and unknown is not zero.
        for stale in ("neso_connections.normalised.json", "neso_dc_like.normalised.json",
                      "neso_dc_like.geojson", "counts.json"):
            (out / stale).unlink(missing_ok=True)
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
        print("no source fetched; counts UNKNOWN (not zero)", file=sys.stderr)
        return 2

    records.sort(key=lambda r: (r["source_key"], r["source_row"]))
    dc = [r for r in records if r["dc_match"]["is_dc_like"]]
    dc_op = [r for r in records if "customer_is_dc_operator" in r["dc_match"]["rules"]
             and not r["dc_match"]["is_dc_like"]]
    counts = {"rows": {}, "dc_like": {}, "dc_like_mw": {}, "dc_operator_customer_only": {},
              "large_demand_rows": {}, "large_demand_mw": {}}
    for r in records:
        k = r["source_key"]
        counts["rows"][k] = counts["rows"].get(k, 0) + 1
        if r["large_demand"]:
            counts["large_demand_rows"][k] = counts["large_demand_rows"].get(k, 0) + 1
            counts["large_demand_mw"][k] = round(counts["large_demand_mw"].get(k, 0) + (r["grid_connection_mw"] or 0), 3)
    for r in dc:
        k = r["source_key"]
        counts["dc_like"][k] = counts["dc_like"].get(k, 0) + 1
        counts["dc_like_mw"][k] = round(counts["dc_like_mw"].get(k, 0) + (r["grid_connection_mw"] or 0), 3)
    for r in dc_op:
        k = r["source_key"]
        counts["dc_operator_customer_only"][k] = counts["dc_operator_customer_only"].get(k, 0) + 1
    counts["note"] = ("dc_like_mw sums grid connection MW of dc-like rows as published; rows with "
                      "unknown MW add 0 here but are NOT zero. Multiple stages of one project can "
                      "appear as separate rows (NESO aggregates across stages), so this is not de-duplicated.")

    def dump(name, obj):
        (out / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
                                encoding="utf-8")
    dump("manifest.json", manifest)
    dump("neso_connections.normalised.json", {"schema": SCHEMA_VERSION, "records": records})
    dump("neso_dc_like.normalised.json", {"schema": SCHEMA_VERSION, "records": dc,
                                          "customer_is_dc_operator_only": dc_op})
    dump("neso_dc_like.geojson", {
        "type": "FeatureCollection",
        "note": "geometry null: NESO publishes a connection-site name only; no coordinates are invented",
        "features": [{"type": "Feature", "id": r["id"], "geometry": None,
                      "properties": {k: v for k, v in r.items() if k != "geometry"}} for r in dc]})
    dump("counts.json", counts)
    print(json.dumps(counts, indent=1), file=sys.stderr)
    return 2 if manifest["failures"] else 0


# ---------------------------------------------------------------- self-test

SELFTEST_CSV = """Project Name,Customer Name,Connection Site,Stage,MW Connected,MW Increase / Decrease,Cumulative Total Capacity (MW),MW Effective From,Project Status,Agreement Type,HOST TO,Plant Type,Project ID,Project Number,Gate
SYNTHETIC Solar Farm,SYNTHETIC Solar Ltd,Example 400kV,1,0,49.9,49.9,31/10/2029,Scoping,Directly Connected,NGET,PV Array (Photo Voltaic/solar),SYN001,1,2
SYNTHETIC Data Centre Campus BESS,SYNTHETIC Ltd,Example 275kV,1,0,100,100,2030-04-01,Awaiting Consents,Directly Connected,NGET,Energy Storage System,SYN002,2,2
SYNTHETIC Battery,Equinix (UK) Ltd,Example 132kV,1,10,0,10,01/01/2020,Built,Embedded,SPT,Energy Storage System,SYN003,3,
"""


def selftest() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        f = d / "tec-selftest.csv"
        f.write_text(SELFTEST_CSV, encoding="utf-8")
        rc = run(d / "out", ["TEC"], {"TEC": f})
        recs = json.loads((d / "out" / "neso_connections.normalised.json").read_text())["records"]
        assert rc == 0 and len(recs) == 3, (rc, len(recs))
        a, b, c = recs
        assert a["status"] == "connection request" and not a["dc_match"]["is_dc_like"]
        assert b["dc_match"]["is_dc_like"] and b["status"] == "in planning" and b["grid_connection_mw"] == 100.0
        assert b["it_load_mw"] is None and b["dates"]["mw_effective_from"] == "2030-04-01"
        assert c["status"] == "operational" and c["dc_match"]["rules"] == ["customer_is_dc_operator"]
        assert not c["dc_match"]["is_dc_like"]
        assert all(r["geometry"] is None and r["it_load_mw"] is None for r in recs)
        assert map_status("Under Construction/Commissioning") == "under construction"
        assert map_status("Consents Approved") == "consented"
        assert to_date("45000") == "2023-03-15"
    print("selftest OK (synthetic fixture only; not evidence)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    ap.add_argument("--include-ea", action="store_true",
                    help="also fetch the EA register (licence UNVERIFIED; kept separate in output fields)")
    ap.add_argument("--from-file", action="append", default=[], metavar="KEY=PATH",
                    help="use a local copy of a source instead of fetching (KEY: TEC, EMBEDDED, EA)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    keys = [k for k, s in SOURCES.items() if s["default"]] + (["EA"] if a.include_ea else [])
    ff = {}
    for spec in a.from_file:
        k, _, p = spec.partition("=")
        if k not in SOURCES or not p:
            ap.error(f"bad --from-file {spec}")
        ff[k] = Path(p)
        if k not in keys:
            keys.append(k)
    return run(a.out, keys, ff)


if __name__ == "__main__":
    sys.exit(main())
