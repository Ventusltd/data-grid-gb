"""DuckDB/Parquet prototype preserving the exact frozen canonical records.

Source blobs are read from a caller-provided local Git repository at a pinned
commit. Only a new caller-owned output directory is written. No network, source
refresh, scheduled writes, browser engine or live-pointer modification occurs.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import time

DATES = {"planning_application_submitted", "repd_record_updated", "operational", "planning_application_withdrawn",
         "planning_permission_granted", "planning_permission_refused", "planning_permission_expired", "under_construction"}
TECH = {"solar", "bess", "wind_onshore", "wind_offshore"}
HERE = Path(__file__).resolve().parent


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(root, commit, path):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("source commit must be an immutable 40-character SHA")
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts or "\\" in path or not path or ":" in path:
        raise ValueError("unsafe source path")
    return subprocess.check_output(["git", "-C", str(root), "show", commit + ":" + path], stderr=subprocess.PIPE)


def load_source(source_root, pin):
    if pin.get("schema") != "data-grid-gb.repd-columnar-input.v1":
        raise ValueError("invalid input pin schema")
    files = pin.get("files")
    if not isinstance(files, list) or not files or len({f["path"] for f in files}) != len(files):
        raise ValueError("missing or duplicate source pins")
    blobs = {}
    for f in files:
        raw = git_blob(source_root, pin["source_commit"], f["path"])
        if len(raw) != f["bytes"] or sha(raw) != f["sha256"]:
            raise ValueError("source byte/hash pin mismatch: " + f["path"])
        blobs[f["path"]] = raw
    base = pin["edition_path"].rstrip("/") + "/"
    manifest = json.loads(blobs[base + "data/v9.1/build_manifest.json"])
    raw_expected = json.loads(blobs[pin["raw_descriptor_path"]])["expected"]
    if any(raw_expected[k] != v for k, v in pin["raw_dataset_hashes"].items()):
        raise ValueError("raw dataset hashes disagree with pinned source descriptor")
    if manifest["project_count"] != pin["expected_rows"] or manifest["solar_count"] != pin["expected_solar"]:
        raise ValueError("wrong canonical population or scope")
    if manifest["source_record_count"] != pin["original_source_population"]:
        raise ValueError("original source population is a different scope")
    rows = []
    for part in manifest["project_partitions"]:
        raw = blobs[base + part["path"]]
        if sha(raw) != part["sha256"]:
            raise ValueError("partition disagrees with source build manifest")
        document = json.loads(raw)
        if document["record_count"] != part["record_count"] or len(document["projects"]) != part["record_count"]:
            raise ValueError("partition count mismatch")
        rows.extend(document["projects"])
    solar = json.loads(blobs[base + "solar1000/manifest.json"])
    ids = solar["ids"]
    if len(ids) != 1000 or len(set(ids)) != 1000 or ids != sorted(ids, key=int):
        raise ValueError("Solar-1000 ordered selection changed")
    if solar["record_count"] != pin["expected_rows"] or solar["solar_count"] != pin["expected_solar"]:
        raise ValueError("Solar-1000 source scope mismatch")
    if sha(blobs[base + "solar1000/manifest.json"]) != pin["solar1000_manifest_sha256"]:
        raise ValueError("Solar-1000 seed manifest changed")
    if len(rows) != pin["expected_rows"] or sum(r["technology"] == "solar" for r in rows) != pin["expected_solar"]:
        raise ValueError("actual rows disagree with pinned scope")
    if len({r["repd_ref"] for r in rows}) != len(rows) or len({r["gg_project_id"] for r in rows}) != len(rows):
        raise ValueError("duplicate canonical identity")
    by_ref = {r["repd_ref"]: r for r in rows}
    if any(i not in by_ref or by_ref[i]["technology"] != "solar" for i in ids):
        raise ValueError("Solar-1000 identity missing from canonical source")
    for r in rows:
        if not {"repd_ref", "gg_project_id", "technology", "capacity_mw", "capacity_known", *DATES} <= r.keys():
            raise ValueError("missing required canonical field")
        if not isinstance(r["repd_ref"], str) or r["gg_project_id"] != "GG2050-REPD-" + r["repd_ref"]:
            raise ValueError("canonical ID changed")
        cap = r["capacity_mw"]
        if cap is not None and (type(cap) not in (int, float) or not math.isfinite(cap)):
            raise ValueError("invalid capacity")
        if r.get("capacity_known") is False and cap is not None:
            raise ValueError("unknown capacity cannot become a numeric value")
        for f in DATES:
            if r.get(f) is not None and (not isinstance(r[f], str) or dt.date.fromisoformat(r[f]).isoformat() != r[f]):
                raise ValueError("date must remain a source calendar day or null")
    return rows, solar, blobs, manifest


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def schema_for(rows):
    keys = sorted(rows[0])
    if any(sorted(r) != keys for r in rows):
        raise ValueError("heterogeneous record field sets")
    if set(keys) & {"source_ordinal", "solar1000_ordinal", "record_json"}:
        raise ValueError("reserved column collision")
    result = []
    for key in keys:
        types = {type(r[key]) for r in rows if r[key] is not None}
        if key in DATES:
            typ = "DATE"
        elif not types or types <= {str}:
            typ = "VARCHAR"
        elif types <= {bool}:
            typ = "BOOLEAN"
        elif types <= {int}:
            typ = "BIGINT"
        elif types <= {int, float}:
            typ = "DOUBLE"
        elif types <= {list, dict}:
            typ = "JSON"
        else:
            raise ValueError("unhandled field types: " + key)
        result.append((key, typ))
    return result


def filters(technology=None, submitted_year=None, updated_year=None, operational_year=None):
    clauses, values = [], []
    if technology is not None:
        if technology not in TECH:
            raise ValueError("unsupported technology")
        clauses.append("technology = ?")
        values.append(technology)
    for field, year in [("planning_application_submitted", submitted_year), ("repd_record_updated", updated_year), ("operational", operational_year)]:
        if year is None:
            continue
        if year == "missing":
            clauses.append(_q(field) + " IS NULL")
        elif isinstance(year, str) and re.fullmatch(r"[0-9]{4}", year) and 1 <= int(year) <= 9999:
            clauses.append("EXTRACT(YEAR FROM " + _q(field) + ") = ?")
            values.append(int(year))
        else:
            raise ValueError("year must be YYYY or missing")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), values


def projection(parquet, limit=100, **query):
    import duckdb
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("projection limit must be 1..1000")
    where, params = filters(**query)
    with duckdb.connect() as db:
        total = db.execute("SELECT count(*) FROM read_parquet(?)" + where, [str(parquet), *params]).fetchone()[0]
        records = db.execute("SELECT record_json FROM read_parquet(?)" + where + " ORDER BY source_ordinal LIMIT ?", [str(parquet), *params, limit]).fetchall()
    return {"schema": "data-grid-gb.repd-filtered-view.v1", "matching_count": total,
            "returned_count": len(records), "limit": limit, "truncated": total > len(records),
            "rows": [json.loads(r[0]) for r in records]}


def _matches(row, query):
    if query.get("technology") is not None and row["technology"] != query["technology"]:
        return False
    for f, param in [("planning_application_submitted", "submitted_year"), ("repd_record_updated", "updated_year"), ("operational", "operational_year")]:
        y = query.get(param)
        if y is not None and (row[f] is not None if y == "missing" else not row[f] or row[f][:4] != y):
            return False
    return True


def measure(json_path, parquet, limit, query):
    samples = []
    reference = None
    for i in range(3):
        for mode in (["json", "parquet"] if i % 2 == 0 else ["parquet", "json"]):
            start, cpu = time.perf_counter(), time.process_time()
            if mode == "json":
                rows = json.loads(json_path.read_bytes())
                selected = [r for r in rows if _matches(r, query)]
                value = {"matching_count": len(selected), "rows": selected[:limit]}
            else:
                value = projection(parquet, limit=limit, **query)
            samples.append({"format": mode, "iteration": i, "elapsed_ms": (time.perf_counter() - start) * 1000,
                            "cpu_ms": (time.process_time() - cpu) * 1000, "matching_count": value["matching_count"],
                            "returned_count": len(value["rows"])})
            selection = {"matching_count": value["matching_count"], "rows": value["rows"]}
            if reference is not None and selection != reference:
                raise ValueError("JSON and Parquet measured selections differ")
            reference = selection
    return {"scope": "One workload; fresh DuckDB connection per Parquet sample; file read/parse/filter/decode included; connection startup included for Parquet",
            "cache": "OS file cache uncontrolled; not a cold-cache benchmark; no browser transfer/WASM or GPU speedup claim",
            "query": query, "limit": limit, "same_selection": "PASS", "alternating_order": True, "samples": samples}


def export_product(source_root, output, pin, limit=100, **query):
    import duckdb
    if duckdb.__version__ != "1.3.2":
        raise ValueError("prototype requires pinned duckdb==1.3.2")
    # Validate inputs/query and output boundaries before creating any output.
    filters(**query)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("projection limit must be 1..1000")
    if ".." in Path(output).parts:
        raise ValueError("output path traversal is not allowed")
    source_root, output = Path(source_root).resolve(), Path(output).resolve()
    own_root = HERE.parent.resolve()
    if output == source_root or source_root in output.parents or output == own_root or own_root in output.parents:
        raise ValueError("output must be caller-owned, outside source and data repositories")
    if output.exists():
        raise ValueError("refusing to overwrite an existing output path")
    ancestor = output.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    inside = subprocess.run(["git", "-C", str(ancestor), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if inside.returncode == 0 and inside.stdout.strip() == "true":
        raise ValueError("output must be outside every Git source/product worktree")
    rows, solar, blobs, source_manifest = load_source(source_root, pin)
    columns = schema_for(rows)
    ranks = {ref: i for i, ref in enumerate(solar["ids"])}
    tuples = []
    for i, r in enumerate(rows):
        values = [json.dumps(r[k], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) if typ == "JSON" and r[k] is not None else r[k] for k, typ in columns]
        tuples.append((i, ranks.get(r["repd_ref"]), *values, encoded(r).decode()))
    output.mkdir(parents=True)
    parquet = output / "repd.parquet"
    load_path = output / "source-load.json"
    load_path.write_bytes(encoded([{**r, "source_ordinal": i, "solar1000_ordinal": ranks.get(r["repd_ref"]),
                                    "record_json": encoded(r).decode()} for i, r in enumerate(rows)]))
    with duckdb.connect() as db:
        definition = "source_ordinal BIGINT PRIMARY KEY, solar1000_ordinal BIGINT," + ",".join(_q(k) + " " + t for k, t in columns) + ",record_json VARCHAR"
        db.execute("CREATE TABLE repd(" + definition + ")")
        load_columns = {"source_ordinal": "BIGINT", "solar1000_ordinal": "BIGINT", **dict(columns), "record_json": "VARCHAR"}
        db.read_json(str(load_path), columns=load_columns).insert_into("repd")
        load_path.unlink()
        db.sql("SELECT * FROM repd ORDER BY source_ordinal").write_parquet(str(parquet), compression="zstd")
        readback = db.execute("SELECT * FROM read_parquet(?,hive_partitioning=false) ORDER BY source_ordinal", [str(parquet)]).fetchall()
        if len(readback) != len(tuples):
            raise ValueError("Parquet readback row count differs")
        for expected, actual, row in zip(tuples, readback, rows):
            if actual[0:2] != expected[0:2] or json.loads(actual[-1]) != row:
                raise ValueError("record identity/order/complete-field JSON roundtrip differs")
            for j, (key, typ) in enumerate(columns, 2):
                value = actual[j]
                if typ == "DATE" and value is not None:
                    value = value.isoformat()
                if typ == "JSON" and value is not None:
                    value = json.loads(value)
                if value != row[key]:
                    raise ValueError("typed field roundtrip differs: " + key)
        if db.execute("SELECT DISTINCT compression FROM parquet_metadata(?)", [str(parquet)]).fetchall() != [("ZSTD",)]:
            raise ValueError("Parquet compression differs")
        pilot = db.execute("SELECT repd_ref FROM read_parquet(?) WHERE solar1000_ordinal IS NOT NULL ORDER BY solar1000_ordinal", [str(parquet)]).fetchall()
        if [r[0] for r in pilot] != solar["ids"]:
            raise ValueError("Solar-1000 order differs")
    baseline = output / "benchmark-baseline.json"
    baseline.write_bytes(encoded(rows))
    filtered = projection(parquet, limit=limit, **query)
    (output / "filtered.json").write_bytes(encoded(filtered))
    solar_raw = blobs[pin["edition_path"] + "/solar1000/manifest.json"]
    (output / "solar1000-manifest.json").write_bytes(solar_raw)
    measurements = measure(baseline, parquet, limit, query)
    (output / "measurements.json").write_bytes(encoded(measurements))
    outputs = [{"path": name, "bytes": (output / name).stat().st_size, "sha256": sha((output / name).read_bytes())}
               for name in ["repd.parquet", "filtered.json", "solar1000-manifest.json", "benchmark-baseline.json", "measurements.json"]]
    source_package_sha = sha(encoded(pin["files"]))
    manifest = {"schema": "data-grid-gb.repd-columnar-export.v1", "engine": "duckdb-" + duckdb.__version__,
        "source_commit": pin["source_commit"], "edition_path": pin["edition_path"], "source_package_sha256": source_package_sha,
        "raw_dataset_hashes": pin["raw_dataset_hashes"], "source_identity_sha256": source_manifest["source_identity_sha256"],
        "raw_hash_evidence": "Reference hashes checked against pinned product release descriptor; raw CSV/workbook not fetched or rehashed by this export",
        "rows": len(rows), "solar_rows": sum(r["technology"] == "solar" for r in rows), "columns": columns,
        "full_row_and_typed_field_readback": "PASS", "solar1000_order_readback": "PASS", "solar1000_manifest_sha256": sha(solar_raw),
        "first_seen_last_seen": "Preserved if present; absent in this frozen source and never inferred from REPD update, submission or export time",
        "date_precision": "Calendar day (DATE), null preserved; no time zone or instant invented",
        "capacity_policy": "Unknown remains null; no default zero", "outputs": outputs,
        "output_package_sha256": sha(encoded({"source_package_sha256": source_package_sha, "outputs": outputs})),
        "browser_delivery": "Prototype filtered.json only; no current JSON switch or DuckDB-WASM/GPU dependency",
        "benchmark_json": "benchmark-baseline.json is offline comparison only, not a browser payload",
        "size_context": {"parquet_codec": "ZSTD", "json_file_codec": "uncompressed",
            "json_gzip_bytes_reference_only": len(gzip.compress(baseline.read_bytes(), mtime=0)),
            "note": "Gzip size is an in-memory reference, not a measured browser transfer or gzip query benchmark"},
        "measurements": "measurements.json reports all alternating samples, OS cache uncontrolled"}
    (output / "manifest.json").write_bytes(encoded(manifest))
    return manifest


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", required=True, type=Path)
    p.add_argument("--pin", type=Path, default=HERE / "pins/v939-source.json")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--technology", choices=sorted(TECH))
    p.add_argument("--submitted-year")
    p.add_argument("--updated-year")
    p.add_argument("--operational-year")
    args = p.parse_args(argv)
    manifest = export_product(args.source_root, args.out, json.loads(args.pin.read_bytes()), limit=args.limit,
                              technology=args.technology, submitted_year=args.submitted_year,
                              updated_year=args.updated_year, operational_year=args.operational_year)
    print(json.dumps({"rows": manifest["rows"], "solar_rows": manifest["solar_rows"], "outputs": manifest["outputs"]}, indent=2))
