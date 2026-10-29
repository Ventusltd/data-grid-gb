#!/usr/bin/env python3
"""Synthetic unit tests for dcschema / dcmerge / dcingest (stdlib unittest).

Run: python3 -m unittest -v test_dcmerge   (from this directory)
     DC_NO_JSONSCHEMA=1 ... hides the optional jsonschema package.
"""
import copy
import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if os.environ.get("DC_NO_JSONSCHEMA"):
    sys.modules["jsonschema"] = None  # import -> ImportError
import dcingest  # noqa: E402
import dcmerge  # noqa: E402
import dcschema  # noqa: E402

LIC = {"OGL-UK-3.0": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
       "ODbL-1.0": "https://opendatacommons.org/licenses/odbl/1-0/",
       "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/"}


def mk(lane, dataset, ref, name, lat=None, lon=None, precision="exact_site", operator=None,
       status="unknown", it=None, grid=None, uncl=None, plan=None, conn=None, licence="OGL-UK-3.0",
       url="https://example.org/x", dates=None):
    sid = "%s:%s:%s" % (lane, dataset, ref)
    r = {
        "name": name, "aliases": [], "operator": operator, "status": status,
        "status_source_id": sid if status != "unknown" else None,
        "it_load_mw": {"value": it, "source_id": sid if it is not None else None, "label": None},
        "grid_connection_mw": {"value": grid, "source_id": sid if grid is not None else None, "label": None},
        "unclassified_mw": {"value": uncl, "source_id": sid if uncl is not None else None, "label": None},
        "dates": dict({f: None for f in dcmerge.DATE_FIELDS}, **(dates or {})),
        "location": {"lat": lat, "lon": lon, "precision": precision if lat is not None else "unknown",
                     "source_id": sid if lat is not None else None, "local_authority": None, "postcode": None},
        "planning_refs": [{"authority": a, "reference": b, "url": None} for a, b in (plan or [])],
        "connection_refs": list(conn or []),
        "sources": [{"id": sid, "lane": lane, "dataset": dataset, "record_ref": ref, "url": url,
                     "licence": licence, "licence_url": LIC.get(licence, "https://example.org/l"),
                     "licence_status": "unverified", "attribution": None, "retrieved": "2026-10-29",
                     "sha256": None}],
        "conflicts": [], "notes": [],
    }
    r["site_key"] = dcmerge.geo_key(r)
    r["id"] = dcmerge.site_id(r["site_key"])
    return r


# Slough-ish coordinates
SL = (51.5230, -0.6290)


