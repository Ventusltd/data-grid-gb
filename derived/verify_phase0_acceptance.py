"""Independent Phase-0 acceptance oracle for the ETYS network products.

This oracle keeps four different populations separate:

* 1,472 Appendix B transformer rows (global physical-record population);
* transformer-to-site incidences after one row is counted once per site;
* node-end/winding landings, which legitimately count both ends; and
* cable *records* versus independent endpoint pairs.

It intentionally does not reproduce the road-routing experiment: the graph
binary and its source-build provenance are not in this repository.  An
optional externally generated route-result file can be re-scored, but that is
labelled evidence replay rather than an independent graph reconstruction.

Examples:

    python derived/verify_phase0_acceptance.py
    python derived/verify_phase0_acceptance.py --output evidence.json

The historical comparison defaults to the immutable pre-remediation commit
recorded below.  Do not substitute ``HEAD``: after this work is committed,
``HEAD`` is the corrected product rather than the 502/886 baseline.
"""

import argparse
import hashlib
import io
import json
import math
import os
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict

from build_connection_points import normalise, site_join_context


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NETWORK_PATH = os.path.join(REPO, "derived", "gb-transmission-network.v1.json")
POINTS_PATH = os.path.join(REPO, "derived", "connection-points.v3.json")
R_KM = 6371.0088
FIXED_CORRIDOR_FACTOR = 1.245
HISTORICAL_BASELINE_REF = "1c9909d1138704b29235c27fd769436dda8a0b18"

LEGACY_NOISE = re.compile(
    r"\b(SUBSTATION|SUB STATION|SUBSTN|GRID|SUPPLY|POINT|GSP|NATIONAL|"
    r"POWER|STATION|WIND|FARM|WINDFARM|OFFSHORE|ONSHORE|EXTENSION|"
    r"400KV|275KV|132KV|66KV|33KV|11KV|NGET|SSE|SP|SHE)\b")


def load_json(path):
    return json.load(io.open(path, encoding="utf-8"))


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def legacy_normalise(name):
    text = str(name or "").upper()
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    return " ".join(LEGACY_NOISE.sub(" ", text).split())


def collision_summary(rows, normaliser):
    groups = defaultdict(list)
    for row in rows:
        groups[normaliser(row["name"])].append(row)
    collisions = {key: values for key, values in groups.items() if len(values) > 1}
    exact_name_groups = {
        key: values for key, values in collisions.items()
        if len({value["name"] for value in values}) == 1
    }
    destructive = {
        key: values for key, values in collisions.items()
        if len({value["name"] for value in values}) > 1
    }
    return {
        "rows": len(rows),
        "distinct_keys": len(groups),
        "collision_groups": len(collisions),
        "rows_in_collision_groups": sum(len(values) for values in collisions.values()),
        "first_win_rows_lost": sum(len(values) - 1 for values in collisions.values()),
        "exact_source_name_duplicate_groups": len(exact_name_groups),
        "exact_source_name_duplicate_rows": sum(
            len(values) for values in exact_name_groups.values()),
        "destructive_normalisation_groups": len(destructive),
        "distinct_source_names_in_destructive_groups": sum(
            len({value["name"] for value in values}) for values in destructive.values()),
        "rows_in_destructive_groups": sum(len(values) for values in destructive.values()),
        "groups": [
            {
                "key": key,
                "sites": [
                    {
                        "site_code": value["code"] if "code" in value else value["site_code"],
                        "name": value["name"],
                        "highest_voltage_kv": max(value["voltages_kv"]),
                        "transmission_owner": value["transmission_owner"],
                    }
                    for value in values
                ],
            }
            for key, values in sorted(collisions.items())
        ],
    }


def haversine(location_a, location_b):
    lat1, lon1 = location_a
    lat2, lon2 = location_b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2)
    return 2.0 * R_KM * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def scalar_metrics(rows, factor=FIXED_CORRIDOR_FACTOR):
    errors = [abs(factor * row["straight_km"] - row["cable_km"])
              / row["cable_km"] * 100.0 for row in rows]
    within = sum(error <= 15.0 for error in errors)
    best = None
    for step in range(2501):
        candidate = 0.5 + step * 0.001
        candidate_errors = [
            abs(candidate * row["straight_km"] - row["cable_km"])
            / row["cable_km"] * 100.0 for row in rows
        ]
        score = statistics.median(candidate_errors)
        if best is None or score < best[1]:
            best = (candidate, score)
    return {
        "n": len(rows),
        "factor": factor,
        "median_absolute_percentage_error": statistics.median(errors),
        "within_15_percent_n": within,
        "within_15_percent_fraction": within / len(rows),
        "best_factor_grid_0.001": best[0],
        "best_factor_in_sample_median_absolute_percentage_error": best[1],
        "evaluation_warning": (
            "Descriptive in-sample calibration only; this is not a held-out "
            "accuracy estimate and does not establish route geometry."),
    }


