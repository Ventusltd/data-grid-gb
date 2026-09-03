"""Verify both products before anything is allowed to consume them.

The rule this repository inherits from the price repository: a consumer
reads a product that already sits clean. So the checks here fail closed,
and they check the things that would quietly mislead a reader rather than
the things that would obviously break a parser.

    python derived/verify_connection_points.py
"""

import io
import json
import os
import sys
from collections import Counter, defaultdict

from build_connection_points import normalise, site_join_context

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

failures = []
passed = 0


def check(label, condition):
    global passed
    if condition:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failures.append(label)
        print(f"  [FAIL] {label}")


def main():
    network = json.load(io.open(os.path.join(REPO, "derived", "gb-transmission-network.v1.json"),
                                encoding="utf-8"))
    points = json.load(io.open(os.path.join(REPO, "derived", "connection-points.v3.json"),
                               encoding="utf-8"))

    print("\nthe network model\n")
    check("schema is named and versioned",
          network.get("schema") == "data-grid-gb.transmission-network.v1")
    check("the publisher and publication are stated",
          network["source"]["publisher"] == "NESO"
          and "Ten Year Statement" in network["source"]["publication"])
    # The substance, not one product's sentence: both must say that queue
    # position and commercial terms decide connection and that no appendix
    # carries them. Pinning the exact wording made this check about prose.
    check("it refuses to be read as a connection assessment",
          "no published appendix contains" in network["not_a_connection_assessment"]
          and "queue position" in network["not_a_connection_assessment"].lower())
    counts = network["counts"]
    check("every transmission owner's sites are present",
          {s["transmission_owner"] for s in network["sites"]} == {"SHET", "SPT", "NGET", "OFTO"})
    check("circuits carry impedance on the declared base",
          all("r_pct_100mva" in c and "x_pct_100mva" in c for c in network["circuits"][:200]))
    check("circuits carry at least a winter rating",
          sum(1 for c in network["circuits"] if c.get("winter_mva")) > len(network["circuits"]) * 0.8)
    check("planned changes carry a year and a status",
          all(c.get("year") and c.get("status") for c in network["planned_changes"][:200]))
    check("the voltage-digit convention is published as derived, with its counts",
          network["node_code_convention"]["derived_not_documented"] is True
          and network["node_code_convention"]["observed_digit_to_site_voltage_counts"])
    check("nodes whose voltage the site does not declare are counted, not hidden",
          isinstance(network["node_code_convention"]
                     ["nodes_whose_voltage_is_not_declared_by_their_site"], int))
    check("the model is not trivially small",
          counts["circuits"] > 1000 and counts["nodes"] > 2000
          and counts["planned_changes"] > 1000)
    check("the ETYS 2025 model retains 1,472 distinct transformer records",
          counts["transformers"] == 1472
          and len(network["transformers"]) == 1472)

    print("\nthe connection points\n")
    check("schema is named and versioned",
          points.get("schema") == "data-grid-gb.connection-points.v3")
    check("it refuses to be read as a connection assessment",
          "no published appendix contains" in points["not_a_connection_assessment"]
          and "queue position" in points["not_a_connection_assessment"].lower())
    check("nothing below the declared minimum voltage is published",
          all(max(p["voltages_kv"]) >= points["minimum_kv"] for p in points["connection_points"]))
    join = points["join"]
    check("the join is reported by tier, not as a single number",
          all(k in join for k in ("exact_name", "distinctive_tokens",
                                  "ambiguous_exact_name",
                                  "ambiguous_distinctive_tokens",
                                  "ambiguous_authoritative_identity",
                                  "rejected_shore_qualifier_conflict", "unlocated")))
    check("the join total equals the number of points",
          join["exact_name"] + join["distinctive_tokens"] + join["unlocated"]
          == len(points["connection_points"]))
    check("ambiguous exact names fail closed inside the unlocated tier",
          0 < join["ambiguous_exact_name"] <= join["unlocated"])
    check("ambiguous token matches fail closed inside the unlocated tier",
          0 < join["ambiguous_distinctive_tokens"] <= join["unlocated"])
    check("an ambiguous authoritative name/voltage/owner identity fails closed",
          0 < join["ambiguous_authoritative_identity"] <= join["unlocated"])
    check("a mapped onshore/offshore qualifier conflict fails closed",
          0 < join["rejected_shore_qualifier_conflict"] <= join["unlocated"])
    check("unlocated sites are published rather than dropped",
          join["unlocated"] > 0
          and sum(1 for p in points["connection_points"] if "location" not in p)
          == join["unlocated"])
    check("every located point carries plausible GB coordinates",
          all(49 < p["location"]["lat"] < 61 and -9 < p["location"]["lon"] < 3
              for p in points["connection_points"] if "location" in p))
    check("a located point says how it was matched",
          all(p["location"]["matched_by"] in ("exact_name_highest_voltage",
                                                "exact_name_voltage_compatible",
                                                "distinctive_tokens_highest_voltage")
              for p in points["connection_points"] if "location" in p))
    check("onshore, offshore and extension remain identity-bearing",
          normalise("Moray East Onshore") == "MORAY EAST ONSHORE"
          and normalise("Moray East Offshore") == "MORAY EAST OFFSHORE"
          and normalise("Arecleoch Extension") == "ARECLEOCH EXTENSION")
    contexts = defaultdict(list)
    for site in network["sites"]:
        if site["voltages_kv"] and max(site["voltages_kv"]) >= points["minimum_kv"]:
            contexts[site_join_context(site)].append(site["code"])
    point_by_code = {p["site_code"]: p for p in points["connection_points"]}
    check("context keys combine name, voltage and owner and fail closed on duplicates",
          all((point_by_code[code]["join_context_key"] is not None) == (len(codes) == 1)
              for context, codes in contexts.items() for code in codes)
          and all("|" in p["join_context_key"]
                  for p in points["connection_points"] if p["join_context_key"] is not None))
    check("Thanet onshore does not lend its coordinate to Thanet offshore",
          "location" in point_by_code["THAW"]
          and "ONSHORE" in point_by_code["THAW"]["location"]["mapped_name"].upper()
          and "location" not in point_by_code["THOW"])
    check("Moray East offshore does not inherit Moray East onshore geometry",
          "location" in point_by_code["MORO"]
          and "ONSHORE" in point_by_code["MORO"]["location"]["mapped_name"].upper()
          and "location" not in point_by_code["MOWE"])
    expected_metrics = {
        "three_phase_initial_peak_current_ka", "three_phase_rms_break_current_ka",
        "three_phase_dc_break_current_ka", "three_phase_peak_break_current_ka",
        "single_phase_initial_peak_current_ka", "single_phase_rms_break_current_ka",
        "single_phase_dc_break_current_ka", "single_phase_peak_break_current_ka"}
    check("the root feed retains all eight exact Appendix D metrics",
          set(network.get("fault_current_metrics", [])) == expected_metrics)
    check("fault-current summaries name every metric and unit separately",
          all(set(entry["metrics"]) == expected_metrics
              and all(metric["unit"] == "kA" and metric["min"] <= metric["max"]
                      for metric in entry["metrics"].values())
              for point in points["connection_points"] if point["fault_current"]
              for entry in point["fault_current"].values()))
    check("fault-current summaries retain scenarios, winters and locations",
          all(entry["scenarios"] > 0 and entry["winters"] and entry["locations"]
              for point in points["connection_points"] if point["fault_current"]
              for entry in point["fault_current"].values()))
    check("every fault-current site envelope states when it combines voltages",
          all("site-wide envelope" in entry["scope"] and entry["voltages_kv"]
              for point in points["connection_points"] if point["fault_current"]
              for entry in point["fault_current"].values()))
    check("fault-current summaries are also separated by published voltage",
          all(point["fault_current_by_voltage"]
              and all(len(entry["voltages_kv"]) == 1
                      and str(int(entry["voltages_kv"][0])) == voltage
                      for voltage, cases in point["fault_current_by_voltage"].items()
                      for entry in cases.values())
              for point in points["connection_points"] if point["fault_current"]))
    cottam = [row for row in network["fault_current_scenarios"]
              if row["demand_case"] == "peak" and row["winter"] == "2025/26"
              and row["location"] == "COTT4 M1"]
    check("Cottam proves initial-peak is not renamed break current",
          len(cottam) == 1
          and abs(cottam[0]["three_phase_initial_peak_current_ka"]
                  - 109.219270174868) < 1e-9
          and "three_phase_rms_break_current_ka" in cottam[0]
          and "three_phase_break_ka" not in cottam[0])
    check("planned change years are consistent with their count",
          all((p["planned_changes"] > 0) == bool(p["planned_change_years"])
              for p in points["connection_points"]))

    # One transformer source row is one physical record.  Its two node ends
    # remain available as winding/landing evidence, but a same-site row must
    # contribute only once to that site's headline equipment count.
    node_by_name = {n["node"]: n for n in network["nodes"]}
    transformer_rows_by_site = defaultdict(set)
    for row_index, transformer in enumerate(network["transformers"]):
        incident_sites = {
            node_by_name[transformer[end]]["site_code"]
            for end in ("node_1", "node_2")
        }
        for site_code in incident_sites:
            transformer_rows_by_site[site_code].add(row_index)
    check("every site transformer headline counts distinct source rows, not node ends",
          all(p["transformers"] == len(transformer_rows_by_site[p["site_code"]])
              for p in points["connection_points"]))
    cowley = point_by_code["COWL"]
    cowley_rows = transformer_rows_by_site["COWL"]
    cowley_windings = Counter(
        node_by_name[transformer[end]]["voltage_kv"]
        for row_index, transformer in enumerate(network["transformers"])
        if row_index in cowley_rows
        for end in ("node_1", "node_2")
        if node_by_name[transformer[end]]["site_code"] == "COWL")
    check("Cowley is five physical records while both voltage winding counts remain five",
          cowley["transformers"] == 5 and len(cowley_rows) == 5
          and cowley_windings == Counter({400: 5, 132: 5}))
    check("the product declares its transformer count semantics",
          "one count per published Appendix B transformer row" in
          points.get("transformer_count_semantics", ""))
    check("the product stays browser-sized",
          os.path.getsize(os.path.join(REPO, "derived", "connection-points.v3.json")) < 3_000_000)

    # A named spot check: if these move, something upstream changed and a
    # reader deserves to hear about it before a map does.
    named = {p["name"].upper(): p for p in points["connection_points"]}
    for site, minimum_circuits in (("COTTAM", 4), ("WEST BURTON", 4),
                                   ("THORPE MARSH", 4), ("BICKER FEN", 4)):
        check(f"{site.title()} is present with its circuits and a location",
              site in named and named[site]["circuits"] >= minimum_circuits
              and "location" in named[site])
    west_burton = named["WEST BURTON"]
    check("West Burton resolves to the voltage-compatible Nottinghamshire feature",
          west_burton["location"]["matched_by"] == "exact_name_highest_voltage"
          and abs(west_burton["location"]["lat"] - 53.359219) < 0.001
          and abs(west_burton["location"]["lon"] + 0.809114) < 0.001)
    check("West Burton keeps 132 kV and 400 kV fault envelopes separate",
          set(west_burton["fault_current_by_voltage"]) == {"132", "400"}
          and west_burton["fault_current_by_voltage"]["400"]["peak"]
              ["metrics"]["three_phase_rms_break_current_ka"]["min"] > 30)

    print(f"\n{passed}/{passed + len(failures)} checks passed")
    if failures:
        print("\nFAILURES")
        for failure in failures:
            print("  " + failure)
        return 1
    print("both products are clean: parameters as published, join reported, "
          "and neither claims to assess a connection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
