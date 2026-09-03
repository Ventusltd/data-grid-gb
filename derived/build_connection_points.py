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
    # ONSHORE, OFFSHORE and EXTENSION are identity-bearing qualifiers.  They
    # must never be treated as presentation noise: removing them aliases
    # physically separate sites such as MORAY EAST ONSHORE/OFFSHORE.
    r"POWER|STATION|WIND|FARM|WINDFARM|"
    r"400KV|275KV|132KV|66KV|33KV|11KV|NGET|SSE|SP|SHE)\b")
MINIMUM_KV = 132
SCHEMA = "data-grid-gb.connection-points.v3"
OUTPUT = "connection-points.v3.json"


def normalise(name):
    text = str(name or "").upper()
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    text = NOISE.sub(" ", text)
    return " ".join(text.split())


def tokens(name):
    return {t for t in normalise(name).split() if len(t) > 3}


def shore_qualifier(name):
    """Return the last explicit onshore/offshore qualifier, if one exists.

    Mapped names sometimes contain an offshore project's name followed by
    "onshore substation".  The last explicit qualifier describes the mapped
    asset more specifically; treating both words as an unordered token set
    would let the same point satisfy both authoritative sites.
    """
    words = re.findall(r"[A-Z0-9]+", str(name or "").upper())
    qualifiers = [word for word in words if word in {"ONSHORE", "OFFSHORE"}]
    return qualifiers[-1] if qualifiers else None


def qualifier_compatible(authoritative_name, mapped_name):
    wanted = shore_qualifier(authoritative_name)
    mapped = shore_qualifier(mapped_name)
    return wanted is None or mapped is None or wanted == mapped


def site_join_context(site):
    """Return the strongest context ETYS itself supplies for a name join.

    Mapped OpenStreetMap features do not carry a trustworthy transmission
    owner, so owner is used to distinguish authoritative ETYS identities, not
    to force a geometry match.  A context that is still duplicated must fail
    closed; the stable site code remains the only unambiguous identifier.
    """
    voltages = site.get("voltages_kv") or []
    highest = max(voltages) if voltages else None
    return (normalise(site.get("name")), highest,
            str(site.get("transmission_owner") or "").upper())


def serialise_join_context(context):
    name, voltage, owner = context
    voltage_text = f"{float(voltage):g}KV" if voltage is not None else "UNKNOWNKV"
    return "|".join((name, voltage_text, owner))


