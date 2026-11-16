# Shared project-intelligence data contract

The owner selected this existing data repository for future shared Grid, REPD,
data-centre and news intelligence. This additive analytical schema establishes
namespaced relationships without migrating an existing source, collector or
operational database. Current ETYS, datacentres and conventional products and
their stricter source-specific rules remain authoritative.

`schema.v1.sql` is executable on DuckDB 1.3.2. It defines sources and immutable
snapshot identities; article identities and dated observations; namespaced asset
records; withheld/candidate research links; typed reported metrics; and Parquet
partition manifests. One article is catalogued once and can have many observations
or associations. Source URLs, publisher, source-byte digest, parser version,
original IDs and date precision stay attributable. Article bodies/snippets are
not part of this contract. The URL and rights fields are metadata, not a licence
grant or an ingestion permission.

## Existing-owner boundaries

- GridAtlas already supplies mapped substations and offtakers. Preserve those
  source products, licences and snapshot identity; do not replace them with a
  second collector or force name/proximity matches into an identity crosswalk.
- `globalgrid2050/automation/news/` remains the existing News Engine owner.
  Its SQLite operational state and existing evidence/Parquet exporters are not
  changed. A later reviewed importer may build this analytical read model from
  accepted exports with complete source/workflow/import/environment closure.
- Full REPD source coverage (14,967 rows at the observed edition) differs from
  Pipeline's qualified 7,775-row browser scope. Store/import exact snapshots and
  namespaces; do not treat a displayed subset as proof of global name uniqueness.
- Intentional data-centre/offtaker overlap is retained in separate namespaces.
  DNO, ETYS and project crosswalks need explicit source-backed identity evidence.
  A nearby substation is not a declared connection, capacity or behind-meter case.
- Restricted/unknown-rights metadata can be catalogued for review, but published
  products still require this repository's existing fully open source policy.
  A caller-supplied receipt URL is not proof of source rights. This schema cannot
  independently approve evidence or emit VERIFIED research links.

## Scale and append-only publication

The intended catalogue can grow to hundreds of thousands of articles. Store
immutable, deterministically sorted Parquet partitions by dataset/source/time,
with schema, row/byte counts and SHA-256 manifests. DuckDB performs joins and
aggregation over those partitions. Browser apps receive bounded projections,
not the entire archive. Large frequently regenerated artifacts should use the
existing data-owner artifact/storage policy rather than repeated binary Git
copies; this change chooses no new storage service.

Append-only is the required writer protocol, not a privilege magically enforced
by SQL DDL: a future importer writes new snapshot/observation rows, validates all
foreign keys and actual artifact bytes, and atomically advances a separately
accepted manifest. Corrections add observations rather than rewriting accepted
history. Existing source publication and independent review gates are unchanged.
Typed timestamps compare actual instants, including fractional seconds.

No scheduled task, credentials, source enablement, real data import or live
consumer is installed by these files. This prepares the selected destination;
it does not delay the authorized Pipeline V9.28 release.

## Verification

```sh
python -m pip install -r intelligence/requirements.txt
python -B -m unittest discover -s intelligence/tests -p 'test_*.py' -v
```

The tests use explicitly synthetic reserved-domain fixtures and an in-memory
database. They challenge namespace collisions, orphan identities, unknown
locations/dates, duplicate articles, fractional time ordering, unsupported
verification, missing rights receipts, untyped quantities and artifact paths.
This CPU metadata/relational work reuses the existing DuckDB approach; no GPU
performance benefit is claimed or needed for this bounded contract check.
One scale control writes 100,000 synthetic articles to ZSTD Parquet and compares
every row in both directions after readback. It proves this serialization case,
not live corpus coverage, model learning, a production importer or a benchmark.
