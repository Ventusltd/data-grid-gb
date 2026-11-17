"""Offline prototype checks. Real integration uses an explicit pinned local checkout."""
import copy
import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb
from repd_columnar.export import DATES, HERE, encoded, export_product, filters, git_blob, load_source, projection, sha


PIN = json.loads((HERE / "pins/v939-source.json").read_bytes())


def synthetic():
    rows = []
    for i in range(1001):
        r = {"repd_ref": str(i + 1000), "gg_project_id": "GG2050-REPD-" + str(i + 1000),
             "technology": "solar" if i < 1000 else "bess", "capacity_mw": None if i == 0 else float(i + 1),
             "capacity_known": i != 0, "name": 'Synthetic, "Unicode" café\nrow ' + str(i),
             "relationships": [{"repd_ref": "999999", "source_field": "fixture"}], "source_row": i + 1,
             "first_seen": None if i == 0 else "2024-01-01T00:00:00Z",
             "last_seen": None if i == 0 else "2024-02-01T00:00:00Z"}
        r.update({d: "2025-01-02" if i % 2 else None for d in DATES})
        rows.append(r)
    base = "uk_renewables_pipeline/209901010000"
    parts = [{"path": "data/v9.1/projects/part-001.json", "record_count": len(rows), "sha256": sha(encoded({"record_count": len(rows), "projects": rows}))}]
    manifest = {"project_count": 1001, "solar_count": 1000, "source_record_count": 2000,
                "project_partitions": parts, "source_identity_sha256": "d" * 64}
    solar = {"ids": [str(i + 1000) for i in range(1000)], "record_count": 1001, "solar_count": 1000, "selection_count": 1000}
    blobs = {base + "/data/v9.1/build_manifest.json": encoded(manifest), base + "/solar1000/manifest.json": encoded(solar),
             "raw-descriptor.json": encoded({"expected": {"csv_sha256": "b" * 64, "xlsx_sha256": "c" * 64}}),
             base + "/data/v9.1/projects/part-001.json": encoded({"record_count": len(rows), "projects": rows})}
    pin = {"schema": "data-grid-gb.repd-columnar-input.v1", "source_commit": "a" * 40, "edition_path": base,
           "expected_rows": 1001, "expected_solar": 1000, "original_source_population": 2000,
           "solar1000_manifest_sha256": sha(blobs[base + "/solar1000/manifest.json"]),
           "raw_descriptor_path": "raw-descriptor.json",
           "raw_dataset_hashes": {"csv_sha256": "b" * 64, "xlsx_sha256": "c" * 64},
           "files": [{"path": p, "bytes": len(b), "sha256": sha(b)} for p, b in blobs.items()]}
    return rows, blobs, pin


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.rows, self.blobs, self.pin = synthetic()
        self.mock = patch("repd_columnar.export.git_blob", side_effect=lambda root, ref, path: self.blobs[path])
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_all_fields_dates_null_capacity_nested_unicode_and_order_roundtrip(self):
        out = self.root / "result"
        m = export_product(self.source, out, self.pin, limit=17, submitted_year="missing")
        self.assertEqual(m["full_row_and_typed_field_readback"], "PASS")
        with duckdb.connect() as db:
            data = db.execute("SELECT source_ordinal,repd_ref,capacity_mw,planning_application_submitted,record_json FROM read_parquet(?) ORDER BY source_ordinal", [str(out / "repd.parquet")]).fetchall()
            self.assertIsNone(data[0][2])
            self.assertIsNone(data[0][3])
            self.assertEqual(data[1][3], datetime.date(2025, 1, 2))
            self.assertEqual([json.loads(r[-1]) for r in data], self.rows)
        filtered = json.loads((out / "filtered.json").read_bytes())
        self.assertEqual(filtered["rows"], self.rows[::2][:17])
        self.assertEqual(filtered["matching_count"], 501)
        self.assertNotEqual(m["source_package_sha256"], m["raw_dataset_hashes"]["csv_sha256"])
        self.assertIsNone(filtered["rows"][0]["first_seen"])
        self.assertEqual(sha((out / "solar1000-manifest.json").read_bytes()), self.pin["solar1000_manifest_sha256"])
        measurements = json.loads((out / "measurements.json").read_bytes())
        self.assertEqual(len(measurements["samples"]), 6)
        self.assertEqual(measurements["same_selection"], "PASS")

    def test_changed_byte_pin_refuses_before_output_write(self):
        pin = copy.deepcopy(self.pin)
        pin["files"][0]["sha256"] = "0" * 64
        out = self.root / "must-not-exist"
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            export_product(self.source, out, pin)
        self.assertFalse(out.exists())
        p = copy.deepcopy(self.pin)
        p["raw_dataset_hashes"]["csv_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "raw dataset hashes"):
            load_source(self.source, p)

    def test_unknown_capacity_is_never_zero_imputed(self):
        self.rows[0]["capacity_mw"] = 0
        path = self.pin["edition_path"] + "/data/v9.1/projects/part-001.json"
        self.blobs[path] = encoded({"record_count": len(self.rows), "projects": self.rows})
        part_hash = sha(self.blobs[path])
        manifest_path = self.pin["edition_path"] + "/data/v9.1/build_manifest.json"
        manifest = json.loads(self.blobs[manifest_path])
        manifest["project_partitions"][0]["sha256"] = part_hash
        self.blobs[manifest_path] = encoded(manifest)
        for f in self.pin["files"]:
            f.update(bytes=len(self.blobs[f["path"]]), sha256=sha(self.blobs[f["path"]]))
        with self.assertRaisesRegex(ValueError, "unknown capacity"):
            load_source(self.source, self.pin)

    def test_duplicate_identity_and_invalid_day_are_refused(self):
        for mutate in [lambda r: r[1].update(repd_ref=r[0]["repd_ref"]),
                       lambda r: r[1].update(planning_application_submitted="2025-99-01")]:
            rows, blobs, pin = synthetic()
            mutate(rows)
            path = pin["edition_path"] + "/data/v9.1/projects/part-001.json"
            blobs[path] = encoded({"record_count": len(rows), "projects": rows})
            manifest_path = pin["edition_path"] + "/data/v9.1/build_manifest.json"
            manifest = json.loads(blobs[manifest_path]);manifest["project_partitions"][0]["sha256"] = sha(blobs[path]);blobs[manifest_path] = encoded(manifest)
            for f in pin["files"]:
                f.update(bytes=len(blobs[f["path"]]), sha256=sha(blobs[f["path"]]))
            with patch("repd_columnar.export.git_blob", side_effect=lambda root, ref, path: blobs[path]):
                with self.assertRaises(ValueError):
                    load_source(self.source, pin)

    def test_population_mismatch_and_solar_order_are_refused(self):
        for field, value in [("expected_rows", 7680), ("expected_solar", 3599), ("original_source_population", 14966)]:
            p = copy.deepcopy(self.pin)
            p[field] = value
            with self.assertRaises(ValueError):
                load_source(self.source, p)
        path = self.pin["edition_path"] + "/solar1000/manifest.json"
        solar = json.loads(self.blobs[path]);solar["ids"].reverse();self.blobs[path] = encoded(solar)
        self.pin["solar1000_manifest_sha256"] = sha(self.blobs[path])
        for f in self.pin["files"]:
            f.update(bytes=len(self.blobs[f["path"]]), sha256=sha(self.blobs[f["path"]]))
        with self.assertRaisesRegex(ValueError, "ordered selection"):
            load_source(self.source, self.pin)

    def test_existing_or_inside_source_output_is_refused(self):
        existing = self.root / "existing";existing.mkdir();(existing / "keep").write_text("do not replace")
        for out in [existing, self.source / "derived", HERE / "output", self.root / "x/../escape"]:
            with self.assertRaises(ValueError):
                export_product(self.source, out, self.pin)
        self.assertEqual((existing / "keep").read_text(), "do not replace")

    def test_another_git_worktree_output_is_refused(self):
        with patch("repd_columnar.export.subprocess.run") as run:
            run.return_value.returncode = 0;run.return_value.stdout = "true\n"
            with self.assertRaisesRegex(ValueError, "every Git"):
                export_product(self.source, self.root / "other-repo/output", self.pin)

    def test_filters_are_bounded_and_parameterized(self):
        for query in [{"submitted_year": "2025 OR 1=1"}, {"technology": "solar';drop table repd;--"}, {"operational_year": "0000"}]:
            with self.assertRaises(ValueError):
                filters(**query)
        clause, args = filters(technology="solar", submitted_year="2025", updated_year="missing")
        self.assertEqual(args, ["solar", 2025]);self.assertIn("IS NULL", clause)
        with self.assertRaises(ValueError):
            export_product(self.source, self.root / "bad-limit", self.pin, limit=1001)

    def test_source_path_and_mutable_ref_are_refused(self):
        self.mock.stop()
        for ref, path in [("main", "okay.json"), ("a" * 40, "../outside.json"), ("a" * 40, "C:/secret")]:
            with self.assertRaises(ValueError):
                git_blob(self.source, ref, path)


@unittest.skipUnless(os.environ.get("REPD_SOURCE_ROOT"), "Set REPD_SOURCE_ROOT for actual pinned integration")
class PinnedSourceTests(unittest.TestCase):
    def test_full_7775_record_source_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "actual-output"
            m = export_product(Path(os.environ["REPD_SOURCE_ROOT"]), out, PIN, limit=100, technology="solar", submitted_year="missing")
            self.assertEqual((m["rows"], m["solar_rows"]), (7775, 3604))
            self.assertEqual(m["solar1000_manifest_sha256"], "c625edbb9c471a1e9a9034a1afe99881fbfabb1fcadcf0623355931f5b6aed1f")
            self.assertEqual(projection(out / "repd.parquet", limit=1000, operational_year="missing")["matching_count"], 5531)


if __name__ == "__main__":
    unittest.main()
