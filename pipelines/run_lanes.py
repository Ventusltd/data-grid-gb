#!/usr/bin/env python3
"""Run the vendored #73 lane scripts and pin what they produce.

    python pipelines/run_lanes.py fetch    [all|datacentres|conventional] [--lane ID ...] [--include-unverified]
    python pipelines/run_lanes.py inputs   [domain] [--lane ID ...]      # pinned downloads (url + sha256)
    python pipelines/run_lanes.py build    [domain] [--lane ID ...]      # offline
    python pipelines/run_lanes.py selftest [domain] [--lane ID ...]      # offline, synthetic fixtures
    python pipelines/run_lanes.py manifest [domain]                      # re-pin every committed lane file
    python pipelines/run_lanes.py summary  [domain]                      # markdown summary for a PR body

``fetch`` runs each permitted lane's network commands. A lane is permitted when
``open`` is "yes", or when it is "unverified" and ``--include-unverified`` is given.
A command that fails is recorded in MANIFEST.json (``last_fetch.codes``), and the
run carries on with the next lane. The previous snapshot stays in place, and a
failure never empties an output. ``build`` then rebuilds every lane offline, in
lanes.json order, so the merge lane (DC-A7) runs last. ``manifest`` re-pins the
files.

The exit status is 0 when every build command succeeds. A failed fetch does not
change the exit status: it is reported in the summary, where an unreachable source
shows as unknown, not zero. Any other failure exits 1.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assetpipe import DOMAINS  # noqa: E402
from assetpipe.lanes import (LaneError, allowed, fetch_inputs, lanes_dir, load_lanes,  # noqa: E402
                             read_manifest, retrieved_for, run_commands, today_utc, write_manifest)


def selected(doc: dict, only):
    return [lane for lane in doc["lanes"] if not only or lane["id"] in only]


def cmd_fetch(domain, only, include_unverified) -> int:
    doc = load_lanes(domain)
    fetches = {}
    today = today_utc()
    for lane in selected(doc, only):
        ok, why = allowed(lane, include_unverified)
        if not lane.get("fetch"):
            continue
        if not ok:
            print(f"  skip {lane['id']}: {why}")
            continue
        codes = run_commands(lanes_dir(domain) / lane["id"], lane, lane["fetch"], today=today,
                             retrieved=today)
        fetches[lane["id"]] = {"date": today, "codes": codes, "ok": all(c == 0 for c in codes),
                               "licence_gate": why}
    write_manifest(domain, fetches=fetches)
    return 0


def cmd_inputs(domain, only) -> int:
    for lane in selected(load_lanes(domain), only):
        fetch_inputs(lanes_dir(domain) / lane["id"], lane)
    return 0


def cmd_build(domain, only, include_unverified) -> int:
    doc = load_lanes(domain)
    manifest = read_manifest(domain)
    failed = False
    for lane in selected(doc, only):
        ok, why = allowed(lane, include_unverified)
        if not lane.get("build"):
            continue
        if not ok:
            print(f"  skip {lane['id']}: {why}")
            continue
        ldir = lanes_dir(domain) / lane["id"]
        fetch_inputs(ldir, lane)
        codes = run_commands(ldir, lane, lane["build"], today=today_utc(),
                             retrieved=retrieved_for(manifest, lane["id"]))
        failed |= any(codes)
    write_manifest(domain)
    return 1 if failed else 0


def cmd_selftest(domain, only) -> int:
    failed = False
    for lane in selected(load_lanes(domain), only):
        if lane.get("selftest"):
            failed |= any(run_commands(lanes_dir(domain) / lane["id"], lane, lane["selftest"],
                                       today=today_utc(), retrieved="selftest"))
    return 1 if failed else 0


def summary_lines(domain) -> list:
    doc = load_lanes(domain)
    manifest = read_manifest(domain)
    lines = [f"### {domain}", "", "| lane | open | last fetch | exit codes | outputs |", "|---|---|---|---|---|"]
    for lane in doc["lanes"]:
        entry = manifest.get("lanes", {}).get(lane["id"], {})
        last = entry.get("last_fetch") or {}
        outs = []
        for rel in lane.get("outputs", []):
            pin = entry.get("files", {}).get(rel)
            outs.append(f"`{rel}` {pin['sha256'][:12]}" if pin else f"`{rel}` (none)")
        fetched = last.get("date", "never") + ("" if last.get("ok", True) else " **FAILED: unknown, not zero**")
        lines.append(f"| {lane['id']} ({lane['lane']}) | {lane['open']} | {fetched} | "
                     f"{last.get('codes', '-')} | {'<br>'.join(outs)} |")
    return lines + [""]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("fetch", "inputs", "build", "selftest", "manifest", "summary"))
    parser.add_argument("domain", nargs="?", default="all", choices=(*DOMAINS, "all"))
    parser.add_argument("--lane", action="append", help="limit to this lane id (repeatable)")
    parser.add_argument("--include-unverified", action="store_true")
    args = parser.parse_args(argv)
    domains = DOMAINS if args.domain == "all" else (args.domain,)
    status = 0
    try:
        for domain in domains:
            if args.command == "summary":
                print("\n".join(summary_lines(domain)))
                continue
            print(f"{args.command} {domain}")
            if args.command == "fetch":
                status |= cmd_fetch(domain, args.lane, args.include_unverified)
            elif args.command == "inputs":
                status |= cmd_inputs(domain, args.lane)
            elif args.command == "build":
                status |= cmd_build(domain, args.lane, args.include_unverified)
            elif args.command == "selftest":
                status |= cmd_selftest(domain, args.lane)
            else:
                write_manifest(domain)
                print(json.dumps({k: len(v["files"]) for k, v in read_manifest(domain)["lanes"].items()}))
    except LaneError as error:
        print(f"::error::{error}")
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
