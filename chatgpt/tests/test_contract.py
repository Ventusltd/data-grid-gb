import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FeedContractTest(unittest.TestCase):
    def test_mobile_acceptance_words_are_exact(self):
        contract = (ROOT / "PRODUCT_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("Mobile is the sales surface; it must answer immediately.", contract)

    def test_source_ledger_is_complete_and_hashes_are_well_formed(self):
        ledger = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(ledger["artifacts"]),
            {"appendix_a", "appendix_b", "appendix_c", "fault_minimum", "fault_peak"},
        )
        for artifact in ledger["artifacts"].values():
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(artifact["bytes"], 0)
            self.assertTrue(artifact["url"].startswith("https://www.neso.energy/document/"))

    def test_real_product_if_built(self):
        path = ROOT / "derived" / "etys-2025.normalized.json"
        if not path.exists():
            self.skipTest("derived product has not been built")
        raw = path.read_bytes()
        data = json.loads(raw)
        self.assertEqual(data["counts"]["sites"], len(data["sites"]))
        self.assertEqual(data["counts"]["fault_scenarios"], len(data["fault_scenarios"]))
        sidecar = path.with_suffix(path.suffix + ".sha256").read_text().split()[0]
        self.assertEqual(sidecar, hashlib.sha256(raw).hexdigest())
        cottam = [r for r in data["fault_scenarios"] if r["demand_case"] == "peak"
                  and r["winter"] == "2025/26" and r["node"] == "COTT4 M1"]
        self.assertEqual(len(cottam), 1)
        self.assertAlmostEqual(cottam[0]["three_phase_initial_peak_current_ka"],
                               109.219270174868, places=10)
        self.assertIn("three_phase_rms_break_current_ka", cottam[0])


if __name__ == "__main__":
    unittest.main()
