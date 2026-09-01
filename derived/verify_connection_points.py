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
    points = json.load(io.open(os.path.join(REPO, "derived", "connection-points.v1.json"),
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

    print("\nthe connection points\n")
    check("schema is named and versioned",
          points.get("schema") == "data-grid-gb.connection-points.v1")
    check("it refuses to be read as a connection assessment",
          "no published appendix contains" in points["not_a_connection_assessment"]
          and "queue position" in points["not_a_connection_assessment"].lower())
    check("nothing below the declared minimum voltage is published",
          all(max(p["voltages_kv"]) >= points["minimum_kv"] for p in points["connection_points"]))
    join = points["join"]
    check("the join is reported by tier, not as a single number",
          all(k in join for k in ("exact_name", "distinctive_tokens", "unlocated")))
    check("the join total equals the number of points",
          join["exact_name"] + join["distinctive_tokens"] + join["unlocated"]
          == len(points["connection_points"]))
    check("unlocated sites are published rather than dropped",
          join["unlocated"] > 0
          and sum(1 for p in points["connection_points"] if "location" not in p)
          == join["unlocated"])
    check("every located point carries plausible GB coordinates",
          all(49 < p["location"]["lat"] < 61 and -9 < p["location"]["lon"] < 3
              for p in points["connection_points"] if "location" in p))
    check("a located point says how it was matched",
          all(p["location"]["matched_by"] in ("exact_name", "distinctive_tokens")
              for p in points["connection_points"] if "location" in p))
    check("fault levels, where published, are a range with a snapshot count",
          all(all("three_phase_break_ka_min" in e and "three_phase_break_ka_max" in e
                  and e.get("snapshots", 0) > 0
                  for e in (p["fault_level"] or {}).values())
              for p in points["connection_points"] if p["fault_level"]))
    check("fault level minima never exceed their maxima",
          all(e["three_phase_break_ka_min"] <= e["three_phase_break_ka_max"]
              for p in points["connection_points"] if p["fault_level"]
              for e in p["fault_level"].values()))
    check("planned change years are consistent with their count",
          all((p["planned_changes"] > 0) == bool(p["planned_change_years"])
              for p in points["connection_points"]))
    check("the product stays browser-sized",
          os.path.getsize(os.path.join(REPO, "derived", "connection-points.v1.json")) < 1_500_000)

    # A named spot check: if these move, something upstream changed and a
    # reader deserves to hear about it before a map does.
    named = {p["name"].upper(): p for p in points["connection_points"]}
    for site, minimum_circuits in (("COTTAM", 4), ("WEST BURTON", 4),
                                   ("THORPE MARSH", 4), ("BICKER FEN", 4)):
        check(f"{site.title()} is present with its circuits and a location",
              site in named and named[site]["circuits"] >= minimum_circuits
              and "location" in named[site])

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
