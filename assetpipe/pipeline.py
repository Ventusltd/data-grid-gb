"""Fetch, pin and normalise: the one code path every source goes through.

FETCHER_CONTRACT
----------------
A source module ``<domain>/fetchers/<module>.py`` defines:

    SOURCE_ID = "osm-datacentres"        # matches its entry in <domain>/sources.json
    LANE = "DC-A4"                        # the work lane that owns it (provenance only)
    STATUS = "ready"                      # "snapshot": fetch and pin raw bytes only (no normaliser yet);
                                          # "placeholder": skipped, never fails a run

    def raw_requests() -> list[dict]:
        # Pinned requests: [{"file": "uk.json", "url": "https://...",
        #                     "data": None | bytes (POST body), "headers": {}}]
        # The same list on every call: no "latest" links, no dates computed from today.

    def normalise(raw: dict[str, bytes]) -> list[dict]:
        # raw maps each requested file name to the exact bytes on disk.
        # Returns schema records (assetpipe.schema). Must be deterministic:
        # no network, no clock, no randomness, no dependence on dict/set order.

A source is fetched only when its registry entry says ``"open": "yes"``. A source
marked ``"no"`` is never fetched. ``"unverified"`` is fetched only with
``--include-unverified``, and its records are never written to normalised/ until
the registry says yes (ci/verify.py fails if one is).

Pinning: the first fetch of a file records its sha256 in raw/MANIFEST.json. A later
fetch that returns different bytes FAILS unless ``--update-pins`` is given, so a
silently changed upstream file is a visible decision in one diff, never a quiet
change in a map.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import DOMAINS
from .schema import SCHEMA_COLLECTION, VALIDATORS, record_to_feature

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA = "gb-asset-raw-manifest.v1"
INDEX_SCHEMA = "gb-asset-normalised-index.v1"
REGISTRY_SCHEMA = "gb-asset-source-registry.v1"
USER_AGENT = "Ventus data-grid-gb asset pipeline (public open-data fetch; github.com/Ventusltd/data-grid-gb)"
MAX_RAW_COMMIT_BYTES = 25 * 1024 * 1024  # larger raw files are pinned by sha256 but not committed
HTTP_TIMEOUT = 120


class PipelineError(RuntimeError):
    pass


# --- serialisation -----------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_text(obj) -> str:
    """Stable JSON: sorted keys, UTF-8, two-space indent, one trailing newline."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def collection_text(obj: dict) -> str:
    """A FeatureCollection with one feature per line: stable, small diffs, still valid JSON."""
    head = {k: v for k, v in obj.items() if k != "features"}
    lines = [json.dumps(f, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for f in obj["features"]]
    head_text = json.dumps(head, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    body = ",\n".join(lines)
    return head_text[:-1] + ',"features":[\n' + body + ("\n" if body else "") + "]}\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# --- registry and modules ----------------------------------------------------------------------

def domain_dir(domain: str, root: Path = ROOT) -> Path:
    if domain not in DOMAINS:
        raise PipelineError(f"unknown domain {domain!r}; expected one of {DOMAINS}")
    return root / domain


def load_registry(domain: str, root: Path = ROOT) -> dict:
    registry = read_json(domain_dir(domain, root) / "sources.json")
    if not isinstance(registry, dict) or registry.get("schema") != REGISTRY_SCHEMA:
        raise PipelineError(f"{domain}/sources.json is not a {REGISTRY_SCHEMA} registry")
    ids = [s.get("id") for s in registry.get("sources", [])]
    if len(ids) != len(set(ids)):
        raise PipelineError(f"{domain}/sources.json has duplicate source ids")
    return registry


def load_module(domain: str, entry: dict, root: Path = ROOT):
    """Import <root>/<domain>/fetchers/<module>.py by path (so a test root never picks up the repo's)."""
    name = entry.get("module")
    if not name:
        return None
    path = domain_dir(domain, root) / "fetchers" / f"{name}.py"
    if not path.exists():
        raise PipelineError(f"{domain}: fetcher module {path} missing")
    key = "assetfetch_" + sha256_bytes(str(path.resolve()).encode("utf-8"))[:16] + "_" + name
    module = sys.modules.get(key)
    if module is None or getattr(module, "__file__", None) != str(path):
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    if getattr(module, "SOURCE_ID", None) != entry["id"]:
        raise PipelineError(f"{name}: SOURCE_ID {getattr(module, 'SOURCE_ID', None)!r} != registry id {entry['id']!r}")
    return module


def http_get(request: dict) -> bytes:
    headers = {"User-Agent": USER_AGENT, **(request.get("headers") or {})}
    req = urllib.request.Request(request["url"], data=request.get("data"), headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:  # noqa: S310 - pinned https URLs
        return response.read()


# --- fetch -------------------------------------------------------------------------------------

def _safe_file(name: str) -> str:
    if not name or name.startswith(("/", ".")) or ".." in name.split("/") or "\\" in name:
        raise PipelineError(f"unsafe raw file name {name!r}")
    return name


def fetch_domain(domain: str, *, root: Path = ROOT, only=None, include_unverified=False,
                 update_pins=False, opener=http_get, fetched_at=None, log=print) -> dict:
    """Download every ready, fully open source and pin it. Returns the updated manifest."""
    registry = load_registry(domain, root)
    stamp = fetched_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_dir = domain_dir(domain, root) / "raw"
    manifest_path = raw_dir / "MANIFEST.json"
    manifest = read_json(manifest_path, {"schema": MANIFEST_SCHEMA, "domain": domain, "files": {}})
    failures: list = []
    for entry in registry["sources"]:
        sid = entry["id"]
        if only and sid not in only:
            continue
        if entry.get("open") == "no":
            log(f"  skip {sid}: not fully open ({entry.get('licence')})")
            continue
        if entry.get("open") != "yes" and not include_unverified:
            log(f"  skip {sid}: licence unverified (use --include-unverified to snapshot it for review)")
            continue
        module = load_module(domain, entry, root)
        if module is None or getattr(module, "STATUS", "placeholder") not in ("ready", "snapshot"):
            log(f"  skip {sid}: fetcher is a placeholder ({entry.get('lane', 'no lane')})")
            continue
        for request in module.raw_requests():
            rel = f"{sid}/{_safe_file(request['file'])}"
            try:
                payload = opener(request)
            except OSError as error:  # network failure: recorded, previous snapshot kept, run continues
                log(f"::warning::{domain}/{rel}: fetch failed ({type(error).__name__}: {error}); "
                    "the previous snapshot, if any, is kept; unknown, not zero")
                failures.append(rel)
                continue
            digest = sha256_bytes(payload)
            pinned = manifest["files"].get(rel)
            if pinned and pinned["sha256"] != digest and not update_pins:
                raise PipelineError(
                    f"{domain}/raw/{rel}: upstream bytes changed ({pinned['sha256'][:12]} -> {digest[:12]}); "
                    "review the change and rerun with --update-pins")
            committed = len(payload) <= MAX_RAW_COMMIT_BYTES
            target = raw_dir / rel
            if committed:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            manifest["files"][rel] = {
                "source_id": sid,
                "url": request["url"],
                "method": "POST" if request.get("data") is not None else "GET",
                "bytes": len(payload),
                "sha256": digest,
                "committed": committed,
                # An unchanged file keeps the time it was first seen, so a re-fetch that
                # changes nothing leaves no diff.
                "fetched_at": pinned["fetched_at"] if pinned and pinned["sha256"] == digest else stamp,
            }
            log(f"  {rel:<48} {len(payload):>11,} bytes  {digest[:16]}{'' if committed else '  (not committed: too large)'}")
    manifest["files"] = dict(sorted(manifest["files"].items()))
    if manifest["files"] or manifest_path.exists():
        write_text(manifest_path, canonical_text(manifest))
    manifest["failures"] = failures  # reported to the caller, never written into the pins
    return manifest


# --- normalise ---------------------------------------------------------------------------------

def read_raw(domain: str, source_id: str, *, root: Path = ROOT) -> dict:
    """The raw bytes for one source, each checked against its pinned sha256."""
    raw_dir = domain_dir(domain, root) / "raw"
    manifest = read_json(raw_dir / "MANIFEST.json", {"files": {}})
    raw = {}
    for rel, meta in manifest["files"].items():
        if meta.get("source_id") != source_id:
            continue
        path = raw_dir / rel
        if not path.exists():
            raise PipelineError(f"{domain}/raw/{rel} is pinned but not on disk; fetch it first")
        data = path.read_bytes()
        if sha256_bytes(data) != meta["sha256"]:
            raise PipelineError(f"{domain}/raw/{rel} does not match its pinned sha256")
        raw[rel.split("/", 1)[1]] = data
    return raw


def build_collection(domain: str, entry: dict, records: list, raw_manifest: dict) -> dict:
    schema_name, validate = VALIDATORS[domain]
    problems = []
    seen = set()
    for rec in records:
        for p in validate(rec):
            problems.append(f"{rec.get('id') if isinstance(rec, dict) else '?'}: {p}")
        if isinstance(rec, dict):
            if rec.get("id") in seen:
                problems.append(f"{rec.get('id')}: duplicate id")
            seen.add(rec.get("id"))
            src = rec.get("source") or {}
            if src.get("id") != entry["id"] or src.get("licence") != entry["licence"]:
                problems.append(f"{rec.get('id')}: source id/licence differ from the registry entry")
    if problems:
        shown = "\n  ".join(problems[:50])
        raise PipelineError(f"{domain}/{entry['id']}: {len(problems)} schema problem(s):\n  {shown}")
    raw_files = {rel: meta["sha256"] for rel, meta in raw_manifest["files"].items()
                 if meta.get("source_id") == entry["id"]}
    return {
        "type": "FeatureCollection",
        "metadata": {
            "schema": SCHEMA_COLLECTION,
            "record_schema": schema_name,
            "domain": domain,
            "source_id": entry["id"],
            "publisher": entry.get("publisher"),
            "licence": entry["licence"],
            "licence_url": entry["licence_url"],
            "attribution": entry.get("attribution"),
            "raw_sha256": dict(sorted(raw_files.items())),
            "count": len(records),
            "honesty": [
                "Proximity is not connection, capacity or feasibility.",
                "A pipeline entry is not a commitment.",
                "Unknown is null, not zero.",
            ],
        },
        "features": [record_to_feature(r) for r in sorted(records, key=lambda r: r["id"])],
    }


def normalise_domain(domain: str, *, root: Path = ROOT, only=None, log=print) -> dict:
    """Rebuild normalised/<id>.geojson for every fully open, ready source with raw files."""
    registry = load_registry(domain, root)
    ddir = domain_dir(domain, root)
    raw_manifest = read_json(ddir / "raw" / "MANIFEST.json", {"files": {}})
    out_dir = ddir / "normalised"
    index = read_json(out_dir / "INDEX.json", {"schema": INDEX_SCHEMA, "domain": domain, "outputs": {}})
    for entry in registry["sources"]:
        sid = entry["id"]
        if only and sid not in only:
            continue
        if entry.get("open") != "yes":
            continue  # never publish a source that is not confirmed fully open
        module = load_module(domain, entry, root)
        if module is None or getattr(module, "STATUS", "placeholder") != "ready":
            continue
        raw = read_raw(domain, sid, root=root)
        if not raw:
            log(f"  skip {sid}: no raw snapshot yet")
            continue
        collection = build_collection(domain, entry, module.normalise(raw), raw_manifest)
        text = collection_text(collection)
        rel = f"{sid}.geojson"
        write_text(out_dir / rel, text)
        index["outputs"][rel] = {
            "source_id": sid,
            "count": collection["metadata"]["count"],
            "sha256": sha256_bytes(text.encode("utf-8")),
            "licence": entry["licence"],
            "share_alike": bool(entry.get("share_alike")),
        }
        log(f"  {domain}/normalised/{rel:<40} {collection['metadata']['count']:>6} records")
    index["outputs"] = dict(sorted(index["outputs"].items()))
    licences = sorted({o["licence"] for o in index["outputs"].values()})
    index["combined_licence_note"] = (
        "Outputs are per source and keep their own licence. Any product that merges an ODbL "
        "source (share_alike: true) with others is itself an ODbL database." if licences else None)
    write_text(out_dir / "INDEX.json", canonical_text(index))
    return index


def domain_paths(root: Path = ROOT):
    for domain in DOMAINS:
        yield domain, domain_dir(domain, root)


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() in ("1", "true", "yes")
