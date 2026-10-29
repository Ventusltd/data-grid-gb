#!/usr/bin/env python3
"""Standard-library validator for dc_pipeline.schema.json (GridAtlas #73 step 3).

Implements the JSON Schema subset the schema uses (type, enum, const, required,
properties, additionalProperties, items, minItems, minLength, pattern, minimum,
maximum, local $ref) plus semantic rules JSON Schema cannot express cleanly:

  * an MW value that is set must name a source_id, and that id must be in sources[];
  * every *source_id referenced anywhere must be in sources[];
  * lat and lon are both null or both set; a set point cannot have precision 'unknown';
  * id must equal dcuk-<sha256(site_key)[:16]> when site_key is present;
  * conflicts[].values[].source_id must be in sources[].

If the optional `jsonschema` package is installed, validate(..., cross_check=True)
also runs it and reports any disagreement (tests run with and without it).

Usage: python3 dcschema.py FILE.json|FILE.geojson   (exit 1 on any error)
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "dc_pipeline.schema.json")

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def load_schema(path=SCHEMA_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve(root, ref):
    if not ref.startswith("#/"):
        raise ValueError("only local $ref supported: %s" % ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _check(node, value, root, path, errors):
    if "$ref" in node:
        _check(_resolve(root, node["$ref"]), value, root, path, errors)
        node = {k: v for k, v in node.items() if k != "$ref"}
    t = node.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            errors.append("%s: expected %s, got %s" % (path, "/".join(types), type(value).__name__))
            return
    if "enum" in node and value not in node["enum"]:
        errors.append("%s: %r not in enum %s" % (path, value, node["enum"]))
    if "const" in node and value != node["const"]:
        errors.append("%s: %r != const %r" % (path, value, node["const"]))
    if isinstance(value, str):
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append("%s: shorter than %d" % (path, node["minLength"]))
        if "pattern" in node and not re.search(node["pattern"], value):
            errors.append("%s: %r does not match %s" % (path, value, node["pattern"]))
    if _TYPES["number"](value):
        if "minimum" in node and value < node["minimum"]:
            errors.append("%s: %r < minimum %r" % (path, value, node["minimum"]))
        if "maximum" in node and value > node["maximum"]:
            errors.append("%s: %r > maximum %r" % (path, value, node["maximum"]))
    if isinstance(value, dict):
        props = node.get("properties", {})
        for k in node.get("required", []):
            if k not in value:
                errors.append("%s: missing required '%s'" % (path, k))
        for k, v in value.items():
            if k in props:
                _check(props[k], v, root, "%s.%s" % (path, k), errors)
            elif node.get("additionalProperties") is False:
                errors.append("%s: unexpected property '%s'" % (path, k))
    if isinstance(value, list):
        if "minItems" in node and len(value) < node["minItems"]:
            errors.append("%s: fewer than %d items" % (path, node["minItems"]))
        if "items" in node:
            for i, v in enumerate(value):
                _check(node["items"], v, root, "%s[%d]" % (path, i), errors)


def site_id(site_key):
    return "dcuk-" + hashlib.sha256(site_key.encode("utf-8")).hexdigest()[:16]


def semantic_errors(rec, path="$"):
    errs = []
    if not isinstance(rec, dict):
        return errs
    src_ids = {s.get("id") for s in rec.get("sources", []) if isinstance(s, dict)}
    for f in ("it_load_mw", "grid_connection_mw", "unclassified_mw"):
        m = rec.get(f)
        if isinstance(m, dict):
            if m.get("value") is not None and not m.get("source_id"):
                errs.append("%s.%s: value set without source_id" % (path, f))
            if m.get("source_id") and m["source_id"] not in src_ids:
                errs.append("%s.%s: source_id %r not in sources[]" % (path, f, m["source_id"]))
    for f in ("status_source_id",):
        if rec.get(f) and rec[f] not in src_ids:
            errs.append("%s.%s: %r not in sources[]" % (path, f, rec[f]))
    loc = rec.get("location")
    if isinstance(loc, dict):
        if (loc.get("lat") is None) != (loc.get("lon") is None):
            errs.append("%s.location: lat and lon must both be set or both null" % path)
        if loc.get("lat") is not None and loc.get("precision") == "unknown":
            errs.append("%s.location: a point with precision 'unknown'" % path)
        if loc.get("source_id") and loc["source_id"] not in src_ids:
            errs.append("%s.location.source_id: %r not in sources[]" % (path, loc["source_id"]))
    if rec.get("site_key") and rec.get("id") and site_id(rec["site_key"]) != rec["id"]:
        errs.append("%s.id: does not equal hash of site_key" % path)
    for i, c in enumerate(rec.get("conflicts", []) or []):
        for v in (c.get("values") or []) if isinstance(c, dict) else []:
            if isinstance(v, dict) and v.get("source_id") not in src_ids:
                errs.append("%s.conflicts[%d]: source_id %r not in sources[]" % (path, i, v.get("source_id")))
    ids = [s.get("id") for s in rec.get("sources", []) if isinstance(s, dict)]
    if len(ids) != len(set(ids)):
        errs.append("%s.sources: duplicate source ids" % path)
    return errs


def validate(rec, schema=None, cross_check=False, path="$"):
    """Return a list of error strings (empty = valid)."""
    schema = schema or load_schema()
    errors = []
    _check(schema, rec, schema, path, errors)
    errors += semantic_errors(rec, path)
    if cross_check:
        try:
            import jsonschema  # optional
        except ImportError:
            return errors
        lib_ok = jsonschema.Draft202012Validator(schema).is_valid(rec)
        ours_ok = not [e for e in errors if " not in sources[]" not in e and "without source_id" not in e
                       and "lat and lon" not in e and "precision 'unknown'" not in e and "hash of site_key" not in e
                       and "duplicate source ids" not in e]
        if lib_ok != ours_ok:
            errors.append("%s: stdlib validator and jsonschema disagree (lib_ok=%s)" % (path, lib_ok))
    return errors


def feature_to_record(feat):
    """GeoJSON Feature -> record: the record is the properties; geometry must agree with location."""
    return feat.get("properties") or {}


def validate_file(path, cross_check=True):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    schema = load_schema()
    errors = []
    if isinstance(doc, dict) and doc.get("type") == "FeatureCollection":
        for i, feat in enumerate(doc.get("features", [])):
            rec = feature_to_record(feat)
            p = "features[%d]" % i
            errors += validate(rec, schema, cross_check, p)
            loc = rec.get("location") or {}
            g = feat.get("geometry")
            if loc.get("lat") is None and g is not None:
                errors.append("%s: geometry set but location null" % p)
            if loc.get("lat") is not None:
                if not g or g.get("type") != "Point" or g.get("coordinates") != [loc["lon"], loc["lat"]]:
                    errors.append("%s: geometry does not equal location" % p)
            if feat.get("id") != rec.get("id"):
                errors.append("%s: feature id != properties.id" % p)
    elif isinstance(doc, list):
        for i, rec in enumerate(doc):
            errors += validate(rec, schema, cross_check, "[%d]" % i)
    else:
        errors += validate(doc, schema, cross_check)
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    errs = validate_file(sys.argv[1])
    for e in errs[:200]:
        print(e)
    print("%d error(s)" % len(errs))
    sys.exit(1 if errs else 0)
