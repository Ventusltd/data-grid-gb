"""Offline GJ-05 scalar research context, never circuit flow or source enablement.

Only the NESO day-ahead named-boundary profile is implemented. Source-local
GMT/BST labels stay unresolved: no timezone database, inferred offset or arrow.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlparse

from .contract import encoded, sha256

SCHEMA = "data-grid-gb.boundary-forecast-context.v1"
PINS_SCHEMA = "data-grid-gb.gj05-source-pins.v1"
PROFILE = "neso-day-ahead-boundary"
DATASET = "day-ahead-constraint-flows-and-limits"
DATASET_ID = "cf3cbc92-2d5d-4c2b-bd29-e11a21070b26"
RESOURCE_ID = "38a18ec1-9e40-465d-93fb-301e80fd1352"
LICENCE = "https://www.neso.energy/data-portal/ngeso-open-licence"
ATTRIBUTION = "Supported by National Energy SO Open Data"
METADATA_URL = "https://api.neso.energy/api/3/action/datapackage_show?id=" + DATASET
FIELDS = {"Constraint Group": "text", "Date_ Time GMT_BST": "timestamp", "Limit_MW": "int4", "Flow_MW": "numeric"}
SCOPES = {"metadata": "metadata_response_bytes", "sample": "bounded_api_response_bytes_not_raw_csv",
          "receipt": "local_observation_receipt_bytes"}
UNSUPPORTED = {
    "ukpn-primary-transformer": "UKPN_POLARITY_INSTRUMENT_AND_RIGHTS_NOT_REVIEWED",
    "elexon-interconnector": "ELEXON_SERIES_SIGN_TIME_AND_GRANULARITY_NOT_REVIEWED",
}
MAX_BYTES = 65536


class Refusal(ValueError):
    """A deterministic, machine-readable refusal rather than an inferred value."""


def require(condition, code):
    if not condition:
        raise Refusal(code)


def parse_json(raw):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, "INPUT_BYTES_OR_BUDGET")
    try:
        return json.loads(raw.decode("utf-8"), parse_float=Decimal,
                          parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (UnicodeError, ValueError, RecursionError, InvalidOperation):
        raise Refusal("INVALID_JSON_OR_UTF8") from None


def utc_receipt(raw):
    require(isinstance(raw, str), "OBSERVATION_CLOCK_MISSING")
    require(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", raw), "OBSERVATION_CLOCK_OFFSET_REQUIRED")
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        require(value.tzinfo is not None and value.utcoffset() is not None, "OBSERVATION_CLOCK_OFFSET_REQUIRED")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError) as error:
        if isinstance(error, Refusal):
            raise
        raise Refusal("INVALID_OBSERVATION_CLOCK") from None


def raw_metadata_clock(raw):
    # Metadata MTIME has no supplied timezone. A missing offset is not UTC.
    return {"raw": raw if isinstance(raw, str) else None, "utc": None,
            "state": "SOURCE_METADATA_CLOCK_UNRESOLVED" if isinstance(raw, str) and raw else "UNKNOWN"}


def local_clock(raw):
    result = {"raw": raw if isinstance(raw, str) else None, "sourceField": "Date_ Time GMT_BST", "civilLabel": None,
              "basis": "SOURCE_GMT_BST_LOCAL_LABEL", "utc": None, "intervalPosition": "UNKNOWN",
              "state": "UTC_WITHHELD_LOCAL_OFFSET_UNRESOLVED"}
    if not isinstance(raw, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", raw):
        result["state"] = "INVALID_OR_UNSUPPORTED_LOCAL_LABEL"
        return result
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        result["state"] = "INVALID_LOCAL_CALENDAR_LABEL"
        return result
    if value.minute not in (0, 30) or value.second != 0:
        result["state"] = "NOT_A_HALF_HOUR_LABEL"
    else:
        result["civilLabel"] = raw
    # This is a civil string, not an accepted Europe/London instant. Fold and
    # gap labels never acquire a fabricated offset, UTC time or settlement ID.
    return result


def megawatts(raw, *, limit=False):
    source_type = "null" if raw is None else "boolean" if type(raw) is bool else "string" if isinstance(raw, str) else "number"
    value = {"valueDecimal": None, "unit": "MW", "sourceRaw": None if raw is None else str(raw),
             "sourceJsonType": source_type, "state": "UNKNOWN"}
    if raw is None:
        return value
    if type(raw) is bool or not isinstance(raw, (str, int, Decimal)):
        value["state"] = "INVALID_MW_VALUE"
        return value
    token = str(raw)
    if not re.fullmatch(r"[+-]?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", token):
        value["state"] = "INVALID_MW_VALUE"
        return value
    try:
        parsed = Decimal(token)
    except InvalidOperation:
        value["state"] = "INVALID_MW_VALUE"
        return value
    if not parsed.is_finite() or limit and parsed < 0:
        value["state"] = "INVALID_NEGATIVE_LIMIT" if limit and parsed < 0 else "INVALID_MW_VALUE"
        return value
    value.update(valueDecimal=str(parsed), state="REPORTED_SCALAR")
    return value


def verify_inputs(metadata_bytes, sample_bytes, receipt_bytes, pins):
    require(isinstance(pins, dict) and set(pins) == {"schema", "profile", "attribution", "metadata", "sample", "receipt"}, "PIN_MANIFEST_SCHEMA")
    require(pins["schema"] == PINS_SCHEMA and pins["profile"] == PROFILE and pins["attribution"] == ATTRIBUTION, "PIN_PROFILE_OR_ATTRIBUTION")
    hashes = {}
    for name, raw in (("metadata", metadata_bytes), ("sample", sample_bytes), ("receipt", receipt_bytes)):
        pin = pins[name]
        require(isinstance(pin, dict) and pin.get("scope") == SCOPES[name], "HASH_SCOPE_" + name.upper())
        require(type(raw) is bytes and len(raw) <= (8192 if name == "receipt" else MAX_BYTES), "INPUT_BUDGET_" + name.upper())
        require(type(pin.get("bytes")) is int and pin["bytes"] == len(raw), "BYTE_LENGTH_" + name.upper())
        require(isinstance(pin.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]) and sha256(raw) == pin["sha256"], "SHA256_" + name.upper())
        hashes[name] = {"sha256": pin["sha256"], "bytes": len(raw), "scope": pin["scope"]}
    require(pins["metadata"].get("url") == METADATA_URL, "METADATA_SOURCE_URL")
    utc_receipt(pins["metadata"].get("retrievedAt"))
    return hashes


def source_contract(metadata, sample, receipt, pins, boundary):
    require(isinstance(metadata, dict) and metadata.get("success") is True and isinstance(metadata.get("result"), dict), "UNSUCCESSFUL_METADATA")
    package = metadata["result"]
    require(package.get("name") == DATASET and package.get("id") == DATASET_ID, "WRONG_DATASET")
    licences = package.get("licenses")
    require(isinstance(licences, list) and len(licences) == 1 and isinstance(licences[0], dict) and licences[0].get("path") == LICENCE, "DATASET_LICENCE_DECLARATION_MISMATCH")
    resources = package.get("resources")
    require(isinstance(resources, list), "RESOURCE_LIST_MISSING")
    selected = [r for r in resources if isinstance(r, dict) and r.get("id") == RESOURCE_ID]
    require(len(selected) == 1, "RESOURCE_ID_MISSING_OR_DUPLICATE")
    resource = selected[0]
    require(resource.get("package_id") == DATASET_ID and resource.get("format") == "CSV" and resource.get("datastore_active") is True, "WRONG_RESOURCE_PROFILE")
    csv_url = resource.get("path")
    require(isinstance(csv_url, str) and csv_url.startswith(f"https://api.neso.energy/dataset/{DATASET_ID}/resource/{RESOURCE_ID}/download/") and csv_url.endswith(".csv"), "RAW_CSV_SOURCE_URL")
    note = " ".join(str(resource.get("description", "")).lower().split())
    require("22nd april 2024" in note and "no longer consider expected constraint mitigation or optimisation actions" in note, "METHOD_BREAK_NOTE_MISSING")
    require(isinstance(sample, dict) and sample.get("success") is True and isinstance(sample.get("result"), dict), "UNSUCCESSFUL_SAMPLE")
    result = sample["result"]
    fields = result.get("fields")
    require(isinstance(fields, list) and len(fields) == len(FIELDS) and all(isinstance(f, dict) for f in fields), "SOURCE_FIELD_CONTRACT")
    require({f.get("id"): f.get("type") for f in fields} == FIELDS, "SOURCE_UNITS_OR_FIELDS")
    require(isinstance(receipt, dict) and type(receipt.get("http")) is int and receipt.get("http") == 200 and receipt.get("success") is True, "UNSUCCESSFUL_RECEIPT")
    require(receipt.get("sha256") == pins["sample"]["sha256"] and receipt.get("bytes") == pins["sample"]["bytes"], "RECEIPT_HASH_SCOPE")
    require(receipt.get("url") == pins["sample"].get("url"), "SAMPLE_SOURCE_URL_MISMATCH")
    require(isinstance(receipt.get("url"), str) and not re.search(r"[\s\\]", receipt["url"]), "SAMPLE_SOURCE_AUTHORITY")
    try:
        url = urlparse(receipt["url"])
        port = url.port
    except ValueError:
        raise Refusal("SAMPLE_SOURCE_AUTHORITY") from None
    require(url.scheme == "https" and url.hostname == "api.neso.energy" and port in (None, 443) and not url.username and not url.password and not url.fragment and url.path == "/api/3/action/datastore_search_sql", "SAMPLE_SOURCE_AUTHORITY")
    query = parse_qs(url.query)
    require(set(query) == {"sql"} and len(query["sql"]) == 1 and query["sql"][0] == result.get("sql"), "SOURCE_QUERY_MISMATCH")
    sql = " ".join(query["sql"][0].split())
    expected = f'SELECT "Constraint Group", "Date_ Time GMT_BST", "Limit_MW", "Flow_MW" FROM "{RESOURCE_ID}" WHERE "Constraint Group" = \'{boundary}\' ORDER BY "Date_ Time GMT_BST" DESC LIMIT 3'
    require(sql == expected, "BOUNDARY_QUERY_OR_RESOURCE_MISMATCH")
    rows = result.get("records")
    require(isinstance(rows, list) and len(rows) <= 3 and all(isinstance(r, dict) for r in rows), "BOUNDED_ROW_CONTRACT")
    require(receipt.get("records") == rows, "RECEIPT_RECORDS_MISMATCH")
    require(all(r.get("Constraint Group") == boundary for r in rows), "BOUNDARY_KEY_MISMATCH")
    require(all(set(r).issubset(FIELDS) for r in rows), "UNSUPPORTED_ROW_FIELDS")
    labels = [r.get("Date_ Time GMT_BST") for r in rows if isinstance(r.get("Date_ Time GMT_BST"), str)]
    require(len(set(labels)) == len(labels), "DUPLICATE_LOCAL_TARGET_LABEL")
    if len(labels) == len(rows) and all(local_clock(label)["civilLabel"] for label in labels):
        require(labels == sorted(labels, reverse=True), "SOURCE_QUERY_ORDER_MISMATCH")
    utc_receipt(receipt.get("retrievedAt"))
    return package, resource, rows, resources.index(resource)


def base_report(profile):
    return {"schema": SCHEMA, "profile": profile if isinstance(profile, str) else None,
            "status": "WITHHELD", "publishable": False, "productionEnabled": False,
            "direction": None, "polarity": None, "arrow": None, "headroomMw": None,
            "actualOverload": None, "repdLink": None, "etysLink": None, "circuitLink": None,
            "observations": [], "refusals": [], "inputHashes": {}}


def finish(report):
    report["contextSha256"] = sha256(encoded(report))
    return report


def build_context(metadata_bytes, sample_bytes, receipt_bytes, pins, *, boundary_key="SCOTEX", profile=PROFILE):
    """Return deterministic research scalars or a refusal; never enable a source.

    Expected hashes are supplied by an independently reviewed pin manifest.
    Integrity/structure does not authenticate a reviewer or grant dataset rights.
    """
    report = base_report(profile)
    try:
        require(isinstance(profile, str), "UNSUPPORTED_SOURCE_PROFILE")
        require(profile == PROFILE, UNSUPPORTED.get(profile, "UNSUPPORTED_SOURCE_PROFILE"))
        require(isinstance(boundary_key, str) and re.fullmatch(r"[A-Z0-9+_-]{1,64}", boundary_key), "EXACT_BOUNDARY_KEY_REQUIRED")
        report["inputHashes"] = verify_inputs(metadata_bytes, sample_bytes, receipt_bytes, pins)
        metadata, sample, receipt = (parse_json(raw) for raw in (metadata_bytes, sample_bytes, receipt_bytes))
        package, resource, rows, resource_index = source_contract(metadata, sample, receipt, pins, boundary_key)
        report.update(
            source={"publisher": "NESO", "dataset": DATASET, "datasetId": DATASET_ID, "resourceId": RESOURCE_ID,
                    "metadataUrl": METADATA_URL, "sampleUrl": receipt["url"],
                    "metadataRetrievedAt": pins["metadata"]["retrievedAt"],
                    "sampleRetrievedAt": receipt["retrievedAt"], "observationUtc": utc_receipt(receipt["retrievedAt"]),
                    "resourceLastModified": raw_metadata_clock(resource.get("last_modified")),
                    "packageLastModified": raw_metadata_clock(package.get("metadata_modified")),
                    "rawCsv": {"url": resource["path"], "sha256": None, "state": "NOT_FETCHED_NOT_HASHED"}},
            rights={"state": "DATASET_LICENCE_DECLARED_REVIEW_REQUIRED", "declaredLicenceUrl": LICENCE,
                    "attribution": ATTRIBUTION, "productionApproved": False},
            quantity={"kind": "DAY_AHEAD_MODELLED_BOUNDARY_FORECAST", "scope": "NAMED_CONSTRAINT_GROUP",
                      "unit": "MW", "liveMeasurement": False, "subsequentChangesReflected": False},
            methodology={"breakDate": "2024-04-22", "breakPrecision": "DAY",
                         "meaning": "Expected constraint mitigation/optimisation actions are no longer considered from the stated method change.",
                         "sourceLocator": f"/result/resources/{resource_index}/description", "unqualifiedCrossBreakComparison": "WITHHELD"},
            polarityReview={"state": "WITHHELD", "reason": "NO_INDEPENDENTLY_REVIEWED_POSITIVE_DIRECTION_MAPPING"},
            coverage={"exactBoundaryKey": boundary_key, "aliasApplied": False, "sampledRows": len(rows),
                      "exhaustive": False, "totalDatasetRows": None, "b6Crosswalk": "NOT_ACCEPTED",
                      "groupEnumeration": "NOT_ASSESSED_BY_THIS_BOUNDED_SAMPLE"})
        partial = False
        for i, row in enumerate(rows):
            forecast, limit = megawatts(row.get("Flow_MW")), megawatts(row.get("Limit_MW"), limit=True)
            clock = local_clock(row.get("Date_ Time GMT_BST"))
            partial |= forecast["state"] != "REPORTED_SCALAR" or limit["state"] != "REPORTED_SCALAR" or clock["civilLabel"] is None
            report["observations"].append({"sourceRow": f"{pins['sample']['sha256']}:row:{i}", "sourceLocator": f"/result/records/{i}",
                                           "boundaryKey": boundary_key, "targetClock": clock,
                                           "reportedForecastMw": forecast, "reportedLimitMw": limit})
        report["status"] = "EMPTY_BOUNDARY_SAMPLE" if not rows else "PARTIAL_SCALAR_RESEARCH_CONTEXT" if partial else "SCALAR_RESEARCH_CONTEXT"
        if not rows:
            report["refusals"].append("NO_ROWS_IN_BOUNDED_RESPONSE_NOT_EXHAUSTIVE_ABSENCE")
    except Refusal as error:
        report["refusals"].append(str(error))
    return finish(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline scalar research context. No fetching or product writes.")
    for name in ("metadata", "sample", "receipt", "pins"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--boundary", default="SCOTEX")
    parser.add_argument("--profile", default=PROFILE)
    args = parser.parse_args(argv)
    try:
        def read(p):
            with p.open("rb") as stream:
                return stream.read(MAX_BYTES + 1)
        pins = parse_json(read(args.pins))
        report = build_context(read(args.metadata), read(args.sample), read(args.receipt), pins,
                               boundary_key=args.boundary, profile=args.profile)
    except (OSError, Refusal):
        report = base_report(args.profile)
        report["refusals"] = ["LOCAL_INPUT_UNAVAILABLE_OR_INVALID"]
        finish(report)
    sys.stdout.buffer.write(encoded(report))
    return 0 if report["status"] in ("SCALAR_RESEARCH_CONTEXT", "PARTIAL_SCALAR_RESEARCH_CONTEXT") else 2


if __name__ == "__main__":
    raise SystemExit(main())
