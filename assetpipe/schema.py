"""Record schemas for the two domains, and validators that return a list of problems.

The rules that matter most are written as code, not prose, so they cannot drift:

- IT load MW and grid connection MW are two separate fields and are never merged.
  A record that carries a bare ``mw``, ``capacity_mw`` or ``power_mw`` on a data
  centre is rejected: nobody downstream could tell which of the two it meant.
- Unknown is ``null``, never ``0``. A zero MW, a zero year or an empty string
  standing in for "not known" is rejected.
- Every record names its source, the source's licence and licence URL, and an
  evidence URL a person can open.
- A pipeline status is a stage in a public process, not a commitment; there is no
  "committed" or "confirmed" status to choose.
- Location precision is stated. A point placed at a postcode or a town centre
  says so, and a record with no location has ``precision: "unknown"`` and no point.
"""
from __future__ import annotations

import math
import re

SCHEMA_DATACENTRE = "gb-datacentre-pipeline-record.v1"
SCHEMA_CONVENTIONAL = "gb-conventional-generation-record.v1"
SCHEMA_COLLECTION = "gb-asset-collection.v1"

DATACENTRE_STATUSES = (
    "operational",
    "under_construction",
    "consented",
    "in_planning",
    "connection_request",
    "announced",
    "unknown",
)
CONVENTIONAL_STATUSES = (
    "operational",
    "mothballed",
    "closed",
    "under_construction",
    "consented",
    "in_planning",
    "unknown",
)
CONVENTIONAL_FUELS = (
    "gas",
    "coal",
    "oil",
    "nuclear",
    "biomass",
    "efw",        # energy from waste
    "diesel",
    "gas_oil_dual",
    "other_thermal",
)
LOCATION_PRECISIONS = (
    "exact",       # a mapped building or site outline / its centroid
    "site",        # a published site point (e.g. a register's grid reference)
    "address",     # geocoded street address
    "postcode",    # postcode centroid
    "locality",    # town or village centre
    "region",      # local authority / region only
    "unknown",     # no location; geometry is null
)
OPEN_VALUES = ("yes", "no", "unverified")

# A rectangle round Great Britain, Northern Ireland and the islands, not the border.
GB_BOUNDS = (-11.0, 49.0, 2.5, 61.5)  # west, south, east, north

FORBIDDEN_DATACENTRE_MW_KEYS = ("mw", "capacity_mw", "power_mw", "load_mw", "demand_mw")
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")  # year, year-month or full date
URL_RE = re.compile(r"^https://\S+$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$")

DATACENTRE_DATE_KEYS = ("announced", "application", "consented", "connection_offer",
                        "energisation", "operational")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _check_mw(rec: dict, key: str, problems: list) -> None:
    if key not in rec:
        problems.append(f"{key} missing (use null when unknown)")
        return
    value = rec[key]
    if value is None:
        return
    if not _is_number(value):
        problems.append(f"{key} is not a number or null: {value!r}")
    elif value <= 0:
        problems.append(f"{key} is {value}: unknown is null, not zero or negative")


def _check_common(rec: dict, problems: list) -> None:
    if not isinstance(rec.get("id"), str) or not ID_RE.match(rec["id"]):
        problems.append(f"id missing or not a lower-case slug: {rec.get('id')!r}")
    for key in ("name", "operator"):
        if key not in rec:
            problems.append(f"{key} missing (use null when unknown)")
        elif rec[key] is not None and (not isinstance(rec[key], str) or not rec[key].strip()):
            problems.append(f"{key} is an empty string; unknown is null")
    source = rec.get("source")
    if not isinstance(source, dict):
        problems.append("source object missing")
    else:
        for key in ("id", "licence", "licence_url", "evidence_url"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                problems.append(f"source.{key} missing")
        for key in ("licence_url", "evidence_url"):
            if isinstance(source.get(key), str) and not URL_RE.match(source[key]):
                problems.append(f"source.{key} is not an https URL: {source[key]!r}")
    location = rec.get("location")
    if not isinstance(location, dict):
        problems.append("location object missing")
        return
    precision = location.get("precision")
    if precision not in LOCATION_PRECISIONS:
        problems.append(f"location.precision {precision!r} not in {LOCATION_PRECISIONS}")
    lon, lat = location.get("lon"), location.get("lat")
    if precision == "unknown":
        if lon is not None or lat is not None:
            problems.append("location.precision is unknown but a point is given")
    else:
        if not (_is_number(lon) and _is_number(lat)):
            problems.append("location.lon/lat missing for a located record")
        else:
            w, s, e, n = GB_BOUNDS
            if not (w <= lon <= e and s <= lat <= n):
                problems.append(f"location ({lon}, {lat}) is outside the GB/NI rectangle")


def validate_datacentre(rec) -> list:
    problems: list = []
    if not isinstance(rec, dict):
        return ["record is not an object"]
    _check_common(rec, problems)
    for key in FORBIDDEN_DATACENTRE_MW_KEYS:
        if key in rec:
            problems.append(f"{key} is ambiguous: use it_load_mw or grid_connection_mw, never both in one field")
    if rec.get("status") not in DATACENTRE_STATUSES:
        problems.append(f"status {rec.get('status')!r} not in {DATACENTRE_STATUSES}")
    _check_mw(rec, "it_load_mw", problems)
    _check_mw(rec, "grid_connection_mw", problems)
    dates = rec.get("dates")
    if not isinstance(dates, dict):
        problems.append("dates object missing (use {} when none are known)")
    else:
        for key, value in dates.items():
            if key not in DATACENTRE_DATE_KEYS:
                problems.append(f"dates.{key} is not one of {DATACENTRE_DATE_KEYS}")
            elif value is not None and (not isinstance(value, str) or not DATE_RE.match(value)):
                problems.append(f"dates.{key} is not YYYY[-MM[-DD]] or null: {value!r}")
    return problems


def validate_conventional(rec) -> list:
    problems: list = []
    if not isinstance(rec, dict):
        return ["record is not an object"]
    _check_common(rec, problems)
    if rec.get("fuel") not in CONVENTIONAL_FUELS:
        problems.append(f"fuel {rec.get('fuel')!r} not in {CONVENTIONAL_FUELS}")
    if rec.get("status") not in CONVENTIONAL_STATUSES:
        problems.append(f"status {rec.get('status')!r} not in {CONVENTIONAL_STATUSES}")
    _check_mw(rec, "capacity_mw", problems)
    if rec.get("capacity_mw") is not None and not (isinstance(rec.get("capacity_basis"), str)
                                                   and rec["capacity_basis"].strip()):
        problems.append("capacity_basis missing: say what the MW is (e.g. 'DUKES installed capacity')")
    year = rec.get("commissioned_year")
    if year is not None and not (isinstance(year, int) and not isinstance(year, bool) and 1880 <= year <= 2100):
        problems.append(f"commissioned_year is not a plausible year or null: {year!r}")
    return problems


VALIDATORS = {
    "datacentres": (SCHEMA_DATACENTRE, validate_datacentre),
    "conventional": (SCHEMA_CONVENTIONAL, validate_conventional),
}


def record_to_feature(rec: dict) -> dict:
    """A record as a GeoJSON Feature; the location block stays in properties too."""
    location = rec.get("location") or {}
    geometry = None
    if location.get("precision") != "unknown" and _is_number(location.get("lon")) and _is_number(location.get("lat")):
        geometry = {"type": "Point", "coordinates": [round(location["lon"], 6), round(location["lat"], 6)]}
    return {"type": "Feature", "id": rec["id"], "geometry": geometry, "properties": rec}