def summarise_fault_rows(rows, metric_names, scope):
    """Keep a published-row envelope explicit about what it combines."""
    metrics = {}
    for metric in metric_names:
        values = [row[metric] for row in rows]
        metrics[metric] = {"min": round(min(values), 2),
                           "max": round(max(values), 2), "unit": "kA"}
    return {
        "scenarios": len(rows),
        "winters": sorted({row["winter"] for row in rows}),
        "locations": sorted({row["location"] for row in rows}),
        "voltages_kv": sorted({row["voltage_kv"] for row in rows}),
        "metrics": metrics,
        "scope": scope,
        "aggregation": "envelope across the listed published rows; metrics, voltages and buses are not interchangeable",
    }


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
    # One Appendix B transformer row is one physical transformer record.  Its
    # two node ends are windings/landings, and both commonly resolve to the
    # same site.  Count each source row once per incident site without
    # collapsing genuinely parallel (and sometimes byte-identical) units.
    transformers_at = defaultdict(set)
    for transformer_index, transformer in enumerate(network["transformers"]):
        incident_sites = {
            node_site.get(transformer[end]) for end in ("node_1", "node_2")
        } - {None}
        for site in incident_sites:
            transformers_at[site].add(transformer_index)
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

    eligible_sites = [
        site for site in network["sites"]
        if site["voltages_kv"] and max(site["voltages_kv"]) >= MINIMUM_KV
    ]
    context_claims = defaultdict(list)
    for site in eligible_sites:
        context_claims[site_join_context(site)].append(site["code"])

    points, joined_exact, joined_token = [], 0, 0
    ambiguous_exact, ambiguous_token = 0, 0
    ambiguous_identity, qualifier_conflict, unjoined = 0, 0, 0
    for site in eligible_sites:
        code = site["code"]

        key = normalise(site["name"])
        context = site_join_context(site)
        context_unique = len(context_claims[context]) == 1
        match, how = None, None
        if not context_unique:
            # Name + highest voltage + owner still cannot tell these records
            # apart (currently the two Erebus rows).  Never let file order pick
            # one geometry for an ambiguous authoritative identity.
            ambiguous_identity += 1
        elif key and key in mapped_exact:
            raw_candidates = mapped_exact[key]
            candidates = [candidate for candidate in raw_candidates
                          if qualifier_compatible(site["name"], candidate["name"])]
            highest = max(site["voltages_kv"])
            compatible = [candidate for candidate in candidates
                          if candidate["kv"] == highest]
            if len(compatible) == 1:
                match, how = compatible[0], "exact_name_highest_voltage"
                joined_exact += 1
            elif len(candidates) == 1 and (not candidates[0]["kv"]
                                           or candidates[0]["kv"] in site["voltages_kv"]):
                match, how = candidates[0], "exact_name_voltage_compatible"
                joined_exact += 1
            elif not candidates and raw_candidates:
                qualifier_conflict += 1
            else:
                ambiguous_exact += 1
        else:
            site_tokens = tokens(site["name"])
            if site_tokens:
                raw_candidates = [candidate for candidate_tokens, candidate in mapped_tokens
                                  if candidate_tokens and site_tokens <= candidate_tokens]
                candidates = [candidate for candidate in raw_candidates
                              if qualifier_compatible(site["name"], candidate["name"])]
                highest = max(site["voltages_kv"])
                compatible = [candidate for candidate in candidates
                              if candidate["kv"] == highest]
                if len(compatible) == 1:
                    match, how = compatible[0], "distinctive_tokens_highest_voltage"
                    joined_token += 1
                elif candidates:
                    ambiguous_token += 1
                elif raw_candidates:
                    qualifier_conflict += 1
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
            fault_current[demand_case] = summarise_fault_rows(
                scenarios, network["fault_current_metrics"],
                "site-wide envelope; may combine voltage levels and buses")

        fault_by_voltage = {}
        for voltage in sorted({row["voltage_kv"] for row in fault_at.get(code, [])}):
            cases = {}
            for demand_case in ("peak", "minimum"):
                scenarios = [row for row in fault_at.get(code, [])
                             if row["demand_case"] == demand_case
                             and row["voltage_kv"] == voltage]
                if scenarios:
                    cases[demand_case] = summarise_fault_rows(
                        scenarios, network["fault_current_metrics"],
                        f"{voltage:g} kV published-row envelope")
            if cases:
                voltage_key = str(int(voltage) if float(voltage).is_integer() else voltage)
                fault_by_voltage[voltage_key] = cases

        point = {
            "site_code": code,
            "name": site["name"],
            "transmission_owner": site["transmission_owner"],
            "voltages_kv": site["voltages_kv"],
            # The component fields already appear above.  This compact key is
            # safe for a consumer lookup only when ETYS makes the context
            # unique; null means callers must use site_code or fail closed.
            "join_context_key": (
                serialise_join_context(context) if context_unique else None),
            "circuits": len(circuits),
            "transformers": len(transformers_at.get(code, set())),
            "circuit_winter_rating_mva": (
                {"min": round(min(winter)), "max": round(max(winter))} if winter else None),
            "reactive_compensation": (
                {"units": compensation_at[code]["units"],
                 "mvar_generation": round(compensation_at[code]["mvar_generation"]),
                 "mvar_absorption": round(compensation_at[code]["mvar_absorption"])}
                if code in compensation_at else None),
            "fault_current": fault_current or None,
            "fault_current_by_voltage": fault_by_voltage or None,
            "planned_changes": len(changes),
            "planned_change_years": sorted({c["year"] for c in changes if c.get("year")}),
        }
        if match:
            point["location"] = {"lat": match["lat"], "lon": match["lon"],
                                 "mapped_name": match["name"], "matched_by": how}
        points.append(point)

    points.sort(key=lambda p: p["site_code"])
    product = {
        "schema": SCHEMA,
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
            "ambiguous_exact_name": ambiguous_exact,
            "ambiguous_distinctive_tokens": ambiguous_token,
            "ambiguous_authoritative_identity": ambiguous_identity,
            "rejected_shore_qualifier_conflict": qualifier_conflict,
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
        "transformer_count_semantics": (
            "one count per published Appendix B transformer row incident to the site; "
            "the two winding/node ends of one row are not two physical units"),
        "join_context_semantics": (
            "join_context_key combines normalised name, highest published voltage and "
            "transmission owner; null means that context is ambiguous and site_code is "
            "required. Geometry owner tags are not trusted to force a match."),
        "connection_points": points,
    }

    out = os.path.join(REPO, "derived", OUTPUT)
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        json.dumps(product, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote derived/{OUTPUT} "
          f"({os.path.getsize(out) / 1024:.0f} kB)")
    for key, value in product["counts"].items():
        print(f"  {key:<26} {value:>6,}")
    print(f"  join: exact {joined_exact}, tokens {joined_token}, "
          f"ambiguous exact {ambiguous_exact}, ambiguous tokens {ambiguous_token}, "
          f"ambiguous identities {ambiguous_identity}, "
          f"shore qualifier conflicts {qualifier_conflict}, "
          f"unlocated {unjoined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
