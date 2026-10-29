"""Vendored lane scripts: run them, pin every file they read or write, and check the pins.

The #73 work lanes (DC-A1, A3, A4, A6, A7) delivered standalone, standard-library
scripts, each with its own output schema. They are vendored here unchanged, except
for a marked path-redirect block in DC-A6. ``<domain>/lanes/lanes.json`` describes
how to run each one:

    fetch     commands that use the network (run by the manual fetch workflow)
    build     offline commands that turn the lane's raw snapshots into outputs
    selftest  offline commands that test the lane on synthetic fixtures
    inputs    pinned downloads (url + sha256) a build reads; "commit": false
              means the file is fetched at run time and never committed
    open      yes / no / unverified, with the same meaning as sources.json

Each command is ``[script, arg, ...]`` and runs as ``python -I script args`` with the
lane directory as the working directory. ``{today}`` is the UTC date of this run.
``{retrieved}`` is the date of the lane's last successful fetch, or "unspecified".
``{tmp}`` is a fresh scratch directory, deleted after the command (for self-tests).

``<domain>/lanes/MANIFEST.json`` pins the sha256 and size of every committed file in
every lane directory, and records each lane's last fetch: its date and each
command's exit code. A failed fetch is recorded as a failure and the previous
snapshot is kept. Unknown is never written as zero.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .pipeline import ROOT, USER_AGENT, canonical_text, read_json, write_text

LANES_SCHEMA = "gb-asset-lanes.v1"
MANIFEST_SCHEMA = "gb-asset-lanes-manifest.v1"
IGNORED_PARTS = {"__pycache__", ".pytest_cache"}
COMMAND_TIMEOUT = 1800


class LaneError(RuntimeError):
    pass


def lanes_dir(domain: str, root: Path = ROOT) -> Path:
    return root / domain / "lanes"


def load_lanes(domain: str, root: Path = ROOT) -> dict:
    path = lanes_dir(domain, root) / "lanes.json"
    doc = read_json(path)
    if doc is None:
        return {"schema": LANES_SCHEMA, "domain": domain, "lanes": []}
    if doc.get("schema") != LANES_SCHEMA or doc.get("domain") != domain:
        raise LaneError(f"{domain}/lanes/lanes.json is not a {LANES_SCHEMA} file for {domain}")
    ids = [lane.get("id") for lane in doc.get("lanes", [])]
    if len(ids) != len(set(ids)) or not all(ids):
        raise LaneError(f"{domain}/lanes/lanes.json: missing or duplicate lane ids")
    return doc


def allowed(lane: dict, include_unverified: bool) -> tuple[bool, str]:
    if lane.get("open") == "yes":
        return True, "fully open"
    if lane.get("open") == "unverified" and include_unverified:
        return True, "licence unverified: included for review only"
    if lane.get("open") == "unverified":
        return False, "licence unverified (use --include-unverified to snapshot it for review)"
    return False, f"not fully open (open: {lane.get('open')})"


def uncommitted_inputs(lane: dict) -> set:
    return {i["file"] for i in lane.get("inputs", []) if not i.get("commit", True)}


def tracked_files(ldir: Path, lane: dict) -> dict:
    """Every committed file in a lane directory: {relative path: (sha256, bytes)}."""
    skip = uncommitted_inputs(lane)
    out = {}
    if not ldir.exists():
        return out
    for path in sorted(ldir.rglob("*")):
        rel = path.relative_to(ldir).as_posix()
        if not path.is_file() or IGNORED_PARTS & set(path.relative_to(ldir).parts) or path.suffix == ".pyc":
            continue
        if rel in skip:
            continue
        data = path.read_bytes()
        out[rel] = (hashlib.sha256(data).hexdigest(), len(data))
    return out


def _expand(arg: str, today: str, retrieved: str, tmp: str) -> str:
    return arg.replace("{today}", today).replace("{retrieved}", retrieved).replace("{tmp}", tmp)


def run_commands(ldir: Path, lane: dict, commands: list, *, today: str, retrieved: str, log=print) -> list:
    env = dict(os.environ)
    for key, value in (lane.get("env") or {}).items():
        env[key] = str(ldir / value) if not os.path.isabs(value) else value
    codes = []
    for command in commands:
        script, *args = command
        if not (ldir / script).exists():
            raise LaneError(f"{lane['id']}: script {script} missing")
        tmp = tempfile.mkdtemp(prefix=f"lane-{lane['id']}-")
        argv = [sys.executable, "-I", script, *[_expand(a, today, retrieved, tmp) for a in args]]
        log(f"  [{lane['id']}] $ {' '.join(argv[2:])}")
        try:
            proc = subprocess.run(argv, cwd=ldir, env=env, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
            code = proc.returncode
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
        except subprocess.TimeoutExpired:
            code, tail = 124, ["timed out"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        for line in tail:
            log(f"    {line}")
        log(f"  [{lane['id']}] exit {code}")
        codes.append(code)
    return codes


def fetch_inputs(ldir: Path, lane: dict, *, opener=None, log=print) -> None:
    """Download pinned inputs that are not on disk; refuse bytes whose sha256 differs."""
    for item in lane.get("inputs", []):
        path = ldir / item["file"]
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise LaneError(f"{lane['id']}: {item['file']} on disk is not the pinned bytes")
            continue
        if not item.get("url"):
            raise LaneError(f"{lane['id']}: {item['file']} is missing and has no URL")
        if opener is None:
            req = urllib.request.Request(item["url"], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=300) as response:  # noqa: S310 - pinned https URL
                data = response.read()
        else:
            data = opener(item["url"])
        digest = hashlib.sha256(data).hexdigest()
        if digest != item["sha256"]:
            raise LaneError(f"{lane['id']}: {item['url']} returned {digest[:12]}, pinned {item['sha256'][:12]}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        log(f"  [{lane['id']}] input {item['file']} {len(data):,} bytes ok")


def inputs_present(ldir: Path, lane: dict) -> bool:
    return all((ldir / i["file"]).exists() for i in lane.get("inputs", []))


def read_manifest(domain: str, root: Path = ROOT) -> dict:
    return read_json(lanes_dir(domain, root) / "MANIFEST.json",
                     {"schema": MANIFEST_SCHEMA, "domain": domain, "lanes": {}})


def retrieved_for(manifest: dict, lane_id: str) -> str:
    last = (manifest.get("lanes", {}).get(lane_id) or {}).get("last_fetch") or {}
    return last.get("date") if last.get("ok") else "unspecified"


def write_manifest(domain: str, root: Path = ROOT, *, fetches: dict | None = None) -> dict:
    doc = load_lanes(domain, root)
    old = read_manifest(domain, root)
    out = {"schema": MANIFEST_SCHEMA, "domain": domain, "lanes": {}}
    for lane in doc["lanes"]:
        ldir = lanes_dir(domain, root) / lane["id"]
        files = tracked_files(ldir, lane)
        entry = {"files": {rel: {"sha256": h, "bytes": n} for rel, (h, n) in files.items()}}
        last = (fetches or {}).get(lane["id"]) or (old.get("lanes", {}).get(lane["id"]) or {}).get("last_fetch")
        if last:
            entry["last_fetch"] = last
        out["lanes"][lane["id"]] = entry
    write_text(lanes_dir(domain, root) / "MANIFEST.json", canonical_text(out))
    return out


def check_manifest(domain: str, root: Path = ROOT, *, strict_open: bool = True) -> list:
    """Problems with the committed lane files; empty when everything matches its pin."""
    problems = []
    doc = load_lanes(domain, root)
    if not doc["lanes"]:
        return problems
    manifest = read_manifest(domain, root)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        return [f"{domain}/lanes/MANIFEST.json missing or not {MANIFEST_SCHEMA}"]
    listed = set(manifest["lanes"])
    declared = {lane["id"] for lane in doc["lanes"]}
    if listed != declared:
        problems.append(f"MANIFEST lanes {sorted(listed)} != lanes.json {sorted(declared)}")
    for lane in doc["lanes"]:
        lid = lane["id"]
        ldir = lanes_dir(domain, root) / lid
        if lane.get("open") not in ("yes", "no", "unverified"):
            problems.append(f"{lid}: open must be yes/no/unverified")
        if lane.get("open") == "no":
            problems.append(f"{lid}: a lane that is not fully open may not be vendored")
        for command in lane.get("fetch", []) + lane.get("build", []) + lane.get("selftest", []):
            if not (ldir / command[0]).exists():
                problems.append(f"{lid}: script {command[0]} missing")
        for item in lane.get("inputs", []):
            if item.get("url") and not item["url"].startswith("https://"):
                problems.append(f"{lid}: input {item['file']} URL is not https")
            path = ldir / item["file"]
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                problems.append(f"{lid}: input {item['file']} is not the pinned bytes")
        pinned = (manifest["lanes"].get(lid) or {}).get("files", {})
        actual = tracked_files(ldir, lane)
        for rel in sorted(set(pinned) | set(actual)):
            if rel not in actual:
                problems.append(f"{lid}: {rel} is pinned but missing")
            elif rel not in pinned:
                problems.append(f"{lid}: {rel} is on disk without a pin")
            elif (pinned[rel]["sha256"], pinned[rel]["bytes"]) != actual[rel]:
                problems.append(f"{lid}: {rel} does not match its pinned sha256")
        if strict_open and lane.get("open") != "yes":
            present = [o for o in lane.get("outputs", []) if (ldir / o).exists()]
            if present:
                problems.append(f"{lid}: outputs {present} from a source whose licence is {lane.get('open')}; "
                                "confirm the licence (open: yes) before merging")
    return problems


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
