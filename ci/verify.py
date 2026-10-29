#!/usr/bin/env python3
"""Verify the committed data-centre and conventional-generation data. Fails closed.

    python ci/verify.py [--root .] [--domain datacentres|conventional|all]

Checks, per domain, each only when its file exists:

- registry   ``sources.json`` and ``NOTICE``: schema, unique ids, open in yes/no/unverified, a NOTICE entry per source, a
             licence and licence URL for every open source, and no ready fetcher
             for a source that is not fully open;
- raw        ``raw/MANIFEST.json``: every committed file exists with the pinned
             bytes and sha256; no raw file sits on disk without a pin;
- outputs    ``normalised/*.geojson``: listed in ``INDEX.json`` with the same
             sha256 and count; from a registered source with ``open: "yes"``;
             licence equal to the registry's; ``raw_sha256`` equal to the pins;
             every feature a valid record (assetpipe.schema: IT load and grid
             connection MW separate, unknown is null not zero, licence and
             evidence URL present, location precision stated); unique ids;
             the three honesty sentences present.

Exit 0 when every check passes, 1 otherwise (``::error::`` lines), as
lightning's ci/verify_data.py does.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assetpipe import DOMAINS  # noqa: E402
from assetpipe.schema import OPEN_VALUES, SCHEMA_COLLECTION, VALIDATORS  # noqa: E402

HONESTY = (
    "Proximity is not connection, capacity or feasibility.",
    "A pipeline entry is not a commitment.",
    "Unknown is null, not zero.",
)
IGNORED_RAW = {"MANIFEST.json", ".gitkeep", "README.md"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_registry(ddir: Path, domain: str) -> tuple[str, dict]:
    reg = json.loads((ddir / "sources.json").read_text(encoding="utf-8"))
    if reg.get("schema") != "gb-asset-source-registry.v1" or reg.get("domain") != domain:
        raise ValueError("not a gb-asset-source-registry.v1 for this domain")
    by_id = {}
    for entry in reg.get("sources", []):
        sid = entry.get("id")
        if not sid or sid in by_id:
            raise ValueError(f"missing or duplicate source id {sid!r}")
        if entry.get("open") not in OPEN_VALUES:
            raise ValueError(f"{sid}: open must be one of {OPEN_VALUES}")
        if entry["open"] == "yes":
            for key in ("licence", "licence_url", "url", "publisher"):
                if not entry.get(key):
                    raise ValueError(f"{sid}: open source without {key}")
            if not str(entry["licence_url"]).startswith("https://"):
                raise ValueError(f"{sid}: licence_url is not https")
        module = entry.get("module")
        if module:
            path = ddir / "fetchers" / f"{module}.py"
            if not path.exists():
                raise ValueError(f"{sid}: fetcher {path.relative_to(ddir.parent)} missing")
            text = path.read_text(encoding="utf-8")
            if f'SOURCE_ID = "{sid}"' not in text:
                raise ValueError(f"{sid}: {path.name} does not declare SOURCE_ID = \"{sid}\"")
            if entry["open"] == "no" and 'STATUS = "ready"' in text:
                raise ValueError(f"{sid}: a source that is not fully open has a ready fetcher")
        by_id[sid] = entry
    notice = (ddir / "NOTICE").read_text(encoding="utf-8") if (ddir / "NOTICE").exists() else ""
    for sid, entry in by_id.items():
        if f"{sid}  (open: {entry['open']})" not in notice:
            raise ValueError(f"NOTICE has no entry '{sid}  (open: {entry['open']})' matching the registry")
    ready =sum(1 for e in by_id.values() if e.get("module") and
                'STATUS = "ready"' in (ddir / "fetchers" / f"{e['module']}.py").read_text(encoding="utf-8"))
    return f"registry ok ({len(by_id)} sources, {ready} ready)", by_id


def check_raw(ddir: Path) -> tuple[str, dict]:
    raw_dir = ddir / "raw"
    manifest_path = raw_dir / "MANIFEST.json"
    if not manifest_path.exists():
        stray = [p for p in raw_dir.rglob("*") if p.is_file() and p.name not in IGNORED_RAW] if raw_dir.exists() else []
        if stray:
            raise ValueError(f"raw files without a MANIFEST.json: {[str(p.relative_to(ddir)) for p in stray[:5]]}")
        return "raw ok (no snapshots yet)", {"files": {}}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "gb-asset-raw-manifest.v1":
        raise ValueError("raw/MANIFEST.json is not gb-asset-raw-manifest.v1")
    for rel, meta in manifest["files"].items():
        path = raw_dir / rel
        if not meta.get("committed", True):
            continue
        if not path.exists():
            raise ValueError(f"raw/{rel} is pinned but missing")
        data = path.read_bytes()
        if len(data) != meta["bytes"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise ValueError(f"raw/{rel} does not match its pinned bytes/sha256")
        if not str(meta.get("url", "")).startswith("https://"):
            raise ValueError(f"raw/{rel} was not fetched from an https URL")
    on_disk = {p.relative_to(raw_dir).as_posix() for p in raw_dir.rglob("*")
               if p.is_file() and p.name not in IGNORED_RAW}
    orphans = sorted(on_disk - set(manifest["files"]))
    if orphans:
        raise ValueError(f"raw files without a pin: {orphans[:5]}")
    return f"raw ok ({len(manifest['files'])} pinned files)", manifest


def check_outputs(ddir: Path, domain: str, registry: dict, manifest: dict) -> str:
    out_dir = ddir / "normalised"
    files = sorted(p for p in out_dir.glob("*.geojson")) if out_dir.exists() else []
    index_path = out_dir / "INDEX.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"outputs": {}}
    listed = set(index.get("outputs", {}))
    present = {p.name for p in files}
    if listed != present:
        raise ValueError(f"INDEX.json lists {sorted(listed)} but normalised/ holds {sorted(present)}")
    schema_name, validate = VALIDATORS[domain]
    total = 0
    for path in files:
        meta_index = index["outputs"][path.name]
        if sha256_file(path) != meta_index["sha256"]:
            raise ValueError(f"{path.name}: sha256 differs from INDEX.json")
        obj = json.loads(path.read_text(encoding="utf-8"))
        meta = obj.get("metadata") or {}
        if obj.get("type") != "FeatureCollection" or meta.get("schema") != SCHEMA_COLLECTION:
            raise ValueError(f"{path.name}: not a {SCHEMA_COLLECTION} FeatureCollection")
        if meta.get("record_schema") != schema_name or meta.get("domain") != domain:
            raise ValueError(f"{path.name}: record_schema/domain mismatch")
        sid = meta.get("source_id")
        entry = registry.get(sid)
        if entry is None:
            raise ValueError(f"{path.name}: source {sid!r} is not in the registry")
        if entry["open"] != "yes":
            raise ValueError(f"{path.name}: source {sid} is not marked fully open (open: {entry['open']})")
        if meta.get("licence") != entry["licence"] or meta.get("licence_url") != entry["licence_url"]:
            raise ValueError(f"{path.name}: licence differs from the registry")
        pins = {rel: m["sha256"] for rel, m in manifest["files"].items() if m.get("source_id") == sid}
        if meta.get("raw_sha256") != pins or not pins:
            raise ValueError(f"{path.name}: raw_sha256 does not equal the raw pins for {sid}")
        if list(meta.get("honesty") or []) != list(HONESTY):
            raise ValueError(f"{path.name}: the honesty sentences are missing or altered")
        features = obj.get("features")
        if not isinstance(features, list) or len(features) != meta.get("count") or len(features) != meta_index["count"]:
            raise ValueError(f"{path.name}: feature count differs from metadata/INDEX")
        seen = set()
        for i, feature in enumerate(features):
            props = (feature or {}).get("properties")
            problems = validate(props)
            if problems:
                raise ValueError(f"{path.name} feature {i}: {problems[0]}")
            if feature.get("id") != props["id"] or props["id"] in seen:
                raise ValueError(f"{path.name} feature {i}: id mismatch or duplicate {props['id']!r}")
            if (props.get("source") or {}).get("id") != sid:
                raise ValueError(f"{path.name} feature {i}: record source differs from the file's source")
            seen.add(props["id"])
            geometry = feature.get("geometry")
            if (props["location"]["precision"] == "unknown") != (geometry is None):
                raise ValueError(f"{path.name} feature {i}: geometry and location.precision disagree")
        total += len(features)
    return f"outputs ok ({len(files)} files, {total} records)"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--domain", default="all", choices=(*DOMAINS, "all"))
    args = parser.parse_args(argv)
    failed = False
    for domain in (DOMAINS if args.domain == "all" else (args.domain,)):
        ddir = args.root / domain
        if not (ddir / "sources.json").exists():
            print(f"::error::verify {domain}: sources.json missing")
            failed = True
            continue
        try:
            message, registry = check_registry(ddir, domain)
            print(f"verify {domain}: {message}")
            message, manifest = check_raw(ddir)
            print(f"verify {domain}: {message}")
            print(f"verify {domain}: {check_outputs(ddir, domain, registry, manifest)}")
        except Exception as error:  # noqa: BLE001 - reported, then exit 1
            failed = True
            print(f"::error::verify {domain}: {type(error).__name__}: {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
