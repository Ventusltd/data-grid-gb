"""Fail-closed Grid Journey review ledger shared with ventus-grid-engine.

A valid receipt is a structural prerequisite, not a claim that this program
independently reviewed an asset or granted a licence. Real data defaults to
withheld. The contract counts source records, never physical equipment units.
"""
import hashlib
import json
import math
import re
from datetime import datetime
from urllib.parse import urlparse

SCHEMA = "data-grid-gb.grid-journey-review.v1"
PRODUCT_SCHEMA = "data-grid-gb.transmission-network.v1"
KINDS = {"circuit": "circuits", "transformer": "transformers", "planned_change": "planned_changes"}
GEOMETRY = {"verified_circuit_and_polyline", "published_topology_only", "published_node_unlocated",
            "vicinity_screening_only", "ambiguous_or_licence_blocked"}
HEX = re.compile(r"^[0-9a-f]{64}$")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def edge_uid(bundle_sha, kind, index):
    if not isinstance(bundle_sha, str) or not HEX.fullmatch(bundle_sha):
        raise ValueError("sourceBundleSha must be a SHA256")
    if kind not in KINDS or type(index) is not int or index < 0:
        raise ValueError("invalid source-row identity")
    return f"{bundle_sha}:{kind}:{index}"


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")


def _url(value, label):
    _text(value, label)
    if re.search(r"[\s\\]", value) or not value.startswith("https://"):
        raise ValueError(f"{label} contains forbidden URL whitespace, backslash or scheme")
    u = urlparse(value)
    host = u.hostname or ""
    valid_host = len(host) <= 253 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) for part in host.split("."))
    try:
        valid_port = u.port is None or 1 <= u.port <= 65535
    except ValueError:
        valid_port = False
    if u.scheme != "https" or not valid_host or not valid_port or u.username or u.password:
        raise ValueError(f"{label} must be an absolute credential-free HTTPS URL")


def _receipt(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} requires an explicit review receipt")
    for key in ("id", "reviewer"):
        _text(value.get(key), f"{label}.{key}")
    stamp = value.get("reviewedAt")
    if not isinstance(stamp, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp):
        raise ValueError(f"{label}.reviewedAt must be a UTC second-precision instant")
    datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    _url(value.get("reference"), f"{label}.reference")


def _source(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} requires a source locator")
    _url(value.get("url"), f"{label}.url")
    _text(value.get("locator"), f"{label}.locator")


def _rights(value, label):
    if not isinstance(value, dict) or value.get("status") not in {"accepted", "unconfirmed", "blocked"}:
        raise ValueError(f"{label} has invalid rights status")
    if value["status"] == "accepted":
        _url(value.get("licenceUrl"), f"{label}.licenceUrl")
        _text(value.get("attribution"), f"{label}.attribution")
        _receipt(value.get("review"), f"{label}.review")


def _identity(value, label):
    if not isinstance(value, dict) or value.get("status") not in {"confirmed", "unreviewed", "ambiguous"}:
        raise ValueError(f"{label} has invalid identity status")
    if value["status"] == "confirmed":
        _receipt(value.get("review"), f"{label}.review")


def _position(value, label):
    if (not isinstance(value, list) or len(value) != 2 or
            any(type(v) not in (float, int) or not math.isfinite(v) for v in value) or
            not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90):
        raise ValueError(f"{label} must be finite WGS84 [longitude,latitude]")


def validate_product(product):
    if not isinstance(product, dict) or product.get("schema") != PRODUCT_SCHEMA:
        raise ValueError("unsupported network product")
    for key in ("nodes", *KINDS.values()):
        if not isinstance(product.get(key), list):
            raise ValueError(f"network.{key} must be an array")
    if len(product["nodes"]) > 100_000 or sum(len(product[k]) for k in KINDS.values()) > 200_000:
        raise ValueError("network exceeds contract limits")
    seen = set()
    def node_id(value):
        _text(value, "source node ID")
        if re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError("source node ID contains control characters")
    for n in product["nodes"]:
        if not isinstance(n, dict):
            raise ValueError("network node must be an object")
        node_id(n.get("node"))
        if n["node"] in seen:
            raise ValueError("duplicate source node identity")
        seen.add(n["node"])
    for key in KINDS.values():
        for row in product[key]:
            if not isinstance(row, dict):
                raise ValueError("network branch must be an object")
            node_id(row.get("node_1"))
            node_id(row.get("node_2"))
    return product


