#!/usr/bin/env python3
"""Fetch, pin and normalise the data-centre and conventional-generation sources.

    python pipelines/fetch_assets.py fetch     [datacentres|conventional|all] [--source ID ...]
                                               [--include-unverified] [--update-pins]
    python pipelines/fetch_assets.py normalise [datacentres|conventional|all] [--source ID ...]
    python pipelines/fetch_assets.py list      [datacentres|conventional|all]

``fetch`` downloads every source that is FULLY OPEN (``open: "yes"`` in
``<domain>/sources.json``) and whose fetcher is ready, writes the raw bytes under
``<domain>/raw/<source>/`` and pins each file's sha256 in ``<domain>/raw/MANIFEST.json``.
A pinned file whose upstream bytes changed fails the run until ``--update-pins``.

``normalise`` needs no network: it re-reads the pinned raw bytes (checking each
sha256), runs each source's deterministic normaliser and writes
``<domain>/normalised/<source>.geojson`` plus ``normalised/INDEX.json``.

Standard library only. A spreadsheet normaliser needs openpyxl (requirements.txt).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assetpipe import DOMAINS  # noqa: E402
from assetpipe.pipeline import (PipelineError, fetch_domain, load_module,  # noqa: E402
                                load_registry, normalise_domain)


def cmd_list(domains) -> int:
    for domain in domains:
        print(f"{domain}:")
        for entry in load_registry(domain)["sources"]:
            module = load_module(domain, entry) if entry.get("module") else None
            status = getattr(module, "STATUS", "no fetcher") if module else "no fetcher"
            print(f"  {entry['id']:<26} open={entry['open']:<10} {status:<12} {entry['licence']:<10} lane={entry.get('lane')}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("fetch", "normalise", "list"))
    parser.add_argument("domain", nargs="?", default="all", choices=(*DOMAINS, "all"))
    parser.add_argument("--source", action="append", help="limit to this source id (repeatable)")
    parser.add_argument("--include-unverified", action="store_true",
                        help="also snapshot sources whose licence is unverified (never normalised)")
    parser.add_argument("--update-pins", action="store_true", help="accept changed upstream bytes")
    args = parser.parse_args(argv)
    domains = DOMAINS if args.domain == "all" else (args.domain,)
    try:
        if args.command == "list":
            return cmd_list(domains)
        for domain in domains:
            print(f"{args.command} {domain}")
            if args.command == "fetch":
                fetch_domain(domain, only=args.source, include_unverified=args.include_unverified,
                             update_pins=args.update_pins)
            else:
                normalise_domain(domain, only=args.source)
    except PipelineError as error:
        print(f"::error::{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
