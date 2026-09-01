"""Derive the browser-sized connection-point product.

WHAT A MAP COULD SAY BEFORE THIS
--------------------------------
"Nearest substation: Cottam Substation, 400 kV, 10.82 km." A name, a
voltage class and a distance. Everything a network engineer would ask next
was missing, and the map had no way to answer because OpenStreetMap does
not carry it.

WHAT IT CAN SAY AFTER
---------------------
The same substation, plus what the system operator publishes about it: how
many circuits meet there and what they are rated at through the seasons,
the transformers, the reactive plant installed, the fault level range
across NESO's demand snapshots, and whether the operator has already
published changes at that node out to 2033/34. Each is a citation, not an
inference.

THE JOIN, AND ITS HONESTY
-------------------------
ETYS names substations; it does not locate them. Coordinates come from the
OpenStreetMap-derived substation payload. The two are joined on a
normalised name in two tiers - exact after normalisation, then a
distinctive-token match - and every tier is counted in the product. A site
that does not join is published WITHOUT coordinates rather than dropped,
because a consumer that needs to know a node exists should not be told it
does not merely because nobody has mapped it.

    python derived/build_connection_points.py
"""

import io
import json
import os
import re
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

NOISE = re.compile(
    r"\b(SUBSTATION|SUB STATION|SUBSTN|GRID|SUPPLY|POINT|GSP|NATIONAL|"
    r"POWER|STATION|WIND|FARM|WINDFARM|OFFSHORE|ONSHORE|EXTENSION|"
    r"400KV|275KV|132KV|66KV|33KV|11KV|NGET|SSE|SP|SHE)\b")
MINIMUM_KV = 132


def normalise(name):
    text = str(name or "").upper()
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    text = NOISE.sub(" ", text)
    return " ".join(text.split())


def tokens(name):
    return {t for t in normalise(name).split() if len(t) > 3}