def validate_ledger(ledger, product=None, expected_sha=None):
    """Validate receipt shapes and exact source-row references; never infer joins."""
    if not isinstance(ledger, dict) or ledger.get("schema") != SCHEMA:
        raise ValueError("unsupported review ledger schema")
    digest = ledger.get("sourceBundleSha")
    edge_uid(digest, "circuit", 0)
    if expected_sha is not None and digest != expected_sha:
        raise ValueError("review ledger source digest does not match actual input bytes")
    if product is not None:
        validate_product(product)
    if not isinstance(ledger.get("nodes"), list) or not isinstance(ledger.get("edges"), list):
        raise ValueError("review ledger requires node and edge arrays")
    if len(ledger["nodes"]) > 100_000 or len(ledger["edges"]) > 200_000:
        raise ValueError("review ledger exceeds contract limits")
    source_nodes = {n["node"] for n in product.get("nodes", [])} if product is not None else None
    nodes, edges = set(), set()
    for entry in ledger["nodes"]:
        if not isinstance(entry, dict):
            raise ValueError("node review must be an object")
        nid = entry.get("nodeId")
        _text(nid, "nodeId")
        if nid in nodes or source_nodes is not None and nid not in source_nodes:
            raise ValueError("duplicate or unknown node review")
        nodes.add(nid)
        _identity(entry.get("identity"), "node.identity")
        _rights(entry.get("rights"), "node.rights")
        _source(entry.get("sourceRef"), "node.sourceRef")
        loc = entry.get("location", {"status": "unlocated"})
        if not isinstance(loc, dict) or loc.get("status") not in {"reviewed", "unreviewed", "unlocated", "ambiguous"}:
            raise ValueError("invalid node location state")
        if loc["status"] == "reviewed":
            _position(loc.get("coordinates"), "location.coordinates")
            _source(loc.get("sourceRef"), "location.sourceRef")
            _receipt(loc.get("review"), "location.review")
            _rights(loc.get("rights"), "location.rights")
    for entry in ledger["edges"]:
        if not isinstance(entry, dict):
            raise ValueError("edge review must be an object")
        uid = edge_uid(digest, entry.get("kind"), entry.get("sourceIndex"))
        if entry.get("edgeUid") != uid or uid in edges:
            raise ValueError("duplicate or inconsistent source-row UID")
        edges.add(uid)
        for key in ("node1", "node2"):
            _text(entry.get(key), key)
        if product is not None:
            rows = product.get(KINDS[entry["kind"]], [])
            i = entry["sourceIndex"]
            if i >= len(rows) or (entry["node1"], entry["node2"]) != (rows[i].get("node_1"), rows[i].get("node_2")):
                raise ValueError("review refers to a different source row or endpoint")
        _identity(entry.get("identity"), "edge.identity")
        _rights(entry.get("rights"), "edge.rights")
        _source(entry.get("sourceRef"), "edge.sourceRef")
        if entry.get("multiplicityStatus") not in {"reviewed", "unreviewed", "ambiguous"}:
            raise ValueError("edge multiplicity review status is required")
        if entry["multiplicityStatus"] == "reviewed":
            _receipt(entry.get("multiplicityReview"), "edge.multiplicityReview")
        if entry.get("geometryStatus") not in GEOMETRY:
            raise ValueError("unknown geometry evidence class")
        if entry["kind"] == "transformer" and entry["identity"]["status"] == "confirmed":
            _text(entry.get("transformerRef"), "transformerRef")
        if entry["geometryStatus"] == "verified_circuit_and_polyline":
            geom = entry.get("geometry")
            if not isinstance(geom, dict) or not isinstance(geom.get("coordinates"), list) or len(geom["coordinates"]) < 2:
                raise ValueError("verified polyline requires at least two reviewed coordinates")
            for pos in geom["coordinates"]:
                _position(pos, "geometry.coordinates")
            _source(geom.get("sourceRef"), "geometry.sourceRef")
            _receipt(geom.get("review"), "geometry.review")
            _rights(geom.get("rights"), "geometry.rights")
    return ledger


