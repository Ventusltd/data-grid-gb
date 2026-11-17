#!/usr/bin/env python3
"""Offline inventory/review validation; no fetch, source enablement or live writes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from journey.contract import classify_edges, encoded, inventory, sha256


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("network", type=Path)
    p.add_argument("--points", type=Path)
    p.add_argument("--ledger", type=Path, help="Externally reviewed ledger; omitted means all withheld")
    p.add_argument("--out", type=Path, help="Optional explicit output; otherwise writes stdout")
    args = p.parse_args(argv)
    raw = args.network.read_bytes()
    result = inventory(raw, args.points.read_bytes() if args.points else None)
    if args.ledger:
        rows = classify_edges(json.loads(raw), json.loads(args.ledger.read_bytes()), sha256(raw))
        result["reviewResult"] = {"sourceRows": rows, "eligibleEdges": sum(r["eligible"] for r in rows)}
    output = encoded(result)
    if args.out:
        args.out.write_bytes(output)
    else:
        sys.stdout.buffer.write(output)


if __name__ == "__main__":
    main()