class SchemaTests(unittest.TestCase):
    def test_valid_record(self):
        r = mk("DC-A1", "osm", "w1", "Alpha DC", *SL, it=10)
        self.assertEqual(dcschema.validate(r, cross_check=True), [])

    def test_status_enum(self):
        r = mk("DC-A1", "osm", "w1", "Alpha DC", *SL)
        r["status"] = "maybe"
        self.assertTrue(any("enum" in e for e in dcschema.validate(r)))

    def test_mw_needs_source(self):
        r = mk("DC-A1", "osm", "w1", "Alpha DC", *SL)
        r["grid_connection_mw"] = {"value": 50, "source_id": None, "label": None}
        self.assertTrue(any("without source_id" in e for e in dcschema.validate(r)))

    def test_mw_source_must_exist(self):
        r = mk("DC-A1", "osm", "w1", "Alpha DC", *SL)
        r["it_load_mw"] = {"value": 5, "source_id": "nope", "label": None}
        self.assertTrue(any("not in sources" in e for e in dcschema.validate(r)))

    def test_non_open_licence_rejected(self):
        r = mk("DC-A1", "x", "1", "Alpha DC", *SL)
        r["sources"][0]["licence"] = "CC-BY-NC-4.0"
        self.assertTrue(any("enum" in e for e in dcschema.validate(r)))

    def test_dates_and_precision(self):
        r = mk("DC-A1", "x", "1", "Alpha DC", *SL, dates={"consented": "2026-13"})
        r["dates"]["consented"] = "26/10/2026"
        r["location"]["precision"] = "vague"
        errs = dcschema.validate(r)
        self.assertTrue(any("dates.consented" in e for e in errs))
        self.assertTrue(any("precision" in e for e in errs))

    def test_half_point(self):
        r = mk("DC-A1", "x", "1", "Alpha DC", *SL)
        r["location"]["lon"] = None
        self.assertTrue(any("both" in e for e in dcschema.validate(r)))

    def test_id_must_hash_key(self):
        r = mk("DC-A1", "x", "1", "Alpha DC", *SL)
        r["id"] = "dcuk-0000000000000000"
        self.assertTrue(any("hash of site_key" in e for e in dcschema.validate(r)))

    def test_jsonschema_agrees_on_structure(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = dcschema.load_schema()
        good = mk("DC-A1", "x", "1", "Alpha DC", *SL, it=3)
        bad = copy.deepcopy(good)
        bad["status"] = "nope"
        v = jsonschema.Draft202012Validator(schema)
        self.assertTrue(v.is_valid(good))
        self.assertFalse(v.is_valid(bad))


class NormaliserTests(unittest.TestCase):
    def test_name_similarity(self):
        self.assertEqual(dcmerge.name_similarity("Alpha Data Centre", "ALPHA datacenter"), 1.0)
        self.assertGreaterEqual(dcmerge.name_similarity("Blackwall Yard DC", "Blackwall Yard Data Center Campus"), 0.85)
        self.assertLess(dcmerge.name_similarity("Alpha", "Omega"), 0.5)
        self.assertEqual(dcmerge.name_similarity("Data Centre", "Data Centre"), 0.0)  # uninformative

    def test_operator_match(self):
        self.assertTrue(dcmerge.operator_match("Equinix (UK) Ltd", "EQUINIX"))
        self.assertFalse(dcmerge.operator_match("Equinix", "Digital Realty"))
        self.assertIsNone(dcmerge.operator_match(None, "Equinix"))

    def test_designators(self):
        self.assertNotEqual(dcmerge.designators("Equinix LD4"), dcmerge.designators("Equinix LD5"))
        self.assertEqual(dcmerge.designators("Building 2"), frozenset({"building2"}))

    def test_haversine(self):
        self.assertAlmostEqual(dcmerge.haversine_km(51.5, 0, 51.5, 0), 0)
        self.assertAlmostEqual(dcmerge.haversine_km(51.0, 0, 52.0, 0), 111.19, delta=0.1)

    def test_licence_mapping(self):
        f = dcingest.norm_licence
        self.assertEqual(f("OGL v3"), "OGL-UK-3.0")
        self.assertEqual(f("ODbL"), "ODbL-1.0")
        self.assertIsNone(f("CC-BY-NC-4.0"))
        self.assertIsNone(f("CC BY-SA 4.0"))
        self.assertIsNone(f("Open Government Licence (non-commercial)"))
        self.assertIsNone(f("proprietary"))

    def test_status_mapping(self):
        f = dcingest.norm_status
        self.assertEqual(f("Planning permission granted"), "consented")
        self.assertEqual(f("Under Construction"), "under_construction")
        self.assertEqual(f("Refused"), "refused_or_withdrawn")
        self.assertEqual(f("Application submitted"), "in_planning")
        self.assertEqual(f(None), "unknown")


class MergeTests(unittest.TestCase):
    def test_same_name_close_points_merge(self):
        a = mk("DC-A1", "osm", "w1", "Alpha Data Centre", 51.5230, -0.6290, operator="Alphaco")
        b = mk("DC-A2", "wikidata", "Q1", "Alpha DC", 51.5250, -0.6290, operator="Alphaco Ltd")
        out = dcmerge.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["sources"]), 2)
        self.assertEqual(out[0]["match"][0]["rule"], "name_distance")

    def test_same_name_far_apart_do_not_merge(self):
        a = mk("DC-A1", "osm", "w1", "Alpha DC", 51.5230, -0.6290)
        b = mk("DC-A2", "wikidata", "Q1", "Alpha DC", 53.48, -2.24)
        self.assertEqual(len(dcmerge.merge([a, b])), 2)

    def test_threshold_depends_on_precision(self):
        # 3 km apart: no merge at exact_site, merge when one is settlement-level
        a = mk("DC-A1", "osm", "w1", "Gamma DC", 51.5230, -0.6290)
        b = mk("DC-A2", "wikidata", "Q1", "Gamma DC", 51.5500, -0.6290)
        self.assertEqual(len(dcmerge.merge([a, b])), 2)
        b2 = mk("DC-A2", "wikidata", "Q1", "Gamma DC", 51.5500, -0.6290, precision="settlement")
        self.assertEqual(len(dcmerge.merge([a, b2])), 1)

    def test_region_precision_never_distance_matches(self):
        a = mk("DC-A1", "osm", "w1", "Gamma Campus", 51.5230, -0.6290, operator="G")
        b = mk("DC-A3", "dsit", "1", "Gamma Campus Expansion", 51.5230, -0.6290, precision="region", operator="H")
        self.assertEqual(len(dcmerge.merge([a, b])), 2)

    def test_operator_rule(self):
        a = mk("DC-A1", "osm", "w1", "Slough Trading Estate", 51.5230, -0.6290, operator="Equinix")
        b = mk("DC-A4", "company", "e1", "Slough Estate Campus", 51.5235, -0.6292, operator="Equinix UK Ltd")
        out = dcmerge.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["match"][0]["rule"], "operator_distance")

    def test_designators_block_adjacent_buildings(self):
        a = mk("DC-A1", "osm", "w1", "Equinix LD4", 51.5230, -0.6290, operator="Equinix")
        b = mk("DC-A2", "wikidata", "Q5", "Equinix LD5", 51.5232, -0.6291, operator="Equinix")
        self.assertEqual(len(dcmerge.merge([a, b])), 2)

    def test_planning_ref_merges_despite_names(self):
        a = mk("DC-A3", "planning.data.gov.uk", "p1", "Land north of Foo Lane", 51.70, -0.40,
               precision="planning_site", plan=[("Three Rivers District Council", "23/0001/FUL")])
        b = mk("DC-A4", "company", "c1", "Project Kestrel", 51.70, -0.40, precision="settlement",
               plan=[("three rivers", "23/0001/ ful")])
        out = dcmerge.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["site_key"], "plan:three rivers:23/0001/FUL")
        self.assertIn("Project Kestrel", out[0]["aliases"] + [out[0]["name"]])

    def test_connection_ref_merge(self):
        a = mk("DC-A2", "NESO TEC register", "t1", "Foo Demand", conn=["TEC-123"])
        b = mk("DC-A4", "company", "c1", "Foo Campus", 52.0, -1.0, precision="settlement", conn=["tec-123"])
        out = dcmerge.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["match"][0]["rule"], "connection_ref")

    def test_no_location_exact_name_and_operator(self):
        a = mk("DC-A2", "NESO TEC register", "t1", "Zeta DC", operator="ZetaCo")
        b = mk("DC-A4", "company", "c1", "Zeta Data Centre", 52.0, -1.0, operator="ZetaCo Ltd")
        c = mk("DC-A5", "wikidata", "Q9", "Zeta DC", 53.0, -2.0, operator="Other")
        out = dcmerge.merge([a, b, c])
        self.assertEqual(sorted(len(m["sources"]) for m in out), [1, 2])

    def test_same_source_needs_strong_evidence(self):
        a = mk("DC-A1", "osm", "w1", "Kappa North", 51.5230, -0.6290, operator="K")
        b = mk("DC-A1", "osm", "w2", "Kappa South", 51.5233, -0.6290, operator="K")
        self.assertEqual(len(dcmerge.merge([a, b])), 2)

    def test_same_source_duplicate_flagged(self):
        a = mk("DC-A3", "planning", "p1", "X", 51.6, -0.5, plan=[("Slough", "P/1")])
        b = mk("DC-A3", "planning", "p2", "Y", 51.6, -0.5, plan=[("Slough", "P/1")])
        out = dcmerge.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertIn("same_source_duplicate", [c["kind"] for c in out[0]["conflicts"]])

    def test_no_transitive_chain_through_guard(self):
        # A~B (operator), B~C (operator), but A and C are distinct buildings LD4/LD5
        a = mk("DC-A1", "osm", "w1", "Equinix LD4", 51.5230, -0.6290, operator="Equinix")
        b = mk("DC-A4", "company", "c1", "Equinix Slough", 51.5231, -0.6290, operator="Equinix")
        c = mk("DC-A5", "wikidata", "Q1", "Equinix LD5", 51.5232, -0.6290, operator="Equinix")
        out = dcmerge.merge([a, b, c])
        for m in out:
            names = [m["name"]] + m["aliases"]
            self.assertFalse("Equinix LD4" in names and "Equinix LD5" in names)


