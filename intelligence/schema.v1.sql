-- Analytical read model only. Existing source owners and operational stores remain authoritative.
CREATE TABLE intelligence_sources (
    source_id VARCHAR PRIMARY KEY CHECK (length(source_id) BETWEEN 1 AND 160),
    publisher VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL CHECK (starts_with(source_url, 'https://')),
    rights_status VARCHAR NOT NULL CHECK (rights_status IN ('UNKNOWN','ACCEPTED','RESTRICTED','PROHIBITED')),
    rights_receipt_url VARCHAR,
    CHECK (rights_status <> 'ACCEPTED' OR starts_with(coalesce(rights_receipt_url,''), 'https://'))
);

CREATE TABLE intelligence_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    source_id VARCHAR NOT NULL REFERENCES intelligence_sources(source_id),
    retrieved_at TIMESTAMPTZ NOT NULL,
    source_sha256 VARCHAR NOT NULL CHECK (regexp_full_match(source_sha256, '[a-f0-9]{64}')),
    source_record_count UBIGINT NOT NULL,
    parser_version VARCHAR NOT NULL,
    UNIQUE (source_id, source_sha256, parser_version),
    UNIQUE (snapshot_id, source_id)
);

-- Names are not identities. Namespaces and snapshot-specific original IDs remain separate.
CREATE TABLE intelligence_assets (
    namespace VARCHAR NOT NULL CHECK (regexp_full_match(namespace, '[a-z][a-z0-9_-]{0,63}')),
    source_record_id VARCHAR NOT NULL,
    snapshot_id VARCHAR NOT NULL REFERENCES intelligence_snapshots(snapshot_id),
    name VARCHAR,
    operator VARCHAR,
    reported_status VARCHAR,
    longitude DOUBLE,
    latitude DOUBLE,
    PRIMARY KEY (namespace, source_record_id, snapshot_id),
    CHECK ((longitude IS NULL AND latitude IS NULL) OR
      (longitude IS NOT NULL AND latitude IS NOT NULL AND isfinite(longitude) AND isfinite(latitude)
       AND longitude BETWEEN -180 AND 180 AND latitude BETWEEN -90 AND 90))
);

-- One article identity, many observations/associations. No article body or snippet column.
CREATE TABLE intelligence_articles (
    article_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(article_id, '[a-f0-9]{64}')),
    source_id VARCHAR NOT NULL REFERENCES intelligence_sources(source_id),
    original_url VARCHAR NOT NULL CHECK (starts_with(original_url, 'https://')),
    canonical_url VARCHAR NOT NULL UNIQUE CHECK (starts_with(canonical_url, 'https://')),
    title VARCHAR NOT NULL CHECK (length(title) BETWEEN 1 AND 1000),
    first_observed_at TIMESTAMPTZ NOT NULL,
    UNIQUE (article_id, source_id)
);

CREATE TABLE intelligence_article_observations (
    article_id VARCHAR NOT NULL REFERENCES intelligence_articles(article_id),
    snapshot_id VARCHAR NOT NULL REFERENCES intelligence_snapshots(snapshot_id),
    source_id VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    published_text VARCHAR,
    published_at TIMESTAMPTZ,
    date_precision VARCHAR NOT NULL CHECK (date_precision IN ('UNKNOWN','YEAR','MONTH','DAY','INSTANT')),
    PRIMARY KEY (article_id, snapshot_id),
    FOREIGN KEY (article_id, source_id) REFERENCES intelligence_articles(article_id, source_id),
    FOREIGN KEY (snapshot_id, source_id) REFERENCES intelligence_snapshots(snapshot_id, source_id),
    CHECK (published_at IS NULL OR published_at <= observed_at),
    CHECK ((date_precision = 'UNKNOWN' AND published_text IS NULL AND published_at IS NULL) OR
      (date_precision <> 'UNKNOWN' AND published_text IS NOT NULL))
);

-- This research layer cannot grant independent VERIFIED/publication status.
CREATE TABLE intelligence_project_links (
    link_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(link_id, '[a-f0-9]{64}')),
    article_id VARCHAR NOT NULL REFERENCES intelligence_articles(article_id),
    asset_namespace VARCHAR NOT NULL,
    asset_record_id VARCHAR NOT NULL,
    asset_snapshot_id VARCHAR NOT NULL,
    method VARCHAR NOT NULL CHECK (method IN ('EXPLICIT_SOURCE_ID','EXACT_NAME_CANDIDATE','MANUAL_SOURCE_RECEIPT','SPATIAL_SCREENING')),
    review_state VARCHAR NOT NULL DEFAULT 'WITHHELD' CHECK (review_state IN ('CANDIDATE','REJECTED','WITHHELD')),
    evidence_url VARCHAR NOT NULL CHECK (starts_with(evidence_url, 'https://')),
    evidence_locator VARCHAR,
    observed_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (asset_namespace,asset_record_id,asset_snapshot_id)
      REFERENCES intelligence_assets(namespace,source_record_id,snapshot_id)
);

-- Typed quantities retain their scope/unit and are never silently allocated to a facility.
CREATE TABLE intelligence_reported_metrics (
    metric_id VARCHAR PRIMARY KEY,
    snapshot_id VARCHAR NOT NULL REFERENCES intelligence_snapshots(snapshot_id),
    subject_namespace VARCHAR NOT NULL,
    subject_record_id VARCHAR NOT NULL,
    subject_snapshot_id VARCHAR NOT NULL,
    metric_kind VARCHAR NOT NULL,
    reported_value DECIMAL(38,6),
    unit VARCHAR,
    basis VARCHAR NOT NULL,
    source_locator VARCHAR NOT NULL,
    FOREIGN KEY (subject_namespace,subject_record_id,subject_snapshot_id)
      REFERENCES intelligence_assets(namespace,source_record_id,snapshot_id),
    CHECK ((reported_value IS NULL AND unit IS NULL) OR
      (reported_value IS NOT NULL AND unit IS NOT NULL AND length(unit) > 0)),
    CHECK (metric_kind NOT IN ('available_capacity_mw','confirmed_connection','behind_meter_eligible'))
);

-- Manifest values authenticate artifacts only when separately checked against actual bytes.
CREATE TABLE intelligence_parquet_parts (
    snapshot_id VARCHAR NOT NULL REFERENCES intelligence_snapshots(snapshot_id),
    dataset_id VARCHAR NOT NULL,
    part_path VARCHAR NOT NULL CHECK (starts_with(part_path,'data/') AND NOT contains(part_path,'..') AND NOT contains(part_path,chr(92))),
    schema_id VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL CHECK (regexp_full_match(sha256,'[a-f0-9]{64}')),
    byte_count UBIGINT NOT NULL CHECK (byte_count > 0),
    row_count UBIGINT NOT NULL,
    PRIMARY KEY (snapshot_id,dataset_id,part_path)
);