def classify_edges(product, ledger, expected_sha=None):
    """Return independent refusal reasons and geometry classes per source row."""
    validate_ledger(ledger, product, expected_sha)
    nodes = {n["node"]: n for n in product.get("nodes", [])}
    reviews = {n["nodeId"]: n for n in ledger["nodes"]}
    edge_reviews = {e["edgeUid"]: e for e in ledger["edges"]}
    result = []
    for kind, key in KINDS.items():
        for i, row in enumerate(product.get(key, [])):
            uid = edge_uid(ledger["sourceBundleSha"], kind, i)
            e = edge_reviews.get(uid)
            reasons = []
            if kind == "planned_change":
                reasons.append("planned")
            if not e:
                reasons.append("edge_unreviewed")
            else:
                if e["identity"]["status"] != "confirmed":
                    reasons.append("edge_identity")
                if e["rights"]["status"] != "accepted":
                    reasons.append("edge_rights")
                if e["multiplicityStatus"] != "reviewed":
                    reasons.append("multiplicity")
                if e["geometryStatus"] in {"vicinity_screening_only", "ambiguous_or_licence_blocked"}:
                    reasons.append("geometry_withheld")
            positions, volts = [], []
            for nid in (row.get("node_1"), row.get("node_2")):
                n, review = nodes.get(nid), reviews.get(nid)
                if n is None:
                    reasons.append("orphan_node")
                voltage = n.get("voltage_kv") if n and n.get("voltage_consistent_with_site") is True else None
                if type(voltage) not in (float, int) or not math.isfinite(voltage) or voltage <= 0:
                    reasons.append("unknown_voltage")
                    voltage = None
                volts.append(voltage)
                if not review or review["identity"]["status"] != "confirmed":
                    reasons.append("node_identity")
                if not review or review["rights"]["status"] != "accepted":
                    reasons.append("node_rights")
                loc = review.get("location", {}) if review else {}
                positions.append(loc.get("coordinates") if loc.get("status") == "reviewed" and loc.get("rights", {}).get("status") == "accepted" else None)
            if kind == "circuit" and all(v is not None for v in volts) and volts[0] != volts[1]:
                reasons.append("illegal_voltage")
            status = e["geometryStatus"] if e else "ambiguous_or_licence_blocked"
            if e and status == "verified_circuit_and_polyline":
                geom = e["geometry"]
                if geom["rights"]["status"] != "accepted":
                    reasons.append("geometry_rights")
                line = geom["coordinates"]
                if None in positions or (line[0], line[-1]) not in ((positions[0], positions[1]), (positions[1], positions[0])):
                    reasons.append("geometry_endpoint_mismatch")
            elif status == "published_topology_only" and None in positions:
                status = "published_node_unlocated"
            result.append({"edgeUid": uid, "kind": kind, "sourceIndex": i,
                           "node1": row.get("node_1"), "node2": row.get("node_2"),
                           "eligible": not reasons, "reasons": sorted(set(reasons)), "geometryStatus": status})
    return result


def inventory(network_bytes, points_bytes=None):
    """Counts/hashes only: creates no accepted identity, rights or route."""
    product = json.loads(network_bytes, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("invalid JSON constant " + x)))
    validate_product(product)
    digest = sha256(network_bytes)
    points = json.loads(points_bytes) if points_bytes is not None else None
    if points is not None and points.get("schema") != "data-grid-gb.connection-points.v3":
        raise ValueError("unsupported connection-point product")
    ledger = {"schema": SCHEMA, "sourceBundleSha": digest, "nodes": [], "edges": []}
    classified = classify_edges(product, ledger, digest)
    return {"schema": "data-grid-gb.grid-journey-inventory.v1", "sourceBundleSha": digest,
            "sourceBytes": len(network_bytes), "counts": {"nodes": len(product.get("nodes", [])),
            "circuitRows": len(product.get("circuits", [])), "transformerRows": len(product.get("transformers", [])),
            "plannedRows": len(product.get("planned_changes", [])), "eligibleEdges": sum(e["eligible"] for e in classified)},
            "pointsBundleSha": sha256(points_bytes) if points_bytes is not None else None,
            "automatedPointJoinCounts": points.get("counts") if points else None,
            "manualIdentitiesApproved": 0, "rightsAccepted": False, "reviewLedgerTemplate": ledger,
            "caveat": "Input counts and automated coordinates are not reviewed circuit identities, real routes, current flow or connection capacity."}
