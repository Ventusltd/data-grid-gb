#!/usr/bin/env python3
"""Issue #76: conservative, deterministic DUKES May 2026 -> DC-A6 site join.

Only a unique physical-site name AND a unique operator identity may join.
No radius, fuzzy match, tokens, company-name inference, or MW extrapolation.
DUKES is a list of operational major-power-producer installations, NOT the
whole GB generator estate; unmatched stations retain baseline unknowns.
For different generating units with the same name, require the same published
operator, postcode AND fuel before installed capacities may be aggregated.
Conflicting historical closure/conversion statuses are not overwritten.
Reconciliation never edits the pinned original 301-site DC-A6 product.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "conventional/lanes/dc-a6/out/conventional_power_uk.geojson"
RAW = ROOT / "conventional/raw/dukes-5-11/DUKES_5.11-2026-05.xlsx"
DERIVED = ROOT / "conventional/derived"
SRC_URL = "https://assets.publishing.service.gov.uk/media/6a6a365b0ddb7e4831c629f3/DUKES_5.11.xlsx"
SRC_SHA = "760286f4cb3168f04a5ee1b581dbfcb35e1d65a5ea0151bb2dd4dc12dca2c72a"
SOURCE_DATE = "2026-05"
# A station name has identity, but its redundant *ending* 'power station'
# does not. Never discard descriptive or fuel-specific words like 'CHP',
# 'A/B', 'east', 'north', 'extension', or place identity tokens.
ENDINGS = (" power station", " power plant", " generating station", " power")
CORPORATE_SUFFIX = {"ltd", "limited", "plc"}


def site_key(value):
    s = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    for ending in ENDINGS:
        if s.endswith(ending):
            s = s[:-len(ending)].rstrip()
            break
    return s


def operator_key(value):
    tokens = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    while tokens and tokens[-1] in CORPORATE_SUFFIX:
        tokens.pop()
    return " ".join(tokens)


def join_key(name, operator):
    name_key, operator_id = site_key(name), operator_key(operator)
    return (name_key, operator_id) if name_key and operator_id else None


def reconcile(baseline, dukes_records):
    """Return (derived, evidence). Every mapping is reproducible from exact rows."""
    existing = baseline.get("features")
    if not isinstance(existing, list):
        raise ValueError("baseline has no features array")
    keyed_baseline = collections.defaultdict(list)
    for f in existing:
        p = f["properties"]
        k = join_key(p.get("name"), p.get("operator"))
        if k:
            keyed_baseline[k].append(p["id"])
    dukes_groups = collections.defaultdict(list)
    for rec in dukes_records:
        k = join_key(rec["name"], rec["operator"])
        if k:
            dukes_groups[k].append(rec)
    out = copy.deepcopy(baseline)
    matches, rejected, unmatched_baseline = [], [], []
    for feat in out["features"]:
        p = feat["properties"]
        k = join_key(p.get("name"), p.get("operator"))
        candidates = dukes_groups.get(k, []) if k else []
        why = None
        if not candidates:
            why = "no source-identical name and operator"
        elif len(keyed_baseline[k]) != 1:
            why = "ambiguous baseline site identity"
        elif len({(r["postcode_as_published"], r["fuel"]) for r in candidates}) != 1:
            why = "different DUKES postcode or fuel; distinct installations"
        elif any(r.get("capacity_mw") is None for r in candidates):
            why = "DUKES station has missing installed capacity; no partial sum"
        elif p.get("status") in ("closed", "closing", "converted", "construction"):
            why = "conflicting baseline closure/conversion/construction status; manual review"
        elif p.get("fuel") not in ("unstated", "unknown", candidates[0]["fuel"],
                                  # Old DC-A6 calls Waste 'waste' and oil/diesel 'oil'
                                  "waste" if candidates[0]["fuel"] == "efw" else None,
                                  "oil" if candidates[0]["fuel"] == "gas_oil_dual" else None):
            why = "existing fuel conflicts with DUKES; manual review"
        if why:
            unmatched_baseline.append({"id": p["id"], "name": p.get("name"), "reason": why})
            if candidates:
                rejected.append({"id": p["id"], "dukes_codes": [r["desnz_site_code"] for r in candidates], "reason": why})
            continue
        cap = round(sum(float(r["capacity_mw"]) for r in candidates), 6)
        # Never produce 0 MW; DUKES is an installed-capacity register, not
        # the amount available for a new electricity connection.
        if cap <= 0:
            raise ValueError("matched DUKES site has zero/negative installed MW")
        prev = {"fuel": p.get("fuel"), "mw": p.get("mw"), "mw_basis": p.get("mw_basis"),
                "status": p.get("status"), "status_date": p.get("status_date")}
        p["fuel"] = candidates[0]["fuel"]
        p["fuel_basis"] = "DUKES Table 5.11, Primary Fuel; named site and named operator exact join"
        p["mw"] = cap
        p["mw_basis"] = 'DUKES Table 5.11 "5.11 Full list" InstalledCapacity (MW), May 2026; not a grid connection, export limit or TEC'
        p["status"] = "operational"
        p["status_date"] = SOURCE_DATE
        p["status_basis"] = "DUKES 5.11: major power producer operational in May 2026"
        p["status_source"] = SRC_URL
        p["in_nearest_default"] = True
        p["dukes_reconciliation"] = {
            "desnz_site_codes": [r["desnz_site_code"] for r in candidates],
            "rows": [r["source_excel_row"] for r in candidates],
            "station_name_as_published": [r["name"] for r in candidates],
            "published_operator": candidates[0]["operator"],
            "published_postcode": candidates[0]["postcode_as_published"],
            "published_primary_fuel": candidates[0]["primary_fuel_as_published"],
            "published_technology": [r["technology_type"] for r in candidates],
            "source_url": SRC_URL, "source_sha256": SRC_SHA, "data_as_of": SOURCE_DATE,
            "published": "2026-07-30",
            "capacity_basis": p["mw_basis"],
            "before": prev,
            "identity_method": "unique site name + unique operator, no proximity or fuzzy match",
        }
        if not any(s.get("url") == SRC_URL for s in p.get("sources", [])):
            p.setdefault("sources", []).append({
                "name": "DESNZ DUKES Table 5.11 May 2026",
                "url": SRC_URL,
                "licence": "OGL v3.0; Contains public sector information licensed under the Open Government Licence v3.0",
            })
        matches.append({"id": p["id"], "name": p["name"], "desnz_site_codes": p["dukes_reconciliation"]["desnz_site_codes"],
                        "old_fuel": prev["fuel"], "fuel": p["fuel"], "old_mw": prev["mw"], "dukes_installed_mw": cap,
                        "old_status": prev["status"], "status": p["status"]})
    used = {code for m in matches for code in m["desnz_site_codes"]}
    unlinked = [dict(code=r["desnz_site_code"], name=r["name"], operator=r["operator"], fuel=r["fuel"],
                     capacity_mw=r["capacity_mw"]) for r in dukes_records if r["desnz_site_code"] not in used]
    out["generation"] = str(baseline.get("generation", "unknown")) + "+DUKES-2026-05-review"
    out["dukes_enrichment"] = {
        "review_only": True,
        "source_sha256": SRC_SHA,
        "licence": "OGL v3.0",
        "source_url": SRC_URL,
        "data_as_of": SOURCE_DATE,
        "method": "unique exact site + operator with same postcode and fuel for multiunit sites",
        "matched_site_count": len(matches),
        "unmatched_baseline_count": len(unmatched_baseline),
        "caveat": "Proximity is not connection, capacity or feasibility; unknown is not zero.",
    }
    out["not_included"] = "DUKES matches only uniquely identified May 2026 operational MPP sites; unmatched baseline fuel and MW are unchanged."
    evidence = {
        "source_sha256": SRC_SHA, "source_url": SRC_URL, "source_data_date": SOURCE_DATE,
        "before": {"sites": len(existing), "fuel_unstated": sum(f["properties"].get("fuel") == "unstated" for f in existing)},
        "after": {"sites": len(out["features"]), "fuel_unstated": sum(f["properties"].get("fuel") == "unstated" for f in out["features"])},
        "dukes_conventional_rows": len(dukes_records),
        "matched": len(matches), "unmatched_baseline": len(unmatched_baseline),
        "unused_dukes_rows": len(unlinked), "review_conflicts": len(rejected),
        "matches": matches, "unmatched_stations": unmatched_baseline,
        "rejected_matches": rejected, "unlinked_dukes": unlinked,
    }
    return out, evidence


def encode(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", type=Path, default=RAW)
    ap.add_argument("--baseline", type=Path, default=BASE)
    ap.add_argument("--out", type=Path, default=DERIVED)
    ap.add_argument("--if-snapshot", action="store_true", help="do nothing when no pinned workbook exists")
    ap.add_argument("--check", action="store_true", help="verify existing output is byte-identical")
    opts = ap.parse_args(argv)
    if opts.if_snapshot and not opts.raw.exists():
        print("DUKES enrichment UNVERIFIED: no pinned workbook in conventional/raw")
        return 0
    from conventional.fetchers.dukes_5_11 import RAW_FILE, normalise
    raw = opts.raw.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SRC_SHA:
        raise ValueError("unreviewed DUKES source bytes")
    base = json.loads(opts.baseline.read_bytes())
    enriched, evidence = reconcile(base, normalise({RAW_FILE: raw}))
    targets = {"uk_conventional_dukes_review.geojson": encode(enriched),
               "dukes_match_evidence.json": (json.dumps(evidence, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()}
    if opts.check:
        for name, data in targets.items():
            if (opts.out / name).read_bytes() != data:
                raise ValueError(f"{name}: byte-for-byte reproduction failed")
        print("DUKES reproducible: byte-identical", {name: hashlib.sha256(data).hexdigest() for name, data in targets.items()})
    else:
        opts.out.mkdir(parents=True, exist_ok=True)
        for name, data in targets.items():
            (opts.out / name).write_bytes(data)
            print(name, len(data), hashlib.sha256(data).hexdigest())
    print(json.dumps({k: v for k, v in evidence.items() if not isinstance(v, list)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