def cable_evidence(network, points):
    node_site = {node["node"]: node["site_code"] for node in network["nodes"]}
    locations = {
        point["site_code"]: (float(point["location"]["lat"]),
                             float(point["location"]["lon"]))
        for point in points["connection_points"] if point.get("location")
    }
    rows = []
    skipped = Counter()
    for index, circuit in enumerate(network["circuits"]):
        if circuit.get("circuit_type") != "Cable":
            continue
        site_1 = node_site.get(circuit.get("node_1"))
        site_2 = node_site.get(circuit.get("node_2"))
        if not site_1 or not site_2:
            skipped["node_without_site"] += 1
            continue
        if site_1 == site_2:
            skipped["same_authoritative_site"] += 1
            continue
        if site_1 not in locations or site_2 not in locations:
            skipped["endpoint_without_mapped_joined_coordinates"] += 1
            continue
        cable_km = float(circuit.get("cable_km") or 0.0)
        ohl_km = float(circuit.get("ohl_km") or 0.0)
        if cable_km <= 0.0:
            skipped["no_positive_cable_km"] += 1
            continue
        straight = haversine(locations[site_1], locations[site_2])
        rows.append({
            "circuit_index": index,
            "node_1": circuit["node_1"],
            "node_2": circuit["node_2"],
            "site_1": site_1,
            "site_2": site_2,
            "pair": "|".join(sorted((site_1, site_2))),
            "cable_km": cable_km,
            "ohl_km": ohl_km,
            "straight_km": straight,
        })

    study_rows = [row for row in rows if row["straight_km"] > 1.0]

    def summarise(sample):
        pairs = defaultdict(list)
        for row in sample:
            pairs[row["pair"]].append(row)
        circuit_membership = [
            {
                "circuit_index": row["circuit_index"],
                "pair": row["pair"],
                "node_1": row["node_1"],
                "node_2": row["node_2"],
            }
            for row in sorted(sample, key=lambda item: item["circuit_index"])
        ]
        pair_membership = sorted(pairs)
        circuit_membership_bytes = json.dumps(
            circuit_membership, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        pair_membership_bytes = json.dumps(
            pair_membership, separators=(",", ":")
        ).encode("utf-8")
        pair_rows = []
        for pair, members in sorted(pairs.items()):
            pair_rows.append({
                "pair": pair,
                "straight_km": members[0]["straight_km"],
                "cable_km": statistics.mean(member["cable_km"] for member in members),
                "parallel_circuit_records": len(members),
            })
        contradictions = [
            row for row in sample if row["straight_km"] > row["cable_km"] + 1e-9
        ]
        return {
            "circuit_records": len(sample),
            "distinct_site_pairs": len(pairs),
            "parallel_record_excess": len(sample) - len(pairs),
            "membership": {
                "circuit_records": circuit_membership,
                "circuit_records_sha256": hashlib.sha256(
                    circuit_membership_bytes).hexdigest(),
                "site_pairs": pair_membership,
                "site_pairs_sha256": hashlib.sha256(
                    pair_membership_bytes).hexdigest(),
                "split_rule": (
                    "Freeze and split by site pair; parallel circuit records for one "
                    "pair must never cross train/holdout boundaries."),
            },
            "straight_exceeds_published_cable_km_records": len(contradictions),
            "straight_exceeds_warning": (
                "A contradiction is a location-join/coordinate/length-semantics QA "
                "signal; it does not by itself identify the cause."),
            "fixed_factor_circuit_weighted": scalar_metrics(sample),
            "fixed_factor_pair_weighted_mean_published_length": scalar_metrics(pair_rows),
        }

    return {
        "selection": (
            "circuit_type == 'Cable'; distinct authoritative endpoint sites; "
            "both endpoints have mapped/joined coordinates; cable_km > 0"),
        "all_coordinate_known_intersite_cable_records": summarise(rows),
        "coordinate_known_intersite_records_at_or_below_1km": sum(
            row["straight_km"] <= 1.0 for row in rows),
        "legacy_study_filter_straight_distance_gt_1km": summarise(study_rows),
        "skipped_before_coordinate_known_intersite_sample": dict(skipped),
        "earth_radius_km": R_KM,
        "independence_warning": (
            "Circuit rows sharing a site pair are not independent route geometries; "
            "report record-weighted and pair-weighted results separately. Dynamic "
            "count-only cohorts are not predictive gates; freeze membership before "
            "a pair-grouped train/holdout split."),
    }


def coverage_evidence(points):
    rows = points["connection_points"]
    output = {
        "connection_points": len(rows),
        "with_mapped_joined_coordinates": sum(bool(row.get("location")) for row in rows),
    }
    output["without_mapped_joined_coordinates"] = (
        output["connection_points"] - output["with_mapped_joined_coordinates"])
    output["coverage_fraction"] = (
        output["with_mapped_joined_coordinates"] / output["connection_points"])
    by_voltage = {}
    for voltage in (400, 275, 220, 132, 66, 33, 11):
        population = [row for row in rows if voltage in row["voltages_kv"]]
        located = sum(bool(row.get("location")) for row in population)
        by_voltage[str(voltage)] = {
            "connection_points": len(population),
            "with_mapped_joined_coordinates": located,
            "without_mapped_joined_coordinates": len(population) - located,
            "coverage_fraction": located / len(population) if population else None,
        }
    output["by_voltage_kv"] = by_voltage
    output["wording_boundary"] = (
        "NESO ETYS does not publish these coordinates; they are mapped geometry "
        "joined to ETYS identities and must not be called published coordinates.")
    return output


def transformer_evidence(network, points):
    nodes = {node["node"]: node for node in network["nodes"]}
    all_incidences = defaultdict(set)
    endpoint_landings = Counter()
    same_site_rows = 0
    for index, transformer in enumerate(network["transformers"]):
        endpoint_sites = []
        for end in ("node_1", "node_2"):
            site_code = nodes[transformer[end]]["site_code"]
            endpoint_sites.append(site_code)
            endpoint_landings[site_code] += 1
        if len(set(endpoint_sites)) == 1:
            same_site_rows += 1
        for site_code in set(endpoint_sites):
            all_incidences[site_code].add(index)

    rollup_codes = {point["site_code"] for point in points["connection_points"]}
    point_by_code = {point["site_code"]: point for point in points["connection_points"]}
    rollup_incidences = {
        code: indices for code, indices in all_incidences.items() if code in rollup_codes
    }
    cowley_indices = all_incidences["COWL"]
    cowley_windings = Counter()
    cowley_signatures = Counter()
    for index in sorted(cowley_indices):
        transformer = network["transformers"][index]
        cowley_signatures[json.dumps(transformer, sort_keys=True,
                                     separators=(",", ":"))] += 1
        for end in ("node_1", "node_2"):
            node = nodes[transformer[end]]
            if node["site_code"] == "COWL":
                cowley_windings[str(node["voltage_kv"])] += 1

    mismatches = [
        {
            "site_code": code,
            "product": point_by_code[code]["transformers"],
            "oracle": len(indices),
        }
        for code, indices in rollup_incidences.items()
        if point_by_code[code]["transformers"] != len(indices)
    ]
    return {
        "global_transformer_records": len(network["transformers"]),
        "same_site_transformer_records": same_site_rows,
        "same_site_transformer_record_fraction": same_site_rows / len(network["transformers"]),
        "global_node_end_landings": sum(endpoint_landings.values()),
        "global_transformer_site_incidences": sum(
            len(indices) for indices in all_incidences.values()),
        "global_sites_with_transformers": len(all_incidences),
        "global_sites_inflated_by_endpoint_count": sum(
            endpoint_landings[code] > len(indices)
            for code, indices in all_incidences.items()),
        "rollup_node_end_landings": sum(
            endpoint_landings[code] for code in rollup_incidences),
        "rollup_transformer_site_incidences": sum(
            len(indices) for indices in rollup_incidences.values()),
        "rollup_sites_with_transformers": len(rollup_incidences),
        "rollup_sites_inflated_by_endpoint_count": sum(
            endpoint_landings[code] > len(indices)
            for code, indices in rollup_incidences.items()),
        "product_count_mismatches": mismatches,
        "cowley": {
            "product_physical_record_count": point_by_code["COWL"]["transformers"],
            "oracle_physical_record_count": len(cowley_indices),
            "node_end_windings_by_voltage_kv": dict(sorted(cowley_windings.items())),
            "largest_byte_identical_parallel_group": max(cowley_signatures.values()),
            "identity_rule": (
                "Use source-row ordinal while indexing. Never deduplicate by row "
                "content: distinct parallel units can be byte-identical."),
        },
        "terminology": {
            "1472": "global physical/source transformer records",
            "1550": "global transformer-to-site incidences",
            "1526": "transformer-to-site incidences in the >=132 kV rollup only",
            "2944": "global node-end/winding landings",
            "2920": "node-end/winding landings in the >=132 kV rollup",
        },
    }


def points_from_git(ref):
    raw = subprocess.check_output(
        ["git", "show", f"{ref}:derived/connection-points.v3.json"],
        cwd=REPO)
    return json.loads(raw.decode("utf-8"))


def location_delta(current, comparison):
    current_by_code = {point["site_code"]: point for point in current["connection_points"]}
    comparison_by_code = {
        point["site_code"]: point for point in comparison["connection_points"]
    }
    lost, gained, changed = [], [], []
    for code in sorted(current_by_code):
        now = current_by_code[code].get("location")
        before = comparison_by_code[code].get("location")
        if before and not now:
            lost.append({"site_code": code, "name": current_by_code[code]["name"],
                         "before": before})
        elif now and not before:
            gained.append({"site_code": code, "name": current_by_code[code]["name"],
                           "after": now})
        elif now and before and now != before:
            changed.append({"site_code": code, "name": current_by_code[code]["name"],
                            "before": before, "after": now})
    return {
        "lost": lost,
        "gained": gained,
        "changed": changed,
        "counts": {"lost": len(lost), "gained": len(gained),
                   "changed": len(changed),
                   "net_location_change": len(gained) - len(lost)},
        "interpretation": (
            "A lower count is not automatically a regression: fail-closed removal of "
            "an unsupported join is an epistemic correction. Review every code."),
    }


def replay_road_results(path):
    rows = load_json(path)
    scored = [row for row in rows
              if row.get("status") == "ok" and row.get("routed_total_km") is not None]
    routed_errors = [
        abs(row["routed_total_km"] - row["cable_km"]) / row["cable_km"] * 100.0
        for row in scored
    ]
    wins = sum(
        abs(row["routed_total_km"] - row["cable_km"])
        < abs(row["straight_km"] - row["cable_km"])
        for row in scored
    )
    return {
        "status": "external_result_replay_not_independent_graph_reconstruction",
        "path": os.path.abspath(path),
        "sha256": sha256(path),
        "records": len(rows),
        "scored_records": len(scored),
        "status_counts": dict(Counter(row.get("status") for row in rows)),
        "median_absolute_percentage_error_scored": statistics.median(routed_errors),
        "beats_straight_scored_n": wins,
        "beats_straight_scored_fraction": wins / len(scored),
        "beats_straight_all_records_fraction": wins / len(rows),
        "limitation": (
            "The route geometries cannot be regenerated from data-grid-gb alone; "
            "the graph binary, build inputs and immutable provenance bundle are absent."),
    }


def build_evidence(args):
    network = load_json(NETWORK_PATH)
    points = load_json(POINTS_PATH)
    eligible_sites = [
        site for site in network["sites"]
        if site["voltages_kv"] and max(site["voltages_kv"]) >= points["minimum_kv"]
    ]
    contexts = defaultdict(list)
    for site in eligible_sites:
        contexts[site_join_context(site)].append(site)
    contextual_collisions = [values for values in contexts.values() if len(values) > 1]

    evidence = {
        "schema": "data-grid-gb.phase0-acceptance-evidence.v1",
        "inputs": {
            "network": {"path": os.path.relpath(NETWORK_PATH, REPO),
                        "sha256": sha256(NETWORK_PATH)},
            "connection_points": {"path": os.path.relpath(POINTS_PATH, REPO),
                                  "sha256": sha256(POINTS_PATH)},
        },
        "transformers": transformer_evidence(network, points),
        "name_join": {
            "legacy_normaliser_all_network_sites": collision_summary(
                network["sites"], legacy_normalise),
            "legacy_normaliser_connection_point_population": collision_summary(
                eligible_sites, legacy_normalise),
            "current_normaliser_all_network_sites": collision_summary(
                network["sites"], normalise),
            "current_normaliser_connection_point_population": collision_summary(
                eligible_sites, normalise),
            "contextual_name_highest_voltage_owner_collision_groups": len(
                contextual_collisions),
            "contextual_name_highest_voltage_owner_collision_rows": sum(
                len(values) for values in contextual_collisions),
            "contextual_collision_sites": [
                [site["code"] for site in values] for values in contextual_collisions
            ],
            "rule": (
                "Preserve ONSHORE/OFFSHORE/EXTENSION. Use name + voltage + owner "
                "only to narrow candidates; use site_code or fail closed when that "
                "context is not unique. Never force an OSM operator tag to equal a "
                "transmission owner."),
        },
        "coordinate_coverage": coverage_evidence(points),
        "cable_scalar": cable_evidence(network, points),
        "road_router": {
            "status": "not_reconstructed",
            "reason": (
                "No immutable road-graph bundle and build provenance are present in "
                "this repository; road-router accuracy claims are not acceptance facts."),
        },
    }
    if args.comparison_ref:
        comparison = points_from_git(args.comparison_ref)
        evidence["comparison"] = {
            "git_ref": args.comparison_ref,
            "coordinate_coverage": coverage_evidence(comparison),
            "cable_scalar": cable_evidence(network, comparison),
            "boundary": (
                "Comparison describes the named historical product, including its "
                "known location-join defects; it is not the corrected release oracle."),
            "coordinate_join_delta_to_current": location_delta(points, comparison),
        }
    if args.road_routes:
        evidence["road_router"] = replay_road_results(args.road_routes)

    t = evidence["transformers"]
    c_old_all = evidence["name_join"]["legacy_normaliser_all_network_sites"]
    c_old_rollup = evidence["name_join"]["legacy_normaliser_connection_point_population"]
    c_new_all = evidence["name_join"]["current_normaliser_all_network_sites"]
    c_new_rollup = evidence["name_join"]["current_normaliser_connection_point_population"]
    checks = {
        "1472_global_transformer_records": t["global_transformer_records"] == 1472,
        "1394_same_site_transformer_records": t["same_site_transformer_records"] == 1394,
        "1550_global_transformer_site_incidences":
            t["global_transformer_site_incidences"] == 1550,
        "1526_rollup_transformer_site_incidences":
            t["rollup_transformer_site_incidences"] == 1526,
        "cowley_5_physical_records": t["cowley"]["oracle_physical_record_count"] == 5
            and t["cowley"]["product_physical_record_count"] == 5,
        "cowley_5_windings_at_each_voltage":
            t["cowley"]["node_end_windings_by_voltage_kv"] == {"132": 5, "400": 5},
        "byte_identical_parallel_transformers_remain_distinct":
            t["cowley"]["largest_byte_identical_parallel_group"] > 1,
        "all_product_transformer_counts_match_row_identity_oracle":
            not t["product_count_mismatches"],
        "identity_qualifiers_are_preserved":
            normalise("Moray East Onshore") != normalise("Moray East Offshore")
            and normalise("Arecleoch") != normalise("Arecleoch Extension"),
        "legacy_collision_denominators_reproduced":
            c_old_all["collision_groups"] == 34
            and c_old_all["rows_in_collision_groups"] == 69
            and c_old_rollup["collision_groups"] == 32
            and c_old_rollup["rows_in_collision_groups"] == 65
            and c_old_rollup["destructive_normalisation_groups"] == 30
            and c_old_rollup["distinct_source_names_in_destructive_groups"] == 60
            and c_old_rollup["rows_in_destructive_groups"] == 61,
        "preserved_qualifiers_reduce_collisions":
            c_new_all["collision_groups"] == 6
            and c_new_all["rows_in_collision_groups"] == 12
            and c_new_rollup["collision_groups"] == 5
            and c_new_rollup["rows_in_collision_groups"] == 10,
        "remaining_context_ambiguity_fails_closed":
            evidence["name_join"][
                "contextual_name_highest_voltage_owner_collision_groups"] == 1
            and all(point["join_context_key"] is None
                    for point in points["connection_points"]
                    if point["site_code"] in {"EOWF", "EOWL"}),
        "connection_point_population_is_886":
            evidence["coordinate_coverage"]["connection_points"] == 886,
        "coverage_metadata_matches_rows":
            points["counts"]["with_location"]
            == evidence["coordinate_coverage"]["with_mapped_joined_coordinates"],
        "cable_records_are_not_reported_as_independent_pairs":
            evidence["cable_scalar"]["legacy_study_filter_straight_distance_gt_1km"]
            ["circuit_records"]
            >= evidence["cable_scalar"]["legacy_study_filter_straight_distance_gt_1km"]
            ["distinct_site_pairs"],
        "corrected_cable_cohort_is_95_records_over_60_pairs":
            evidence["cable_scalar"]["legacy_study_filter_straight_distance_gt_1km"]
            ["circuit_records"] == 95
            and evidence["cable_scalar"]
            ["legacy_study_filter_straight_distance_gt_1km"]
            ["distinct_site_pairs"] == 60,
        "corrected_fixed_scalar_metrics_are_reproduced":
            math.isclose(
                evidence["cable_scalar"]
                ["legacy_study_filter_straight_distance_gt_1km"]
                ["fixed_factor_circuit_weighted"]
                ["median_absolute_percentage_error"],
                8.58224,
                rel_tol=0.0,
                abs_tol=1e-4,
            )
            and evidence["cable_scalar"]
            ["legacy_study_filter_straight_distance_gt_1km"]
            ["fixed_factor_circuit_weighted"]["within_15_percent_n"] == 68
            and math.isclose(
                evidence["cable_scalar"]
                ["legacy_study_filter_straight_distance_gt_1km"]
                ["fixed_factor_pair_weighted_mean_published_length"]
                ["median_absolute_percentage_error"],
                9.38712,
                rel_tol=0.0,
                abs_tol=1e-4,
            )
            and evidence["cable_scalar"]
            ["legacy_study_filter_straight_distance_gt_1km"]
            ["fixed_factor_pair_weighted_mean_published_length"]
            ["within_15_percent_n"] == 40,
        "corrected_cable_cohort_membership_is_frozen":
            evidence["cable_scalar"]
            ["legacy_study_filter_straight_distance_gt_1km"]
            ["membership"]["circuit_records_sha256"]
            == "216403a88f9a36a88ed200905a04c84a9c11e7afe7efea6e27b677e5b1bdcf0e"
            and evidence["cable_scalar"]
            ["legacy_study_filter_straight_distance_gt_1km"]
            ["membership"]["site_pairs_sha256"]
            == "ce4f1e56b71c097c4275143633c764b6b5754bf661d1f9d5a3f4012c74d8f736",
    }
    if args.comparison_ref:
        comparison = evidence["comparison"]
        checks["historical_502_of_886_coverage_is_reproduced_not_reused"] = (
            comparison["coordinate_coverage"]["connection_points"] == 886
            and comparison["coordinate_coverage"]["with_mapped_joined_coordinates"] == 502)
        historical = comparison["cable_scalar"][
            "legacy_study_filter_straight_distance_gt_1km"]
        checks["historical_95_records_are_only_59_site_pairs"] = (
            historical["circuit_records"] == 95
            and historical["distinct_site_pairs"] == 59)
        checks["historical_cable_cohort_membership_is_frozen"] = (
            historical["membership"]["circuit_records_sha256"]
            == "75f5130906b25024c2d6b7c797ca06f5b7bcea26539d69ef1edb46dbaa14c53e"
            and historical["membership"]["site_pairs_sha256"]
            == "dfa6ac6086863c489d66238bdf1fec5f807fc9d82994b3d3913b1cf36933512a")
        delta = comparison["coordinate_join_delta_to_current"]["counts"]
        checks["location_delta_is_fully_enumerated"] = (
            delta == {"lost": 16, "gained": 3, "changed": 0,
                      "net_location_change": -13})
    evidence["checks"] = checks
    evidence["summary"] = {
        "passed": sum(checks.values()),
        "total": len(checks),
        "failures": [name for name, passed in checks.items() if not passed],
    }
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--comparison-ref",
        default=HISTORICAL_BASELINE_REF,
        help=("Git ref containing the historical connection-points.v3.json "
              f"(default: {HISTORICAL_BASELINE_REF})"))
    parser.add_argument(
        "--road-routes",
        help="Optional external route-result JSON to re-score (not reconstruct)")
    parser.add_argument("--output", help="Optional path for canonical JSON evidence")
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Print only the check summary; --output still receives full evidence")
    args = parser.parse_args()
    evidence = build_evidence(args)
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        io.open(args.output, "w", encoding="utf-8", newline="\n").write(rendered)
    if args.summary_only:
        sys.stdout.write(json.dumps(evidence["summary"], indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(rendered)
    return 1 if evidence["summary"]["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
