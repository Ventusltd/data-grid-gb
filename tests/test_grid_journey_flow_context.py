"""Bounded real observation plus synthetic adversarial mutations, no network."""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import urlencode, urlparse, urlunparse

from journey.contract import encoded, sha256
from journey.flow_context import ATTRIBUTION, PROFILE, build_context, local_clock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "journey" / "fixtures"
PINS = json.loads((FIXTURES / "neso-source-context.pins.v1.json").read_bytes())
RAW = {name: (FIXTURES / PINS[name]["path"]).read_bytes() for name in ("metadata", "sample", "receipt")}


def actual(**kwargs):
    return build_context(RAW["metadata"], RAW["sample"], RAW["receipt"], copy.deepcopy(PINS), **kwargs)


def synthetic(mutate=None, **kwargs):
    """Re-pinning here constructs synthetic tests, never a real review receipt."""
    meta, sample, receipt = (json.loads(RAW[name]) for name in ("metadata", "sample", "receipt"))
    if mutate:
        mutate(meta, sample, receipt)
    receipt["records"] = copy.deepcopy(sample["result"]["records"])
    url = urlparse(receipt["url"])
    receipt["url"] = urlunparse(url._replace(query=urlencode({"sql": sample["result"]["sql"]})))
    pins = copy.deepcopy(PINS)
    raw_meta, raw_sample = encoded(meta), encoded(sample)
    receipt.update(bytes=len(raw_sample), sha256=sha256(raw_sample))
    raw_receipt = encoded(receipt)
    for name, raw in (("metadata", raw_meta), ("sample", raw_sample), ("receipt", raw_receipt)):
        pins[name].update(bytes=len(raw), sha256=sha256(raw))
    pins["sample"]["url"] = receipt["url"]
    return build_context(raw_meta, raw_sample, raw_receipt, pins, **kwargs)