def main():
    network = json.load(io.open(
        os.path.join(REPO, "derived", "gb-transmission-network.v1.json"),
        encoding="utf-8"))
    geometry = json.load(io.open(
        os.path.join(REPO, "sources", "grid_substations.geojson"),
        encoding="utf-8"))

    # ── index the mapped substations ─────────────────────────────────────
    mapped_exact, mapped_tokens = defaultdict(list), []
    for feature in geometry.get("features", []):
        properties = feature.get("properties") or {}
        name = str(properties.get("name") or "").strip()
        if not name:
            continue
        coordinates = feature.get("geometry", {}).get("coordinates")
        while isinstance(coordinates, list) and coordinates and isinstance(coordinates[0], list):
            coordinates = coordinates[0]
        if not (isinstance(coordinates, list) and len(coordinates) >= 2
                and isinstance(coordinates[0], (int, float))):
            continue
        # OSM voltage is volts at every magnitude, several separated by ';'.
        volts = [int(v) for v in re.findall(r"\d+", str(properties.get("voltage") or "0"))]
        record = {"name": name, "kv": max(volts) / 1000 if volts else 0.0,
                  "lon": round(coordinates[0], 6), "lat": round(coordinates[1], 6)}
        mapped_exact[normalise(name)].append(record)
        mapped_tokens.append((tokens(name), record))

    # ── everything the model knows, gathered per site ────────────────────
    nodes_by_site = defaultdict(list)
    for node in network["nodes"]:
        nodes_by_site[node["site_code"]].append(node)

    node_site = {node["node"]: node["site_code"] for node in network["nodes"]}
    circuits_at = defaultdict(list)
    for circuit in network["circuits"]:
        for end in ("node_1", "node_2"):
            site = node_site.get(circuit[end])
            if site:
                circuits_at[site].append(circuit)
    transformers_at = defaultdict(int)
    for transformer in network["transformers"]:
        for end in ("node_1", "node_2"):
            site = node_site.get(transformer[end])
            if site:
                transformers_at[site] += 1
    changes_at = defaultdict(list)
    for change in network["planned_changes"]:
        for end in ("node_1", "node_2"):
            site = node_site.get(change[end])
            if site:
                changes_at[site].append(change)
    compensation_at = defaultdict(lambda: {"units": 0, "mvar_generation": 0.0,
                                           "mvar_absorption": 0.0})
    for unit in network["reactive_compensation"]:
        site = node_site.get(unit["node"]) or unit["node"][:4]
        entry = compensation_at[site]
        entry["units"] += 1
        entry["mvar_generation"] += unit.get("mvar_generation") or 0.0
        entry["mvar_absorption"] += unit.get("mvar_absorption") or 0.0

    fault_at = defaultdict(list)
    for scenario in network.get("fault_current_scenarios", []):
        if scenario.get("site_code"):
            fault_at[scenario["site_code"]].append(scenario)

    points, joined_exact, joined_token, unjoined = [], 0, 0, 0
    for site in network["sites"]:
        if not site["voltages_kv"] or max(site["voltages_kv"]) < MINIMUM_KV:
            continue
        code = site["code"]

        key = normalise(site["name"])
        match, how = None, None
        if key and key in mapped_exact:
            match, how = mapped_exact[key][0], "exact_name"
            joined_exact += 1
        else:
            site_tokens = tokens(site["name"])
            if site_tokens:
                for candidate_tokens, candidate in mapped_tokens:
                    if candidate_tokens and site_tokens <= candidate_tokens:
                        match, how = candidate, "distinctive_tokens"
                        joined_token += 1
                        break
        if not match:
            unjoined += 1

        circuits = circuits_at.get(code, [])
        winter = [c["winter_mva"] for c in circuits if c.get("winter_mva")]
        changes = changes_at.get(code, [])
        fault_current = {}
        for demand_case in ("peak", "minimum"):
            scenarios = [row for row in fault_at.get(code, [])
                         if row["demand_case"] == demand_case]
            if not scenarios:
                continue
            metrics = {}
            for metric in network["fault_current_metrics"]:
                values = [row[metric] for row in scenarios]
                metrics[metric] = {"min": round(min(values), 2),
                                   "max": round(max(values), 2), "unit": "kA"}
            fault_current[demand_case] = {
                "scenarios": len(scenarios),
                "winters": sorted({row["winter"] for row in scenarios}),
                "locations": sorted({row["location"] for row in scenarios}),
                "metrics": metrics,
                "aggregation": "envelope across the listed published rows; metrics are not interchangeable",
            }

        point = {
            "site_code": code,
            "name": site["name"],
            "transmission_owner": site["transmission_owner"],
            "voltages_kv": site["voltages_kv"],
            "circuits": len(circuits),
            "transformers": transformers_at.get(code, 0),
            "circuit_winter_rating_mva": (
                {"min": round(min(winter)), "max": round(max(winter))} if winter else None),
            "reactive_compensation": (
                {"units": compensation_at[code]["units"],
                 "mvar_generation": round(compensation_at[code]["mvar_generation"]),
                 "mvar_absorption": round(compensation_at[code]["mvar_absorption"])}
                if code in compensation_at else None),
            "fault_current": fault_current or None,
            "planned_changes": len(changes),
            "planned_change_years": sorted({c["year"] for c in changes if c.get("year")}),
        }
        if match:
            point["location"] = {"lat": match["lat"], "lon": match["lon"],
                                 "mapped_name": match["name"], "matched_by": how}
        points.append(point)

    points.sort(key=lambda p: p["site_code"])
    product = {
        "schema": "data-grid-gb.connection-points.v2",
        "what_this_is": (
            "Every transmission substation NESO names at 132 kV and above, "
            "with what the operator publishes about it: circuits and their "
            "seasonal ratings, transformers, reactive plant, eight separately "
            "named fault-current metrics, and changes planned to 2033/34. Coordinates "
            "are joined from the OpenStreetMap-derived substation payload "
            "where a join exists."),
        "not_a_connection_assessment": (
            "Nothing here says a project can or cannot connect at a node. "
            "Queue position, committed connections, consent and commercial "
            "terms decide that, and no published appendix contains them."),
        "source": network["source"],
        "minimum_kv": MINIMUM_KV,
        "join": {
            "why": "ETYS names substations and does not locate them",
            "geometry_source": "OpenStreetMap contributors, via the GridAtlas release",
            "exact_name": joined_exact,
            "distinctive_tokens": joined_token,
            "unlocated": unjoined,
            "unlocated_are_published": (
                "a site nobody has mapped is published without coordinates "
                "rather than dropped"),
        },
        "counts": {
            "connection_points": len(points),
            "with_location": len(points) - unjoined,
            "with_fault_current": sum(1 for p in points if p["fault_current"]),
            "with_planned_changes": sum(1 for p in points if p["planned_changes"]),
        },
        "connection_points": points,
    }

    out = os.path.join(REPO, "derived", "connection-points.v2.json")
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(product, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote derived/connection-points.v2.json "
          f"({os.path.getsize(out) / 1024:.0f} kB)")
    for key, value in product["counts"].items():
        print(f"  {key:<26} {value:>6,}")
    print(f"  join: exact {joined_exact}, tokens {joined_token}, unlocated {unjoined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
