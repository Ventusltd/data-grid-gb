"""Tests for the data-centre and conventional-generation pipelines (assetpipe, ci/verify.py, reproduce.py).

Run with ``python -m unittest discover -s tests -v``. Standard library only; the one
test that needs openpyxl skips without it, unless ASSETS_REQUIRE_DEPS=1 (the
requirements leg of CI), where a skip would hide a broken install and fails instead.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import reproduce  # noqa: E402
from assetpipe import pipeline, schema, xlsx  # noqa: E402
from ci import verify  # noqa: E402

REQUIRE_DEPS = os.environ.get("ASSETS_REQUIRE_DEPS") == "1"


def good_datacentre(**over) -> dict:
    rec = {
        "id": "fake-src:site-1",
        "name": "Example Data Centre",
        "operator": None,
        "status": "in_planning",
        "it_load_mw": None,
        "grid_connection_mw": 90,
        "dates": {"application": "2026-03"},
        "source": {"id": "fake-src", "licence": "OGL-3.0",
                   "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                   "evidence_url": "https://example.org/application/1"},
        "location": {"lon": -1.5, "lat": 52.5, "precision": "site"},
    }
    rec.update(over)
    return rec


FAKE_FETCHER = '''
import json
SOURCE_ID = "fake-src"
LANE = "TEST"
STATUS = "ready"

def raw_requests():
    return [{"file": "sites.json", "url": "https://example.org/sites.json"}]

def normalise(raw):
    rows = json.loads(raw["sites.json"])
    out = []
    for row in rows:
        out.append({
            "id": "fake-src:" + row["ref"], "name": row["name"], "operator": None,
            "status": row["status"], "it_load_mw": row.get("it"), "grid_connection_mw": row.get("grid"),
            "dates": {},
            "source": {"id": "fake-src", "licence": "OGL-3.0",
                       "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                       "evidence_url": "https://example.org/sites/" + row["ref"]},
            "location": {"lon": row["lon"], "lat": row["lat"], "precision": "site"},
        })
    return out
'''

FAKE_ROWS = [
    {"ref": "b", "name": "Site B", "status": "consented", "it": 40, "grid": 60, "lon": -0.4, "lat": 51.5},
    {"ref": "a", "name": "Site A", "status": "announced", "it": None, "grid": None, "lon": -2.2, "lat": 53.4},
]


def make_root(tmp: Path, *, open_value="yes", fetcher=FAKE_FETCHER) -> Path:
    for domain in ("datacentres", "conventional"):
        (tmp / domain / "fetchers").mkdir(parents=True)
        sources = []
        if domain == "datacentres":
            sources = [
                {"id": "fake-src", "title": "Fake", "publisher": "Test", "url": "https://example.org/",
                 "licence": "OGL-3.0",
                 "licence_url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
                 "open": open_value, "module": "fake_src", "lane": "TEST"},
                {"id": "closed-src", "title": "Closed", "publisher": "Test", "url": "https://example.org/c",
                 "licence": "proprietary", "licence_url": None, "open": "no", "module": None},
            ]
            (tmp / domain / "fetchers" / "fake_src.py").write_text(fetcher, encoding="utf-8")
        (tmp / domain / "NOTICE").write_text(
            "".join(f"{e['id']}  (open: {e['open']})\n" for e in sources), encoding="utf-8")
        (tmp / domain / "sources.json").write_text(json.dumps(
            {"schema": "gb-asset-source-registry.v1", "domain": domain, "sources": sources}), encoding="utf-8")
    return tmp


def opener_for(payload: bytes):
    calls = []

    def opener(request):
        calls.append(request["url"])
        return payload
    opener.calls = calls
    return opener


def quiet(fn, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = fn(*args, **kwargs)
    return result, out.getvalue()


class SchemaTests(unittest.TestCase):
    def test_good_record_passes(self):
        self.assertEqual(schema.validate_datacentre(good_datacentre()), [])

    def test_ambiguous_mw_field_is_rejected(self):
        for key in schema.FORBIDDEN_DATACENTRE_MW_KEYS:
            problems = schema.validate_datacentre(good_datacentre(**{key: 50}))
            self.assertTrue(any("ambiguous" in p for p in problems), key)

    def test_it_load_and_grid_connection_both_required_as_keys(self):
        rec = good_datacentre()
        del rec["it_load_mw"]
        self.assertTrue(any("it_load_mw missing" in p for p in schema.validate_datacentre(rec)))

    def test_unknown_is_null_not_zero(self):
        self.assertTrue(schema.validate_datacentre(good_datacentre(it_load_mw=0)))
        self.assertTrue(schema.validate_datacentre(good_datacentre(name="")))

    def test_no_commitment_status(self):
        self.assertTrue(schema.validate_datacentre(good_datacentre(status="committed")))

    def test_location_precision_rules(self):
        self.assertTrue(schema.validate_datacentre(good_datacentre(
            location={"lon": -1, "lat": 52, "precision": "unknown"})))
        self.assertEqual(schema.validate_datacentre(good_datacentre(
            location={"lon": None, "lat": None, "precision": "unknown"})), [])
        self.assertTrue(schema.validate_datacentre(good_datacentre(
            location={"lon": 10.0, "lat": 52, "precision": "site"})))

    def test_evidence_url_must_be_https(self):
        rec = good_datacentre()
        rec["source"]["evidence_url"] = "http://example.org/x"
        self.assertTrue(schema.validate_datacentre(rec))

    def test_conventional_capacity_needs_a_basis(self):
        rec = good_datacentre()
        for key in ("it_load_mw", "grid_connection_mw", "dates"):
            rec.pop(key)
        rec.update({"fuel": "gas", "status": "operational", "capacity_mw": 1200})
        self.assertTrue(any("capacity_basis" in p for p in schema.validate_conventional(rec)))
        rec["capacity_basis"] = "DUKES 5.11 installed capacity"
        self.assertEqual(schema.validate_conventional(rec), [])
        self.assertTrue(schema.validate_conventional({**rec, "fuel": "wind"}))

    def test_feature_has_null_geometry_when_location_unknown(self):
        feature = schema.record_to_feature(good_datacentre(
            location={"lon": None, "lat": None, "precision": "unknown"}))
        self.assertIsNone(feature["geometry"])


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="assets-test-"))
        self.payload = json.dumps(FAKE_ROWS).encode("utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fetch(self, root, payload=None, **kw):
        opener = opener_for(payload or self.payload)
        quiet(pipeline.fetch_domain, "datacentres", root=root, opener=opener,
              fetched_at="2026-10-29T00:00:00Z", **kw)
        return opener

    def test_fetch_pins_and_refetch_is_stable(self):
        root = make_root(self.tmp)
        opener = self.fetch(root)
        self.assertEqual(opener.calls, ["https://example.org/sites.json"])  # the closed source is never requested
        manifest_path = root / "datacentres/raw/MANIFEST.json"
        first = manifest_path.read_bytes()
        meta = json.loads(first)["files"]["fake-src/sites.json"]
        self.assertEqual(meta["sha256"], pipeline.sha256_bytes(self.payload))
        quiet(pipeline.fetch_domain, "datacentres", root=root, opener=opener_for(self.payload),
              fetched_at="2026-10-30T00:00:00Z")
        self.assertEqual(manifest_path.read_bytes(), first, "an unchanged re-fetch must leave no diff")

    def test_changed_upstream_fails_until_update_pins(self):
        root = make_root(self.tmp)
        self.fetch(root)
        changed = json.dumps(FAKE_ROWS[:1]).encode("utf-8")
        with self.assertRaises(pipeline.PipelineError):
            self.fetch(root, changed)
        self.fetch(root, changed, update_pins=True)
        meta = json.loads((root / "datacentres/raw/MANIFEST.json").read_text())["files"]["fake-src/sites.json"]
        self.assertEqual(meta["sha256"], pipeline.sha256_bytes(changed))

    def test_unverified_source_is_not_fetched_by_default_and_never_normalised(self):
        root = make_root(self.tmp, open_value="unverified")
        opener = self.fetch(root)
        self.assertEqual(opener.calls, [])
        self.fetch(root, include_unverified=True)
        index, _ = quiet(pipeline.normalise_domain, "datacentres", root=root)
        self.assertEqual(index["outputs"], {})

    def test_normalise_verify_reproduce_round_trip(self):
        root = make_root(self.tmp)
        self.fetch(root)
        quiet(pipeline.normalise_domain, "datacentres", root=root)
        out = root / "datacentres/normalised/fake-src.geojson"
        obj = json.loads(out.read_text())
        self.assertEqual([f["id"] for f in obj["features"]], ["fake-src:a", "fake-src:b"])  # sorted
        self.assertEqual(obj["metadata"]["licence"], "OGL-3.0")
        rc, text = quiet(verify.main, ["--root", str(root)])
        self.assertEqual(rc, 0, text)
        rc, text = quiet(reproduce.main, ["check", "--root", str(root), "--json", str(self.tmp / "s1.json")])
        self.assertEqual(rc, 0, text)
        summary = json.loads((self.tmp / "s1.json").read_text())
        src = summary["domains"]["datacentres"]["sources"]["fake-src"]
        self.assertEqual(src["rebuilt"], "identical")
        head = src["headline"]
        self.assertEqual(head["it_load_mw"], {"known": 1, "unknown": 1, "sum_of_known": 40})
        self.assertEqual(head["grid_connection_mw"], {"known": 1, "unknown": 1, "sum_of_known": 60})
        rc, _ = quiet(reproduce.main, ["check", "--root", str(root), "--expect", summary["summary_sha256"]])
        self.assertEqual(rc, 0, "the same commit must give the same summary_sha256")

    def test_verify_fails_on_tampered_raw_output_or_licence(self):
        root = make_root(self.tmp)
        self.fetch(root)
        quiet(pipeline.normalise_domain, "datacentres", root=root)
        pristine = self.tmp.parent / (self.tmp.name + "-copy")
        shutil.copytree(root, pristine)
        try:
            cases = {
                "raw": lambda r: (r / "datacentres/raw/fake-src/sites.json").write_bytes(b"[]"),
                "output": lambda r: (r / "datacentres/normalised/fake-src.geojson").write_text(
                    (r / "datacentres/normalised/fake-src.geojson").read_text().replace("Site A", "Site Z")),
                "orphan": lambda r: (r / "datacentres/raw/fake-src/extra.bin").write_bytes(b"x"),
                "notice": lambda r: (r / "datacentres/NOTICE").write_text("closed-src  (open: no)\n"),
                # registry and NOTICE agree that the source is unverified: the published output must fail
                "licence": lambda r: ((r / "datacentres/sources.json").write_text(
                    (r / "datacentres/sources.json").read_text().replace('"open": "yes"', '"open": "unverified"')),
                    (r / "datacentres/NOTICE").write_text(
                        (r / "datacentres/NOTICE").read_text().replace("(open: yes)", "(open: unverified)"))),
            }
            for name, damage in cases.items():
                work = self.tmp.parent / (self.tmp.name + "-" + name)
                shutil.copytree(pristine, work)
                try:
                    damage(work)
                    rc, text = quiet(verify.main, ["--root", str(work)])
                    self.assertEqual(rc, 1, f"{name}: verify passed a damaged tree\n{text}")
                    if name == "licence":
                        self.assertIn("not marked fully open", text)
                finally:
                    shutil.rmtree(work, ignore_errors=True)
        finally:
            shutil.rmtree(pristine, ignore_errors=True)

    def test_nondeterministic_normaliser_is_caught_by_reproduce(self):
        flaky = FAKE_FETCHER.replace('"name": row["name"]', '"name": row["name"] + " " + str(id(row))')
        root = make_root(self.tmp, fetcher=flaky)
        self.fetch(root)
        quiet(pipeline.normalise_domain, "datacentres", root=root)
        rc, text = quiet(reproduce.main, ["check", "--root", str(root)])
        self.assertEqual(rc, 1, text)
        self.assertIn("not byte-identical", text)

    def test_schema_problem_blocks_normalised_output(self):
        bad = FAKE_FETCHER.replace('"grid_connection_mw": row.get("grid")', '"mw": row.get("grid")')
        root = make_root(self.tmp, fetcher=bad)
        self.fetch(root)
        with self.assertRaises(pipeline.PipelineError):
            quiet(pipeline.normalise_domain, "datacentres", root=root)
        self.assertFalse((root / "datacentres/normalised/fake-src.geojson").exists())

    def test_network_failure_is_reported_and_keeps_previous_snapshot(self):
        root = make_root(self.tmp)
        self.fetch(root)
        before = (root / "datacentres/raw/MANIFEST.json").read_bytes()

        def blocked(request):
            raise OSError("Tunnel connection failed: 403 Forbidden")
        manifest, out = quiet(pipeline.fetch_domain, "datacentres", root=root, opener=blocked)
        self.assertEqual(manifest["failures"], ["fake-src/sites.json"])
        self.assertIn("unknown, not zero", out)
        self.assertEqual((root / "datacentres/raw/MANIFEST.json").read_bytes(), before)

    def test_unsafe_raw_file_name_is_refused(self):
        evil = FAKE_FETCHER.replace('"file": "sites.json"', '"file": "../../escape.json"')
        root = make_root(self.tmp, fetcher=evil)
        with self.assertRaises(pipeline.PipelineError):
            self.fetch(root)


class RepositoryTests(unittest.TestCase):
    def test_committed_tree_verifies(self):
        rc, text = quiet(verify.main, ["--root", str(ROOT)])
        self.assertEqual(rc, 0, text)

    def test_committed_tree_reproduces(self):
        rc, text = quiet(reproduce.main, ["check", "--root", str(ROOT)])
        self.assertEqual(rc, 0, text)

    def test_every_registered_fetcher_loads_and_placeholders_do_not_pretend(self):
        for domain in ("datacentres", "conventional"):
            for entry in pipeline.load_registry(domain)["sources"]:
                module = pipeline.load_module(domain, entry)
                if module is None:
                    self.assertEqual(entry["open"], "no", entry["id"])
                    continue
                self.assertIn(module.STATUS, ("ready", "snapshot", "placeholder"))
                if module.STATUS in ("placeholder", "snapshot"):
                    if module.STATUS == "placeholder":
                        self.assertEqual(module.raw_requests(), [])
                    with self.assertRaises(NotImplementedError):
                        module.normalise({})
                if module.STATUS != "placeholder":
                    self.assertNotEqual(entry["open"], "no", f"{entry['id']}: fetcher on a source that is not open")
                    if module.STATUS == "ready":
                        self.assertEqual(entry["open"], "yes", f"{entry['id']}: ready fetcher on a source not fully open")
                    for request in module.raw_requests():
                        self.assertTrue(request["url"].startswith("https://"), request["url"])

    def test_pinned_data_is_exempt_from_line_ending_conversion(self):
        text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
        for domain in ("datacentres", "conventional"):
            self.assertIn(f"{domain}/** -text", text, "eol=lf would rewrite CRLF snapshots and break their pins")

    def test_fetch_workflow_is_manual_only(self):
        text = (ROOT / ".github/workflows/fetch-assets.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("schedule:", text)
        self.assertNotIn("git push origin main", text)

    def test_test_workflow_runs_with_and_without_requirements(self):
        text = (ROOT / ".github/workflows/test-assets.yml").read_text(encoding="utf-8")
        self.assertIn("deps: [requirements, bare]", text)
        self.assertIn("ASSETS_REQUIRE_DEPS", text)


class XlsxTests(unittest.TestCase):
    def test_sheet_rows_round_trip(self):
        if not xlsx.available():
            if REQUIRE_DEPS:
                self.fail("openpyxl is missing but ASSETS_REQUIRE_DEPS=1 (the requirements leg)")
            with self.assertRaises(xlsx.MissingDependency):
                xlsx.sheet_rows(b"not a workbook", "5.11")
            self.skipTest("openpyxl not installed (bare leg)")
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = "5.11"
        sheet.append(["Station", "Fuel", "Installed Capacity (MW)"])
        sheet.append(["Example", "CCGT", 1200])
        buffer = io.BytesIO()
        book.save(buffer)
        rows = xlsx.sheet_rows(buffer.getvalue(), "5.11")
        self.assertEqual(rows[1], ["Example", "CCGT", 1200])
        with self.assertRaises(KeyError):
            xlsx.sheet_rows(buffer.getvalue(), "missing")


if __name__ == "__main__":
    unittest.main()