class BoundaryContextTests(unittest.TestCase):
    def assert_withheld(self, report, reason=None):
        self.assertEqual(report["status"], "WITHHELD")
        self.assertEqual(report["observations"], [])
        self.assertFalse(report["publishable"])
        self.assertFalse(report["productionEnabled"])
        for name in ("direction", "polarity", "arrow", "headroomMw", "actualOverload", "repdLink", "etysLink", "circuitLink"):
            self.assertIsNone(report[name])
        if reason:
            self.assertIn(reason, report["refusals"])

    def test_actual_three_scotex_forecasts_above_limit_are_retained(self):
        report = actual()
        self.assertEqual(report["status"], "SCALAR_RESEARCH_CONTEXT")
        self.assertEqual([r["reportedForecastMw"]["valueDecimal"] for r in report["observations"]], ["4548", "4074", "4115"])
        self.assertEqual([r["reportedLimitMw"]["valueDecimal"] for r in report["observations"]], ["3350"] * 3)
        self.assertTrue(all(Decimal(r["reportedForecastMw"]["valueDecimal"]) > Decimal(r["reportedLimitMw"]["valueDecimal"]) for r in report["observations"]))
        self.assertIsNone(report["actualOverload"])
        self.assertIsNone(report["headroomMw"])
        self.assertFalse(report["quantity"]["liveMeasurement"])

    def test_actual_raw_clocks_and_hash_scopes_are_separate(self):
        report = actual()
        self.assertEqual(report["source"]["resourceLastModified"]["raw"], "2026-11-16T16:48:01.749033")
        self.assertIsNone(report["source"]["resourceLastModified"]["utc"])
        self.assertEqual(report["source"]["observationUtc"], "2026-11-17T16:07:15.040240Z")
        self.assertEqual(report["inputHashes"]["sample"]["scope"], "bounded_api_response_bytes_not_raw_csv")
        self.assertIsNone(report["source"]["rawCsv"]["sha256"])
        self.assertNotEqual(report["inputHashes"]["sample"]["sha256"], report["inputHashes"]["metadata"]["sha256"])
        self.assertTrue(all(r["targetClock"]["utc"] is None for r in report["observations"]))

    def test_dataset_declared_licence_never_enables_publication(self):
        report = actual()
        self.assertEqual(report["rights"]["attribution"], ATTRIBUTION)
        self.assertEqual(report["rights"]["state"], "DATASET_LICENCE_DECLARED_REVIEW_REQUIRED")
        self.assertFalse(report["rights"]["productionApproved"])
        self.assertFalse(report["publishable"])
        self.assertFalse(report["productionEnabled"])

    def test_unknown_and_other_instrument_profiles_are_explicitly_unsupported(self):
        for profile in ("ukpn-primary-transformer", "elexon-interconnector", "neso-live-circuit", None, []):
            with self.subTest(profile=profile):
                self.assert_withheld(actual(profile=profile))

    def test_exact_boundary_identity_no_b6_alias(self):
        for key in ("B6", "scotex", " SCOTEX", "SCOTEX ", "SCOTEX;DROP", "СCOTEX"):
            with self.subTest(key=key):
                self.assert_withheld(actual(boundary_key=key))
        self.assertEqual(actual()["coverage"]["b6Crosswalk"], "NOT_ACCEPTED")
        self.assertFalse(actual()["coverage"]["aliasApplied"])

    def test_empty_bounded_query_does_not_claim_global_absence(self):
        def empty(_m, s, _r):
            s["result"]["records"] = []
            s["result"]["sql"] = s["result"]["sql"].replace("'SCOTEX'", "'B6'")
        report = synthetic(empty, boundary_key="B6")
        self.assertEqual(report["status"], "EMPTY_BOUNDARY_SAMPLE")
        self.assertFalse(report["coverage"]["exhaustive"])
        self.assertIsNone(report["coverage"]["totalDatasetRows"])
        self.assertIn("NO_ROWS_IN_BOUNDED_RESPONSE_NOT_EXHAUSTIVE_ABSENCE", report["refusals"])

    def test_all_three_pins_and_byte_lengths_are_required(self):
        for name in RAW:
            with self.subTest(name=name):
                data = dict(RAW)
                data[name] += b" "
                self.assert_withheld(build_context(data["metadata"], data["sample"], data["receipt"], PINS))
                pins = copy.deepcopy(PINS)
                pins[name]["sha256"] = "0" * 64
                self.assert_withheld(build_context(RAW["metadata"], RAW["sample"], RAW["receipt"], pins), "SHA256_" + name.upper())

    def test_pins_cannot_relabel_sample_as_raw_dataset_or_add_direction_grant(self):
        for change in (lambda p: p["sample"].update(scope="raw_csv_bytes"), lambda p: p.update(directionReview={"approved": True}), lambda p: p.update(attribution="")):
            pins = copy.deepcopy(PINS)
            change(pins)
            self.assert_withheld(build_context(RAW["metadata"], RAW["sample"], RAW["receipt"], pins))

    def test_wrong_dataset_resource_licence_and_missing_method_note_are_refused(self):
        changes = [lambda m: m["result"].update(name="etys"), lambda m: m["result"].update(id="wrong"),
                   lambda m: m["result"].update(licenses=[]),
                   lambda m: m["result"]["licenses"][0].update(path="https://www.gov.uk/ogl"),
                   lambda m: m["result"]["resources"][1].update(id="wrong"),
                   lambda m: m["result"]["resources"][1].update(format="MVA"),
                   lambda m: m["result"]["resources"][1].update(description="No accepted method statement")]
        for change in changes:
            with self.subTest(change=change):
                self.assert_withheld(synthetic(lambda m, _s, _r: change(m)))

    def test_units_fields_and_resource_query_cannot_be_substituted(self):
        for change in (lambda s: s["result"]["fields"][3].update(id="Flow_MVA"),
                       lambda s: s["result"]["fields"][3].update(type="text"),
                       lambda s: s["result"].update(sql=s["result"]["sql"].replace("LIMIT 3", "LIMIT 300")),
                       lambda s: s["result"].update(sql=s["result"]["sql"].replace("'SCOTEX'", "'B6'"))):
            self.assert_withheld(synthetic(lambda _m, s, _r: change(s)))

    def test_wrong_source_host_or_http_receipt_cannot_be_admitted(self):
        for change in (lambda r: r.update(http=500), lambda r: r.update(success=False),
                       lambda r: r.update(url=r["url"].replace("api.neso.energy/", "api.neso.energy.evil.example/")),
                       lambda r: r.update(url=r["url"].replace("https://", "http://"))):
            self.assert_withheld(synthetic(lambda _m, _s, r: change(r)))

    def test_malformed_source_authorities_return_refusals_not_parser_exceptions(self):
        for url in ("https://[x]/", "https://api.neso.energy:bad/api/3/action/datastore_search_sql", "https://api.neso.energy\\evil/", 1):
            receipt, pins = json.loads(RAW["receipt"]), copy.deepcopy(PINS)
            receipt["url"] = url
            raw_receipt = encoded(receipt)
            pins["sample"]["url"] = url
            pins["receipt"].update(bytes=len(raw_receipt), sha256=sha256(raw_receipt))
            self.assert_withheld(build_context(RAW["metadata"], RAW["sample"], raw_receipt, pins), "SAMPLE_SOURCE_AUTHORITY")

    def test_mixed_boundaries_and_injected_asset_links_fail_closed(self):
        self.assert_withheld(synthetic(lambda _m, s, _r: s["result"]["records"][1].update({"Constraint Group": "B6"})), "BOUNDARY_KEY_MISMATCH")
        self.assert_withheld(synthetic(lambda _m, s, _r: s["result"]["records"][0].update(repd_ref="12588")), "UNSUPPORTED_ROW_FIELDS")

    def test_missing_and_null_are_unknown_not_zero(self):
        for field in ("Flow_MW", "Limit_MW"):
            for missing in (True, False):
                def mutate(_m, s, _r):
                    row = s["result"]["records"][0]
                    if missing:
                        row.pop(field)
                    else:
                        row[field] = None
                report = synthetic(mutate)
                self.assertEqual(report["status"], "PARTIAL_SCALAR_RESEARCH_CONTEXT")
                value = report["observations"][0]["reportedForecastMw" if field == "Flow_MW" else "reportedLimitMw"]
                self.assertIsNone(value["valueDecimal"])
                self.assertEqual(value["state"], "UNKNOWN")

    def test_known_zero_and_negative_forecast_remain_scalars_without_arrows(self):
        for value in ("0", "-4548", "-0", "0.123456789123456789"):
            report = synthetic(lambda _m, s, _r: s["result"]["records"][0].update(Flow_MW=value))
            self.assertEqual(report["observations"][0]["reportedForecastMw"]["valueDecimal"], value)
            self.assertIsNone(report["direction"])
            self.assertIsNone(report["arrow"])
        report = synthetic(lambda _m, s, _r: s["result"]["records"][0].update(Limit_MW=-1))
        self.assertIsNone(report["observations"][0]["reportedLimitMw"]["valueDecimal"])

    def test_bad_numeric_units_booleans_and_nonfinite_strings_are_not_numbers(self):
        for value in (True, "NaN", "Infinity", "4500 MW", " 4500", {"value": 4500}):
            report = synthetic(lambda _m, s, _r: s["result"]["records"][0].update(Flow_MW=value))
            self.assertEqual(report["status"], "PARTIAL_SCALAR_RESEARCH_CONTEXT")
            self.assertIsNone(report["observations"][0]["reportedForecastMw"]["valueDecimal"])

    def test_dst_fold_gap_and_unoffset_targets_never_acquire_utc(self):
        for label in ("2026-10-25T01:30:00", "2026-03-29T01:30:00", "2026-07-01T12:00:00", "2026-11-17T23:30:00"):
            clock = local_clock(label)
            self.assertEqual(clock["raw"], label)
            self.assertIsNone(clock["utc"])
            self.assertEqual(clock["state"], "UTC_WITHHELD_LOCAL_OFFSET_UNRESOLVED")
            self.assertEqual(clock["intervalPosition"], "UNKNOWN")

    def test_invalid_calendar_quarter_hour_offset_and_type_remain_unresolved(self):
        for label in ("2026-02-31T12:00:00", "2026-11-17T23:15:00", "2026-11-17T23:30:00Z", True, 3.5):
            report = synthetic(lambda _m, s, _r: s["result"]["records"][0].update({"Date_ Time GMT_BST": label}))
            self.assertEqual(report["status"], "PARTIAL_SCALAR_RESEARCH_CONTEXT")
            clock = report["observations"][0]["targetClock"]
            self.assertIsNone(clock["utc"])
            self.assertIsNone(clock["civilLabel"])

    def test_duplicate_local_labels_cannot_be_disambiguated_by_array_order(self):
        def duplicate(_m, s, _r):
            s["result"]["records"][1]["Date_ Time GMT_BST"] = s["result"]["records"][0]["Date_ Time GMT_BST"]
        self.assert_withheld(synthetic(duplicate), "DUPLICATE_LOCAL_TARGET_LABEL")

    def test_observation_requires_offset_but_metadata_clock_is_not_invented_utc(self):
        self.assert_withheld(synthetic(lambda _m, _s, r: r.update(retrievedAt="2026-11-17T16:07:15")), "OBSERVATION_CLOCK_OFFSET_REQUIRED")
        report = synthetic(lambda m, _s, _r: m["result"]["resources"][1].update(last_modified=None))
        self.assertEqual(report["source"]["resourceLastModified"], {"raw": None, "utc": None, "state": "UNKNOWN"})

    def test_method_break_and_metadata_locator_follow_actual_resource(self):
        report = synthetic(lambda m, _s, _r: m["result"]["resources"].reverse())
        self.assertEqual(report["methodology"]["breakDate"], "2024-04-22")
        # Reversing three entries leaves the CSV in slot1; moving it proves the pointer is not hard-coded.
        report = synthetic(lambda m, _s, _r: m["result"]["resources"].insert(0, m["result"]["resources"].pop(1)))
        self.assertEqual(report["methodology"]["sourceLocator"], "/result/resources/0/description")
        self.assertEqual(report["methodology"]["unqualifiedCrossBreakComparison"], "WITHHELD")

    def test_bounded_rows_and_invalid_json_have_structural_refusals(self):
        self.assert_withheld(synthetic(lambda _m, s, _r: s["result"]["records"].append(copy.deepcopy(s["result"]["records"][0]))), "BOUNDED_ROW_CONTRACT")
        pins = copy.deepcopy(PINS)
        bad = b'{"value":NaN}'
        pins["sample"].update(bytes=len(bad), sha256=sha256(bad))
        self.assert_withheld(build_context(RAW["metadata"], bad, RAW["receipt"], pins), "INVALID_JSON_OR_UTF8")

    def test_report_digest_and_source_order_are_deterministic(self):
        report = actual()
        self.assertEqual(encoded(report), encoded(actual()))
        digest = report.pop("contextSha256")
        self.assertEqual(digest, sha256(encoded(report)))
        self.assertEqual([r["targetClock"]["raw"] for r in report["observations"]], ["2026-11-17T23:30:00", "2026-11-17T23:00:00", "2026-11-17T22:30:00"])

    def test_source_query_order_is_verified_never_silently_repaired(self):
        self.assert_withheld(synthetic(lambda _m, s, _r: s["result"]["records"].reverse()), "SOURCE_QUERY_ORDER_MISMATCH")

    def test_cli_is_offline_stdout_only_and_deterministic(self):
        args = [sys.executable, "-B", "-m", "journey.flow_context"]
        for name in ("metadata", "sample", "receipt"):
            args += ["--" + name, str(FIXTURES / PINS[name]["path"])]
        args += ["--pins", str(FIXTURES / "neso-source-context.pins.v1.json")]
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        with tempfile.TemporaryDirectory() as directory:
            first = subprocess.run(args, cwd=directory, env=env, capture_output=True, check=False)
            second = subprocess.run(args, cwd=directory, env=env, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(first.stderr, b"")
            self.assertEqual(list(Path(directory).iterdir()), [])
            blocked = subprocess.run(args + ["--profile", "ukpn-primary-transformer"], cwd=directory, env=env, capture_output=True, check=False)
            self.assertEqual(blocked.returncode, 2)
            self.assert_withheld(json.loads(blocked.stdout))


if __name__ == "__main__":
    unittest.main()