class EvidenceUrlTests(unittest.TestCase):
    def test_shared_document_url_merges(self):
        doc = "https://assets.publishing.service.gov.uk/media/abc/Request_Statement.pdf"
        a = mk("DC-A2", "s35-seed", "1", "Data Centre Campus, Ampthill Road, Bedford", operator="QuestPit Limited")
        a["evidence_urls"] = [doc]
        b = mk("DC-A3", "MHCLG", "2", "QuestPit data centre campus", 52.09, -0.49, precision="settlement", it=720)
        b["evidence_urls"] = [doc, "https://www.gov.uk/government/collections/ai-growth-zones"]
        out = dcmerge.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["match"][0]["rule"], "evidence_document")
        self.assertEqual(dcschema.validate(out[0]), [])

    def test_same_lane_shared_press_release_does_not_merge(self):
        pr = "https://www.gov.uk/government/news/some-release"
        a = mk("DC-A3", "DSIT", "1", "CloudHQ Didcot", 51.61, -1.24, precision="settlement", operator="CloudHQ")
        b = mk("DC-A3", "DSIT", "2", "QTS Cambois", 55.15, -1.53, precision="settlement", operator="QTS")
        a["evidence_urls"] = b["evidence_urls"] = [pr]
        self.assertEqual(len(dcmerge.merge([a, b])), 2)

    def test_shared_url_far_apart_does_not_merge(self):
        pr = "https://www.gov.uk/government/news/some-release"
        a = mk("DC-A3", "DSIT", "1", "Nscale Loughton", 51.65, 0.07, precision="settlement")
        b = mk("DC-A4", "osm", "2", "Culham Campus", 51.66, -1.23)
        a["evidence_urls"] = b["evidence_urls"] = [pr]
        self.assertEqual(len(dcmerge.merge([a, b])), 2)

    def test_widely_cited_url_is_not_a_key(self):
        coll = "https://www.gov.uk/government/collections/ai-growth-zones"
        recs = []
        for i, nm in enumerate(["North", "South", "East"]):
            r = mk("DC-A3", "DSIT", str(i), nm + " Zone", 51 + i, -1.0, precision="region")
            r["evidence_urls"] = [coll]
            recs.append(r)
        self.assertEqual(len(dcmerge.merge(recs)), 3)


