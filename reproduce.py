#!/usr/bin/env python3
"""Recompute the asset pipelines' published files and numbers from the committed raw snapshots.

    python reproduce.py check [--root .] [--json OUT] [--expect SHA256]

Scope: ``datacentres/`` and ``conventional/`` only (the ETYS network products have
their own verifiers under ``derived/``).

``check`` uses no network and no credentials. For each domain it:

1. hashes every committed file under ``<domain>/raw`` and ``<domain>/normalised``
   (sha256) and derives a manifest digest over the sorted ``<sha256>  <path>`` lines;
2. checks every raw file against its pin in ``raw/MANIFEST.json``;
3. re-runs every ready normaliser on the pinned raw bytes and compares the result
   byte for byte with the committed ``normalised/<source>.geojson``: a normaliser
   that depends on the clock, the network or dict order fails here;
4. re-derives the headline numbers from the committed outputs, with the counting
   rules restated below rather than imported, so a writer bug is not repeated:
   records by status (and by fuel), and - kept apart, never added together -
   IT load MW and grid connection MW, each with how many records know it and
   how many do not. Unknown is counted as unknown, never as zero.

5. for the vendored lane scripts (``<domain>/lanes``): checks every file against
   ``lanes/MANIFEST.json``, then rebuilds each fully open lane offline in a scratch
   copy, in lanes.json order, and compares every declared output with its pin.
   A lane whose pinned inputs are not on disk is ``skipped_missing_input`` (an
   error when ``ASSETS_REQUIRE_INPUTS=1``; ``python pipelines/run_lanes.py inputs``
   downloads them by URL and sha256).

It prints a human summary and a canonical JSON summary whose ``summary_sha256`` is
the sha256 of the canonical JSON of everything except that field and the ``run``
block. Two people who run ``check`` on the same commit get the same digest; it is a
content hash, not an identity signature. Exit 0 = everything reproduced, 1 = at
least one mismatch (listed), 2 = unusable input. Normalisers that read .xlsx need
openpyxl; without it those sources are reported as ``skipped_missing_dependency``
and the run exits 1 when ``ASSETS_REQUIRE_DEPS=1``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from assetpipe.pipeline import load_module  # noqa: E402  (fetchers are loaded by path)
from assetpipe import lanes as lanelib  # noqa: E402
SCHEMA_SUMMARY = "gb-asset-reproduction.v1"
DOMAINS = ("datacentres", "conventional")  # restated, not imported


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def summary_digest(summary: dict) -> str:
    body = {k: v for k, v in summary.items() if k not in ("summary_sha256", "run")}
    return sha256_bytes(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def mw_block(values) -> dict:
    known = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return {"known": len(known), "unknown": len(values) - len(known), "sum_of_known": round(sum(known), 3)}


def headline(domain: str, features: list) -> dict:
    props = [f.get("properties") or {} for f in features]
    by_status: dict = {}
    for p in props:
        by_status[p.get("status")] = by_status.get(p.get("status"), 0) + 1
    out = {"records": len(props), "by_status": dict(sorted(by_status.items(), key=lambda kv: str(kv[0])))}
    if domain == "datacentres":
        # Two separate quantities. Adding them would be the conflation the schema forbids.
        out["it_load_mw"] = mw_block([p.get("it_load_mw") for p in props])
        out["grid_connection_mw"] = mw_block([p.get("grid_connection_mw") for p in props])
    else:
        by_fuel: dict = {}
        for p in props:
            by_fuel[p.get("fuel")] = by_fuel.get(p.get("fuel"), 0) + 1
        out["by_fuel"] = dict(sorted(by_fuel.items(), key=lambda kv: str(kv[0])))
        out["capacity_mw"] = mw_block([p.get("capacity_mw") for p in props])
    out["location_precision"] = {}
    for p in props:
        key = (p.get("location") or {}).get("precision")
        out["location_precision"][key] = out["location_precision"].get(key, 0) + 1
    out["location_precision"] = dict(sorted(out["location_precision"].items(), key=lambda kv: str(kv[0])))
    return out


def check_domain(root: Path, domain: str, mismatches: list) -> dict:
    ddir = root / domain
    registry = json.loads((ddir / "sources.json").read_text(encoding="utf-8"))
    manifest_path = ddir / "raw" / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": {}}

    lines = []
    for sub in ("raw", "normalised"):
        base = ddir / sub
        if base.exists():
            for path in sorted(p for p in base.rglob("*") if p.is_file() and p.name != ".gitkeep"):
                lines.append(f"{sha256_bytes(path.read_bytes())}  {path.relative_to(root).as_posix()}")
    files_digest = sha256_bytes(("\n".join(lines) + "\n").encode("utf-8")) if lines else None

    for rel, meta in manifest["files"].items():
        path = ddir / "raw" / rel
        if meta.get("committed", True) and (not path.exists() or sha256_bytes(path.read_bytes()) != meta["sha256"]):
            mismatches.append(f"{domain}/raw/{rel}: missing or not the pinned bytes")

    sources = {}
    for entry in registry["sources"]:
        sid, module_name = entry["id"], entry.get("module")
        out_path = ddir / "normalised" / f"{sid}.geojson"
        result = {"open": entry.get("open"), "licence": entry.get("licence")}
        module = load_module(domain, entry, root) if module_name else None
        ready = module is not None and getattr(module, "STATUS", "placeholder") == "ready"
        result["fetcher"] = getattr(module, "STATUS", "placeholder") if module else "none"
        if out_path.exists():
            committed = out_path.read_bytes()
            obj = json.loads(committed.decode("utf-8"))
            result["output_sha256"] = sha256_bytes(committed)
            result["headline"] = headline(domain, obj.get("features") or [])
            if ready and entry.get("open") == "yes":
                from assetpipe.pipeline import PipelineError, build_collection, collection_text, read_raw
                from assetpipe.xlsx import MissingDependency
                try:
                    raw = read_raw(domain, sid, root=root)
                    rebuilt = collection_text(build_collection(domain, entry, module.normalise(raw), manifest)).encode("utf-8")
                    result["rebuilt"] = "identical" if rebuilt == committed else "DIFFERENT"
                    if rebuilt != committed:
                        mismatches.append(f"{domain}/normalised/{sid}.geojson: rebuild from raw is not byte-identical")
                except MissingDependency:
                    result["rebuilt"] = "skipped_missing_dependency"
                    if os.environ.get("ASSETS_REQUIRE_DEPS") == "1":
                        mismatches.append(f"{domain}/{sid}: openpyxl missing but ASSETS_REQUIRE_DEPS=1")
                except PipelineError as error:
                    result["rebuilt"] = "error"
                    mismatches.append(f"{domain}/{sid}: {error}")
            else:
                mismatches.append(f"{domain}/normalised/{sid}.geojson exists but its source is not open+ready")
        sources[sid] = result
    return {"files_sha256": files_digest, "file_count": len(lines), "sources": sources}


def check_lanes(root: Path, domain: str, mismatches: list) -> dict:
    """Rebuild every fully open lane offline in a scratch copy and compare its outputs with the pins."""
    doc = lanelib.load_lanes(domain, root)
    if not doc["lanes"]:
        return {}
    manifest = lanelib.read_manifest(domain, root)
    for problem in lanelib.check_manifest(domain, root, strict_open=False):
        mismatches.append(f"{domain}/lanes: {problem}")
    result = {}
    scratch = Path(tempfile.mkdtemp(prefix="reproduce-lanes-"))
    try:
        work = scratch / "lanes"
        lanelib.copy_tree(lanelib.lanes_dir(domain, root), work)
        for lane in doc["lanes"]:
            lid = lane["id"]
            pins = (manifest["lanes"].get(lid) or {}).get("files", {})
            res = {"open": lane.get("open"), "outputs": {rel: (pins.get(rel) or {}).get("sha256") for rel in lane.get("outputs", [])}}
            ldir = work / lid
            if not lane.get("build"):
                res["rebuilt"] = "no offline build (fetch and build are one step)"
            elif lane.get("open") != "yes":
                res["rebuilt"] = "skipped: licence not confirmed"
            elif not lanelib.inputs_present(ldir, lane):
                res["rebuilt"] = "skipped_missing_input"
                if os.environ.get("ASSETS_REQUIRE_INPUTS") == "1":
                    mismatches.append(f"{domain}/{lid}: pinned inputs missing but ASSETS_REQUIRE_INPUTS=1")
            else:
                for rel in lane.get("outputs", []):
                    if (ldir / rel).exists():
                        (ldir / rel).unlink()
                codes = lanelib.run_commands(ldir, lane, lane["build"], today="reproduce",
                                             retrieved=lanelib.retrieved_for(manifest, lid), log=lambda *_: None)
                rebuilt = {rel: sha256_bytes((ldir / rel).read_bytes()) if (ldir / rel).exists() else None
                           for rel in lane.get("outputs", [])}
                same = not any(codes) and rebuilt == res["outputs"]
                res["rebuilt"] = "identical" if same else "DIFFERENT"
                if not same:
                    diff = [rel for rel in rebuilt if rebuilt[rel] != res["outputs"].get(rel)]
                    mismatches.append(f"{domain}/lanes/{lid}: rebuild differs from the pins ({diff or codes})")
            result[lid] = res
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


def cmd_check(args) -> int:
    root = args.root.resolve()
    for domain in DOMAINS:
        if not (root / domain / "sources.json").exists():
            print(f"reproduce: {domain}/sources.json missing", file=sys.stderr)
            return 2
    mismatches: list = []
    summary = {"schema": SCHEMA_SUMMARY, "domains": {d: check_domain(root, d, mismatches) for d in DOMAINS}}
    for d in DOMAINS:
        summary["domains"][d]["lanes"] = check_lanes(root, d, mismatches)
    summary["mismatches"] = mismatches
    summary["run"] = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "python": platform.python_version()}
    summary["summary_sha256"] = summary_digest(summary)

    for domain, block in summary["domains"].items():
        print(f"{domain}: {block['file_count']} committed data files")
        for sid, res in block["sources"].items():
            extra = ""
            if "headline" in res:
                h = res["headline"]
                extra = f" records={h['records']} rebuilt={res.get('rebuilt', '-')}"
            print(f"  {sid:<26} open={res['open']:<10} fetcher={res['fetcher']:<11}{extra}")
        for lid, res in block.get("lanes", {}).items():
            print(f"  lane {lid:<21} open={res['open']:<10} rebuilt={res['rebuilt']}")
    for line in mismatches:
        print(f"::error::reproduce {line}")
    print(f"summary_sha256 {summary['summary_sha256']}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.expect and args.expect != summary["summary_sha256"]:
        print(f"::error::reproduce summary_sha256 {summary['summary_sha256']} != expected {args.expect}")
        return 1
    return 1 if mismatches else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="recompute from the committed files")
    check.add_argument("--root", type=Path, default=ROOT)
    check.add_argument("--json", help="write the canonical summary here")
    check.add_argument("--expect", help="fail unless summary_sha256 equals this")
    args = parser.parse_args(argv)
    return cmd_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
