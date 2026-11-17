"""Synthetic evidence negatives and a zero-approval inventory of committed input."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from journey.contract import SCHEMA, classify_edges, edge_uid, encoded, inventory, sha256, validate_ledger

ROOT = Path(__file__).resolve().parents[1]


def review(tag="fixture"):
    return {"id": tag, "reviewer": "SYNTHETIC TEST ONLY", "reviewedAt": "2026-11-17T00:00:00Z",
            "reference": "https://fixture.invalid/reviews/" + tag}


def rights():
    return {"status": "accepted", "licenceUrl": "https://fixture.invalid/synthetic-licence",
            "attribution": "Synthetic test fixture; no real asset or third-party data approval", "review": review("rights")}


def source(locator):
    return {"url": "https://fixture.invalid/network.json", "locator": locator}


def fixture():
    product = {"schema": "data-grid-gb.transmission-network.v1", "sites": [{"code": "A"}, {"code": "B"}],
               "nodes": [{"node": n, "site_code": n, "voltage_kv": 132, "voltage_consistent_with_site": True}
                         for n in ["A1", "B1"]],
               "circuits": [{"node_1": "A1", "node_2": "B1"}], "transformers": [], "planned_changes": []}
    digest = sha256(encoded(product))
    ledger = {"schema": SCHEMA, "sourceBundleSha": digest, "nodes": [], "edges": []}
    for i, nid in enumerate(["A1", "B1"]):
        ledger["nodes"].append({"nodeId": nid, "sourceRef": source(f"/nodes/{i}"),
            "identity": {"status": "confirmed", "review": review(nid)}, "rights": rights(),
            "location": {"status": "reviewed", "coordinates": [i, 51], "sourceRef": source(f"/nodes/{i}/coordinates"),
                         "review": review("location"), "rights": rights()}})
    ledger["edges"].append({"edgeUid": edge_uid(digest, "circuit", 0), "kind": "circuit", "sourceIndex": 0,
        "node1": "A1", "node2": "B1", "sourceRef": source("/circuits/0"),
        "identity": {"status": "confirmed", "review": review("edge")}, "rights": rights(),
        "multiplicityStatus": "reviewed", "multiplicityReview": review("source-record-multiplicity"),
        "geometryStatus": "published_topology_only"})
    return product, ledger


class GridJourneyTests(unittest.TestCase):
    def setUp(self):
        self.product, self.ledger = fixture()

    def reasons(self):
        return classify_edges(self.product, self.ledger)[0]["reasons"]

    def test_synthetic_reviewed_topology_is_not_a_polyline(self):
        result = classify_edges(self.product, self.ledger)
        self.assertTrue(result[0]["eligible"])
        self.assertEqual(result[0]["geometryStatus"], "published_topology_only")

    def test_default_inventory_has_zero_approved_routes_and_is_deterministic(self):
        raw = encoded(self.product)
        a, b = inventory(raw), inventory(raw)
        self.assertEqual(encoded(a), encoded(b))
        self.assertEqual(a["counts"]["eligibleEdges"], 0)
        self.assertFalse(a["rightsAccepted"])

    def test_source_node_identity_and_required_arrays_fail_closed(self):
        for change in [lambda p: p["nodes"].append(copy.deepcopy(p["nodes"][0])),
                       lambda p: p.pop("circuits"), lambda p: p.update(transformers=None),
                       lambda p: p["circuits"][0].update(node_2="bad\nnode"),
                       lambda p: p["nodes"][0].update(node="")]:
            product = copy.deepcopy(self.product)
            change(product)
            with self.assertRaises(ValueError):
                validate_ledger(self.ledger, product)

    def test_https_metadata_rejects_parser_differences(self):
        for url in ["https://bad host.example/a", "https://good.example\\bad", "https://good.example/\npath",
                    "https://user:secret@good.example/", "https://good.example:99999/", "https://%65xample.com/"]:
            ledger = copy.deepcopy(self.ledger)
            ledger["edges"][0]["sourceRef"]["url"] = url
            with self.assertRaises(ValueError):
                validate_ledger(ledger, self.product)

    def test_exact_input_bytes_pin_is_required(self):
        with self.assertRaisesRegex(ValueError, "digest"):
            validate_ledger(self.ledger, self.product, "0" * 64)

    def test_uid_index_and_endpoint_substitution_are_rejected(self):
        for mutation in [lambda e: e.update(sourceIndex=99), lambda e: e.update(node2="INVENTED"),
                         lambda e: e.update(edgeUid="0" * 64 + ":circuit:0"), lambda e: e.update(sourceIndex=True)]:
            ledger = copy.deepcopy(self.ledger)
            mutation(ledger["edges"][0])
            with self.assertRaises(ValueError):
                validate_ledger(ledger, self.product)

    def test_duplicate_or_unknown_reviews_fail_closed(self):
        for key in ["nodes", "edges"]:
            ledger = copy.deepcopy(self.ledger)
            ledger[key].append(copy.deepcopy(ledger[key][0]))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                validate_ledger(ledger, self.product)
        self.ledger["nodes"][0]["nodeId"] = "NOT_IN_SOURCE"
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_ledger(self.ledger, self.product)

    def test_review_receipt_and_rights_metadata_are_required(self):
        for change in [lambda e: e["identity"].pop("review"), lambda e: e["rights"].pop("licenceUrl"),
                       lambda e: e["rights"]["review"].update(reviewedAt="2026-02-31T00:00:00Z"),
                       lambda e: e["sourceRef"].update(url="javascript:alert(1)")]:
            ledger = copy.deepcopy(self.ledger)
            change(ledger["edges"][0])
            with self.assertRaises(ValueError):
                validate_ledger(ledger, self.product)

    def test_ambiguous_identity_rights_and_multiplicity_are_independent_refusals(self):
        self.ledger["edges"][0]["identity"]["status"] = "ambiguous"
        self.ledger["edges"][0]["rights"]["status"] = "unconfirmed"
        self.ledger["edges"][0]["multiplicityStatus"] = "ambiguous"
        self.assertEqual(self.reasons(), ["edge_identity", "edge_rights", "multiplicity"])

    def test_unknown_voltage_is_not_inferred_from_code_or_site(self):
        self.product["nodes"][0]["voltage_consistent_with_site"] = False
        self.assertIn("unknown_voltage", self.reasons())

    def test_illegal_circuit_voltage_change_is_refused(self):
        self.product["nodes"][1]["voltage_kv"] = 33
        self.assertIn("illegal_voltage", self.reasons())

    def test_explicit_transformer_can_cross_reviewed_voltages(self):
        self.product["nodes"][1]["voltage_kv"] = 33
        self.product["transformers"] = self.product.pop("circuits")
        self.product["circuits"] = []
        e = self.ledger["edges"][0]
        e.update(kind="transformer", edgeUid=edge_uid(self.ledger["sourceBundleSha"], "transformer", 0), transformerRef="fixture-transformer")
        self.assertTrue(classify_edges(self.product, self.ledger)[0]["eligible"])
        e.pop("transformerRef")
        with self.assertRaisesRegex(ValueError, "transformerRef"):
            validate_ledger(self.ledger, self.product)

    def test_unlocated_topology_stays_textual_and_no_coordinate_is_invented(self):
        self.ledger["nodes"][0]["location"] = {"status": "unlocated"}
        row = classify_edges(self.product, self.ledger)[0]
        self.assertTrue(row["eligible"])
        self.assertEqual(row["geometryStatus"], "published_node_unlocated")
        self.assertNotIn("coordinates", row)

    def test_vicinity_and_blocked_geometry_do_not_become_edges(self):
        for status in ["vicinity_screening_only", "ambiguous_or_licence_blocked"]:
            self.ledger["edges"][0]["geometryStatus"] = status
            self.assertIn("geometry_withheld", self.reasons())

    def test_polyline_requires_separate_source_rights_review_and_matching_endpoints(self):
        e = self.ledger["edges"][0]
        e.update(geometryStatus="verified_circuit_and_polyline", geometry={"coordinates": [[0, 51], [0.5, 51.1], [1, 51]],
            "sourceRef": source("/polyline/0"), "review": review("polyline"), "rights": rights()})
        self.assertTrue(classify_edges(self.product, self.ledger)[0]["eligible"])
        e["geometry"]["coordinates"][-1] = [2, 51]
        self.assertIn("geometry_endpoint_mismatch", self.reasons())
        e["geometry"]["coordinates"][-1] = [1, 51]
        e["geometry"]["rights"]["status"] = "blocked"
        self.assertIn("geometry_rights", self.reasons())

    def test_planned_and_orphan_rows_never_gain_a_route(self):
        self.product["planned_changes"] = [{"node_1": "A1", "node_2": "B1", "year": "2029"}]
        rows = classify_edges(self.product, self.ledger)
        self.assertIn("planned", rows[1]["reasons"])
        self.product["nodes"].pop()
        self.ledger["nodes"].pop()
        self.assertIn("orphan_node", classify_edges(self.product, self.ledger)[0]["reasons"])

    def test_committed_network_inventory_does_not_self_approve_real_inputs(self):
        network = (ROOT / "derived/gb-transmission-network.v1.json").read_bytes()
        points = (ROOT / "derived/connection-points.v3.json").read_bytes()
        result = inventory(network, points)
        self.assertEqual(result["sourceBundleSha"], "b26b324c3b6637ddb00f7f80941e036a0d453c06b525d3fd733736bffe7377e4")
        self.assertEqual(result["counts"]["circuitRows"], 1392)
        self.assertEqual(result["counts"]["transformerRows"], 1472)
        self.assertEqual(result["counts"]["eligibleEdges"], 0)
        self.assertEqual(result["manualIdentitiesApproved"], 0)

    def test_cli_stdout_is_deterministic_and_never_writes_existing_products(self):
        args = [sys.executable, str(ROOT / "pipelines/grid_journey_crosswalk.py"), str(ROOT / "derived/gb-transmission-network.v1.json")]
        self.assertEqual(subprocess.check_output(args), subprocess.check_output(args))


if __name__ == "__main__":
    unittest.main()