class ConflictTests(unittest.TestCase):
    def test_mw_disagreement_within_field_only(self):
        a = mk("DC-A2", "NESO TEC register", "t1", "Delta DC", 51.5, -1.0, grid=100, conn=["T1"])
        b = mk("DC-A4", "company", "c1", "Delta DC", 51.5, -1.0, grid=150, it=60, conn=["T1"])
        out = dcmerge.merge([a, b])[0]
        kinds = {(c["kind"], c["field"]) for c in out["conflicts"]}
        self.assertIn(("mw_disagreement", "grid_connection_mw"), kinds)
        self.assertNotIn(("mw_disagreement", "it_load_mw"), kinds)
        self.assertEqual(out["grid_connection_mw"]["value"], 100)  # tier 1 wins
        self.assertEqual(out["it_load_mw"]["value"], 60)  # only source; never derived from grid
        self.assertEqual(out["it_load_mw"]["source_id"], "DC-A4:company:c1")

    def test_it_and_grid_never_cross_compared(self):
        a = mk("DC-A2", "NESO TEC register", "t1", "Eps DC", 51.5, -1.0, grid=100, conn=["T2"])
        b = mk("DC-A4", "company", "c1", "Eps DC", 51.5, -1.0, it=40, conn=["T2"])
        out = dcmerge.merge([a, b])[0]
        self.assertEqual(out["conflicts"], [])
        self.assertEqual((out["it_load_mw"]["value"], out["grid_connection_mw"]["value"]), (40, 100))

    def test_small_mw_difference_is_not_conflict(self):
        a = mk("DC-A2", "NESO TEC register", "t1", "Eta", 51.5, -1.0, grid=100, conn=["T3"])
        b = mk("DC-A4", "company", "c1", "Eta", 51.5, -1.0, grid=103, conn=["T3"])
        self.assertEqual(dcmerge.merge([a, b])[0]["conflicts"], [])

    def test_unknown_mw_stays_null(self):
        a = mk("DC-A1", "osm", "w1", "Theta", 51.5, -1.0)
        b = mk("DC-A5", "wikidata", "Q1", "Theta", 51.5, -1.0)
        out = dcmerge.merge([a, b])[0]
        self.assertIsNone(out["it_load_mw"]["value"])
        self.assertIsNone(out["grid_connection_mw"]["value"])

    def test_status_disagreement(self):
        a = mk("DC-A3", "planning.data.gov.uk", "p1", "Iota", 51.5, -1.0, status="consented", plan=[("X", "1")])
        b = mk("DC-A1", "osm", "w1", "Iota", 51.5, -1.0, status="operational", plan=[("X", "1")])
        out = dcmerge.merge([a, b])[0]
        self.assertEqual(out["status"], "consented")  # planning (tier 2) over OSM (tier 6)
        c = [c for c in out["conflicts"] if c["kind"] == "status_disagreement"][0]
        self.assertEqual({v["value"] for v in c["values"]}, {"consented", "operational"})

    def test_unknown_status_is_not_conflict(self):
        a = mk("DC-A3", "planning", "p1", "Iota", 51.5, -1.0, status="unknown", plan=[("X", "1")])
        b = mk("DC-A1", "osm", "w1", "Iota", 51.5, -1.0, status="operational", plan=[("X", "1")])
        out = dcmerge.merge([a, b])[0]
        self.assertEqual(out["status"], "operational")
        self.assertEqual(out["conflicts"], [])

    def test_operator_and_date_disagreement(self):
        a = mk("DC-A3", "planning", "p1", "Kap", 51.5, -1.0, operator="A Corp", plan=[("X", "2")],
               dates={"consented": "2025-03-01"})
        b = mk("DC-A4", "company", "c1", "Kap", 51.5, -1.0, operator="B Corp", plan=[("X", "2")],
               dates={"consented": "2024"})
        kinds = {c["kind"] for c in dcmerge.merge([a, b])[0]["conflicts"]}
        self.assertEqual(kinds, {"operator_disagreement", "date_disagreement"})

    def test_compatible_dates_refine(self):
        a = mk("DC-A3", "planning", "p1", "Lam", 51.5, -1.0, plan=[("X", "3")], dates={"consented": "2025"})
        b = mk("DC-A4", "company", "c1", "Lam", 51.5, -1.0, plan=[("X", "3")], dates={"consented": "2025-06-30"})
        out = dcmerge.merge([a, b])[0]
        self.assertEqual(out["dates"]["consented"], "2025-06-30")
        self.assertEqual(out["conflicts"], [])

    def test_location_disagreement_on_ref_match(self):
        a = mk("DC-A3", "planning", "p1", "Mu", 51.50, -1.00, plan=[("X", "4")])
        b = mk("DC-A1", "osm", "w1", "Mu", 51.60, -1.00, plan=[("X", "4")])
        out = dcmerge.merge([a, b])[0]
        self.assertIn("location_disagreement", [c["kind"] for c in out["conflicts"]])

    def test_best_precision_location_wins(self):
        a = mk("DC-A3", "dsit", "1", "Nu Campus", 51.50, -1.00, precision="settlement")
        b = mk("DC-A1", "osm", "w1", "Nu Campus", 51.51, -1.00, precision="exact_site")
        out = dcmerge.merge([a, b])[0]
        self.assertEqual(out["location"]["precision"], "exact_site")
        self.assertEqual(out["location"]["lat"], 51.51)


