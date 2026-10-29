"""Agent B (#76): source layout, fail-closed DUKES normaliser and join regressions."""
from __future__ import annotations
import hashlib
import io
import unittest
from unittest import mock

from conventional.fetchers import dukes_5_11 as dukes
from conventional.dukes_reconcile import reconcile, encode, site_key


def station(code, name, operator, fuel="gas", mw=100, postcode="SO404BD"):
    return {
        "id": "dukes-5-11:" + code.lower(), "desnz_site_code": code,
        "name": name, "operator": operator, "fuel": fuel,
        "primary_fuel_as_published": {"gas": "Natural Gas", "biomass": "Biomass",
                                       "gas_oil_dual": "Diesel/Gas Oil"}.get(fuel, fuel),
        "source_excel_row": 7, "postcode_as_published": postcode,
        "capacity_mw": mw, "technology_type": "CCGT",
    }


def baseline_site(ident, name, operator, fuel="unstated", status="unknown", mw=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-1.0, 51.0]},
            "properties": {"id": ident, "name": name, "operator": operator, "fuel": fuel,
                           "mw": mw, "mw_basis": "old source", "status": status,
                           "status_date": None, "sources": []}}


class JoinTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {"generation": "old", "features": [
            baseline_site("M", "Marchwood  Power  Station", "Marchwood Power Ltd", "gas", "operational", 842),
            baseline_site("U", "A Separate Reserve Plant", "Other Operator"),
            baseline_site("C", "Closed Site", "Closed Operator", "coal", "closed", 120),
        ]}

    def test_published_marchwood_replaces_old_installed_not_grid(self):
        output, audit = reconcile(self.baseline, [station("GAS-000354", "Marchwood Power",
                                                         "Marchwood Power", mw=898)])
        self.assertEqual(audit["matched"], 1)
        self.assertEqual(audit["after"]["sites"], 3)
        p = output["features"][0]["properties"]
        self.assertEqual(p["mw"], 898)
        self.assertEqual(p["status_date"], "2026-05")
        self.assertEqual(p["dukes_reconciliation"]["before"]["mw"], 842)
        self.assertIn("InstalledCapacity", p["mw_basis"])
        self.assertIn("not a grid connection", p["mw_basis"])
        self.assertEqual(output["features"][1]["properties"]["fuel"], "unstated")
        self.assertIsNone(output["features"][1]["properties"]["mw"])
        self.assertEqual(encode(output), encode(reconcile(self.baseline, [station(
            "GAS-000354", "Marchwood Power", "Marchwood Power", mw=898)])[0]))
        self.assertEqual(self.baseline["features"][0]["properties"]["mw"], 842, "input immutable")

    def test_operators_must_not_merge(self):
        out, report = reconcile(self.baseline, [station("GAS-000354", "Marchwood Power",
                                                       "Global Technical Realty", mw=898)])
        self.assertEqual(report["matched"], 0)
        self.assertEqual(out["features"][0]["properties"]["mw"], 842)

    def test_ambiguous_baseline_identity_rejected(self):
        a = dict(self.baseline)
        a["features"] = self.baseline["features"] + [
            baseline_site("M2", "Marchwood Power Station", "Marchwood Power")]
        _, report = reconcile(a, [station("GAS-000354", "Marchwood Power", "Marchwood Power")])
        self.assertEqual(report["matched"], 0)
        self.assertTrue(any("ambiguous baseline" in x["reason"] for x in report["rejected_matches"]))

    def test_conflicting_multiunit_rejected_not_summed(self):
        _, report = reconcile(self.baseline, [
            station("GAS-000354", "Marchwood Power", "Marchwood Power", mw=898),
            station("GAS-000355", "Marchwood Power", "Marchwood Power", fuel="biomass", mw=10)])
        self.assertEqual(report["matched"], 0)

    def test_closed_sites_remain_closed(self):
        _, report = reconcile(self.baseline, [station("COA-000111", "Closed Site", "Closed Operator", fuel="coal", mw=150)])
        self.assertEqual(report["matched"], 0)
        self.assertEqual(report["review_conflicts"], 1)

    def test_unknown_not_zero_and_no_hallucinated_capacity(self):
        out, report = reconcile(self.baseline, [])
        self.assertEqual(report["matched"], 0)
        self.assertIsNone(out["features"][1]["properties"]["mw"])
        self.assertEqual(site_key("Marchwood Power Station"), "marchwood")


class DUKESWorkbookTests(unittest.TestCase):
    def test_fixture_header_reconciliation_and_malformed_excel(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest("openpyxl absent (bare leg)")
        book = Workbook()
        sheet = book.active
        sheet.title = dukes.SHEET
        sheet.append(["Table 5.11 operational major power producer stations May 2026"])
        for _ in range(4):
            sheet.append(["notes"])
        sheet.append(list(dukes.EXPECTED_COLUMNS))
        sheet.append(["Marchwood Power", "Marchwood Power", "Fossil Fuel",
                      "CCGT", "No", "Natural Gas", None, 898, "HV",
                      "England", "South East", "SO404BD", None,
                      439654, 111095, 2009, "GAS-000354"])
        sheet.append(["Solar", "Solar Site", "Solar", "PV", "No", "Solar", None,
                      10, "LV", "England", "South", "AA111AA", None,
                      1, 2, 2024, "SOL-123456"])
        bio = io.BytesIO()
        book.save(bio)
        b = bio.getvalue()
        with mock.patch.object(dukes, "SOURCE_SHA256", hashlib.sha256(b).hexdigest()):
            records = dukes.normalise({dukes.RAW_FILE: b})
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["capacity_mw"], 898.0)
            self.assertEqual(records[0]["fuel"], "gas")
            self.assertEqual(records[0]["location"]["precision"], "unknown")
            self.assertEqual(records[0]["desnz_site_code"], "GAS-000354")
            self.assertIsNone(records[0]["location"]["lon"])
            with self.assertRaises(ValueError):
                dukes.normalise({dukes.RAW_FILE: b + b"corrupt"})
            sheet["F6"] = "Changed header"
            next_bio = io.BytesIO()
            book.save(next_bio)
            with mock.patch.object(dukes, "SOURCE_SHA256", hashlib.sha256(next_bio.getvalue()).hexdigest()):
                with self.assertRaisesRegex(ValueError, "header changed"):
                    dukes.normalise({dukes.RAW_FILE: next_bio.getvalue()})
