"""Tests for the vendored-lane machinery (assetpipe.lanes, pipelines/run_lanes.py, the lane part of reproduce.py)."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reproduce  # noqa: E402
from assetpipe import lanes  # noqa: E402

BUILD = '''import json, os, sys
src = open("raw/in.txt", encoding="utf-8").read().split()
os.makedirs("out", exist_ok=True)
extra = {"noise": os.getpid()} if os.environ.get("LANE_NOISE") else {}
with open("out/result.json", "w", encoding="utf-8", newline="\\n") as fh:
    json.dump({"words": sorted(src), "retrieved": sys.argv[1] if len(sys.argv) > 1 else None, **extra}, fh, sort_keys=True)
'''
FETCH_BLOCKED = 'import sys\nprint("Tunnel connection failed: 403 Forbidden")\nsys.exit(2)\n'


def make_lane_root(tmp: Path, *, open_value="yes", env=None) -> Path:
    for domain in ("datacentres", "conventional"):
        (tmp / domain / "lanes").mkdir(parents=True)
        doc = {"schema": lanes.LANES_SCHEMA, "domain": domain, "lanes": []}
        if domain == "datacentres":
            ldir = tmp / domain / "lanes" / "fake-lane"
            (ldir / "raw").mkdir(parents=True)
            (ldir / "raw" / "in.txt").write_text("b a c", encoding="utf-8")
            (ldir / "build.py").write_text(BUILD, encoding="utf-8")
            (ldir / "fetch.py").write_text(FETCH_BLOCKED, encoding="utf-8")
            doc["lanes"].append({"id": "fake-lane", "lane": "TEST", "open": open_value, "licence": "OGL-3.0",
                                 "licence_url": "https://example.org/ogl", "fetch": [["fetch.py"]],
                                 "build": [["build.py", "{retrieved}"]], "outputs": ["out/result.json"],
                                 "env": env or {}})
        (tmp / domain / "lanes" / "lanes.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*args, **kwargs)
    return result, out.getvalue()


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lanes-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, root):
        lane = lanes.load_lanes("datacentres", root)["lanes"][0]
        ldir = lanes.lanes_dir("datacentres", root) / lane["id"]
        codes = lanes.run_commands(ldir, lane, lane["build"], today="2026-10-29", retrieved="unspecified",
                                   log=lambda *_: None)
        lanes.write_manifest("datacentres", root)
        lanes.write_manifest("conventional", root)
        return codes

    def test_build_pin_and_check(self):
        root = make_lane_root(self.tmp)
        self.assertEqual(self.build(root), [0])
        self.assertEqual(lanes.check_manifest("datacentres", root), [])
        out = root / "datacentres/lanes/fake-lane/out/result.json"
        self.assertEqual(json.loads(out.read_text())["words"], ["a", "b", "c"])

    def test_tampered_or_unpinned_file_is_caught(self):
        root = make_lane_root(self.tmp)
        self.build(root)
        out = root / "datacentres/lanes/fake-lane/out/result.json"
        out.write_text("{}", encoding="utf-8")
        self.assertTrue(any("does not match" in p for p in lanes.check_manifest("datacentres", root)))
        self.build(root)
        (root / "datacentres/lanes/fake-lane/out/stray.json").write_text("{}", encoding="utf-8")
        self.assertTrue(any("without a pin" in p for p in lanes.check_manifest("datacentres", root)))

    def test_unverified_lane_outputs_fail_strict_check_only(self):
        root = make_lane_root(self.tmp, open_value="unverified")
        self.build(root)
        self.assertTrue(lanes.check_manifest("datacentres", root, strict_open=True))
        self.assertEqual(lanes.check_manifest("datacentres", root, strict_open=False), [])
        self.assertEqual(lanes.allowed({"open": "unverified"}, False)[0], False)
        self.assertEqual(lanes.allowed({"open": "unverified"}, True)[0], True)
        self.assertEqual(lanes.allowed({"open": "no"}, True)[0], False)

    def test_closed_lane_may_not_be_vendored(self):
        root = make_lane_root(self.tmp, open_value="no")
        lanes.write_manifest("datacentres", root)
        self.assertTrue(any("not fully open" in p for p in lanes.check_manifest("datacentres", root)))

    def test_blocked_fetch_is_recorded_as_failure_not_zero(self):
        root = make_lane_root(self.tmp)
        lane = lanes.load_lanes("datacentres", root)["lanes"][0]
        ldir = lanes.lanes_dir("datacentres", root) / "fake-lane"
        codes = lanes.run_commands(ldir, lane, lane["fetch"], today="2026-10-29", retrieved="x", log=lambda *_: None)
        self.assertEqual(codes, [2])
        manifest = lanes.write_manifest("datacentres", root, fetches={
            "fake-lane": {"date": "2026-10-29", "codes": codes, "ok": False}})
        self.assertFalse(manifest["lanes"]["fake-lane"]["last_fetch"]["ok"])
        self.assertEqual(lanes.retrieved_for(manifest, "fake-lane"), "unspecified")

    def test_pinned_input_with_wrong_bytes_is_refused(self):
        root = make_lane_root(self.tmp)
        lane = {"id": "x", "inputs": [{"file": "inputs/a.bin", "url": "https://example.org/a",
                                        "sha256": hashlib.sha256(b"right").hexdigest(), "commit": False}]}
        ldir = root / "x"
        with self.assertRaises(lanes.LaneError):
            lanes.fetch_inputs(ldir, lane, opener=lambda url: b"wrong", log=lambda *_: None)
        lanes.fetch_inputs(ldir, lane, opener=lambda url: b"right", log=lambda *_: None)
        self.assertTrue(lanes.inputs_present(ldir, lane))
        self.assertEqual(lanes.tracked_files(ldir, lane), {}, "an uncommitted input is never pinned as a file")

    def test_reproduce_rebuilds_lanes_and_catches_nondeterminism(self):
        root = make_lane_root(self.tmp)
        self.build(root)
        for domain in ("datacentres", "conventional"):
            (root / domain / "sources.json").write_text(json.dumps(
                {"schema": "gb-asset-source-registry.v1", "domain": domain, "sources": []}), encoding="utf-8")
        mismatches = []
        lanes_result = reproduce.check_lanes(root, "datacentres", mismatches)
        self.assertEqual(mismatches, [])
        self.assertEqual(lanes_result["fake-lane"]["rebuilt"], "identical")
        noisy = make_lane_root(Path(tempfile.mkdtemp(prefix="lanes-noisy-")), env={})
        try:
            self.build(noisy)
            lanes_json = noisy / "datacentres/lanes/lanes.json"
            doc = json.loads(lanes_json.read_text())
            doc["lanes"][0]["env"] = {"LANE_NOISE": "/1"}  # absolute values pass through unchanged
            lanes_json.write_text(json.dumps(doc), encoding="utf-8")
            mismatches = []
            result = reproduce.check_lanes(noisy, "datacentres", mismatches)
            self.assertEqual(result["fake-lane"]["rebuilt"], "DIFFERENT")
            self.assertTrue(mismatches)
        finally:
            shutil.rmtree(noisy, ignore_errors=True)


class VendoredLaneTests(unittest.TestCase):
    def test_lane_selftests_pass(self):
        proc = subprocess.run([sys.executable, str(ROOT / "pipelines/run_lanes.py"), "selftest"],
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-3000:])

    def test_every_lane_is_open_or_explicitly_unverified(self):
        for domain in ("datacentres", "conventional"):
            for lane in lanes.load_lanes(domain)["lanes"]:
                self.assertIn(lane["open"], ("yes", "unverified"), lane["id"])
                self.assertTrue(lane.get("licence") and lane.get("licence_url", "").startswith("https://"), lane["id"])

    def test_fetch_never_runs_in_tests_or_verify(self):
        text = (ROOT / ".github/workflows/test-assets.yml").read_text(encoding="utf-8")
        self.assertNotIn("run_lanes.py fetch", text)


if __name__ == "__main__":
    unittest.main()