class DeterminismTests(unittest.TestCase):
    def _corpus(self):
        rng = random.Random(73)
        recs = []
        for i in range(60):
            lat, lon = 51.0 + rng.random() * 2, -2.0 + rng.random() * 2
            nm = "Site %s" % "".join(rng.choice("abcdefgh") for _ in range(5))
            recs.append(mk("DC-A1", "osm", "w%d" % i, nm, lat, lon, operator=rng.choice(["A", "B", None])))
            if i % 3 == 0:
                recs.append(mk("DC-A5", "wikidata", "Q%d" % i, nm + " Data Centre", lat + 0.001, lon,
                               it=rng.choice([None, 10, 20]), status="operational"))
            if i % 5 == 0:
                recs.append(mk("DC-A3", "planning", "p%d" % i, "Land at " + nm, lat, lon, precision="planning_site",
                               plan=[("LPA%d" % i, "R/%d" % i)], status="in_planning", grid=rng.choice([None, 50])))
        return recs

    def test_order_independent_and_stable(self):
        recs = self._corpus()
        out1 = json.dumps(dcmerge.merge(recs), sort_keys=True)
        for seed in range(5):
            shuffled = recs[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(json.dumps(dcmerge.merge(shuffled), sort_keys=True), out1)

    def test_ids_unique_and_valid(self):
        out = dcmerge.merge(self._corpus())
        ids = [m["id"] for m in out]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))
        for m in out:
            self.assertEqual(dcschema.validate(m, cross_check=True), [], m["name"])
        self.assertEqual(sum(len(m["sources"]) for m in out), len(self._corpus()))

    def test_id_is_hash_of_site_key(self):
        a = mk("DC-A1", "osm", "w1", "Omicron", 51.5, -1.0)
        m = dcmerge.merge([a])[0]
        self.assertEqual(m["id"], dcschema.site_id("geo:omicron||51.500|-1.000"))

    def test_colliding_keys_disambiguated(self):
        a = mk("DC-A1", "osm", "w1", "Pi", 51.5, -1.0)
        b = mk("DC-A1", "osm", "w2", "Pi 2", 51.5, -1.0)
        c = mk("DC-A1", "osm", "w3", "Pi 3", 51.5, -1.0)
        ids = [m["id"] for m in dcmerge.merge([a, b, c])]
        self.assertEqual(len(set(ids)), 3)


class IngestTests(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            l1, l3 = os.path.join(d, "DC-A1"), os.path.join(d, "DC-A3")
            os.makedirs(l1)
            os.makedirs(l3)
            with open(os.path.join(l1, "osm.geojson"), "w") as f:
                json.dump({"type": "FeatureCollection", "metadata": {"licence": "ODbL-1.0",
                           "url": "https://www.openstreetmap.org/"}, "features": [
                    {"type": "Feature", "id": "way/1", "geometry": {"type": "Point", "coordinates": [-0.629, 51.523]},
                     "properties": {"name": "Alpha Data Centre", "operator": "AlphaCo", "capacity_mw": "12 MW"}},
                    {"type": "Feature", "id": "way/2", "geometry": {"type": "Point", "coordinates": [10.0, 51.0]},
                     "properties": {"name": "Not UK"}},
                    {"type": "Feature", "id": "way/3", "geometry": None, "properties": {"operator": "nameless"}},
                ]}, f)
            with open(os.path.join(l3, "plan.json"), "w") as f:
                json.dump({"records": [
                    {"name": "Alpha DC expansion", "lat": 51.5231, "lon": -0.6291, "precision": "planning_site",
                     "planning_ref": "P/123", "planning_authority": "Slough Borough Council", "status": "Approved",
                     "it_load_mw": 30, "grid_connection_mw": {"value": 45}, "licence": "OGL v3",
                     "evidence_url": "https://www.planning.data.gov.uk/entity/1", "operator": "AlphaCo Ltd"},
                    {"name": "Paywalled", "lat": 51.5, "lon": -1, "licence": "CC-BY-NC-4.0", "url": "https://x.org"},
                ]}, f)
            out = os.path.join(d, "out.geojson")
            fc, rejects = dcingest.build([l1, l3], out)
            s = fc["metadata"]["summary"]
            self.assertEqual(s["input_records"], 3)  # nameless-with-operator kept as "Unnamed data centre"
            self.assertEqual(s["rejected_records"], 2)  # outside UK; non-open licence
            self.assertEqual(s["sites"], 2)
            p = [f["properties"] for f in fc["features"] if f["properties"]["planning_refs"]][0]
            self.assertEqual(p["status"], "consented")
            self.assertEqual(p["it_load_mw"]["value"], 30)
            self.assertEqual(p["grid_connection_mw"]["value"], 45)
            self.assertEqual(p["unclassified_mw"]["value"], 12)  # bare "capacity_mw" never becomes IT or grid
            self.assertEqual(dcschema.validate_file(out), [])
            b1 = open(out, "rb").read()
            dcingest.build([l1, l3], out)
            self.assertEqual(open(out, "rb").read(), b1)  # byte-identical rebuild


class LaneShapeTests(unittest.TestCase):
    def _neso(self, dc_like, mw):
        return {"id": "neso-tec-1", "name": "Foo Data Centre", "operator": "FooCo", "status": "connection request",
                "it_load_mw": None, "grid_connection_mw": mw, "grid_connection_mw_basis": "TEC export",
                "grid_connection_mw_direction": "export", "dates": {"mw_effective_from": "2030-04-01"},
                "connection": {"site_name": "Barking 400kV", "project_id": "PRJ-9"},
                "location_precision": "connection_site_name", "geometry": None,
                "source": "NESO TEC register", "licence": "NESO Open Data Licence",
                "licence_url": "https://www.neso.energy/data-portal/neso-open-licence",
                "evidence_url": "https://www.neso.energy/data-portal/x", "dc_match": {"is_dc_like": dc_like}}

    def test_neso_row(self):
        rec, why = dcingest.normalise(self._neso(True, 120.0), None, {}, None, "DC-A1", "x.json", "0" * 64)
        self.assertIsNone(why)
        self.assertEqual(rec["grid_connection_mw"]["value"], 120.0)
        self.assertIn("direction: export", rec["grid_connection_mw"]["label"])
        self.assertIsNone(rec["it_load_mw"]["value"])
        self.assertEqual(rec["connection_refs"], ["PRJ-9"])
        self.assertEqual(rec["status"], "connection_request")
        self.assertEqual(rec["dates"]["connection_date"], "2030-04-01")
        self.assertEqual(rec["sources"][0]["licence"], "NESO-Open-Data-Licence")
        self.assertEqual(rec["sources"][0]["licence_status"], "unverified")
        self.assertEqual(rec["location"]["precision"], "unknown")

    def test_neso_non_dc_row_rejected(self):
        rec, why = dcingest.normalise(self._neso(False, 50.0), None, {}, None, "DC-A1", "x.json", "0" * 64)
        self.assertIsNone(rec)
        self.assertIn("not data-centre-like", why)

    def test_grouped_site_members(self):
        r = {"name": "Virtus London6", "status": "operational", "lat": 51.5, "lon": -0.4,
             "location_precision": "site = 3 source object(s); point: OSM node or footprint centre",
             "mw_unclassified": [{"property": "P2109", "raw": "12", "mw": 12.0, "source_id": "osm-1"}],
             "sources": [{"source": "OSM", "source_id": "osm-1", "name": "Virtus London5", "licence": "ODbL 1.0",
                          "evidence_url": "https://www.openstreetmap.org/way/1"},
                         {"source": "OSM", "source_id": "osm-2", "name": "Virtus London6", "licence": "ODbL 1.0",
                          "evidence_url": "https://www.openstreetmap.org/way/2"}]}
        rec, why = dcingest.normalise(r, None, {}, None, "DC-A4", "x.json", "0" * 64)
        self.assertIsNone(why)
        self.assertEqual(len(rec["sources"]), 2)
        self.assertEqual(rec["aliases"], ["Virtus London5"])
        self.assertEqual(rec["location"]["precision"], "exact_site")
        self.assertEqual(rec["unclassified_mw"]["value"], 12.0)
        self.assertIsNone(rec["it_load_mw"]["value"])


if __name__ == "__main__":
    unittest.main()
