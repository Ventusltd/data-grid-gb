"""Synthetic relational-boundary tests; no collector, live records or publication."""
import pathlib
import tempfile
import unittest

import duckdb

SCHEMA = pathlib.Path(__file__).parents[1] / 'schema.v1.sql'


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect(':memory:')
        self.db.execute(SCHEMA.read_text(encoding='utf-8'))
        self.db.execute("INSERT INTO intelligence_sources VALUES ('fixture','Fixture publisher','https://fixture.example/source','UNKNOWN',NULL)")
        self.db.execute("INSERT INTO intelligence_snapshots VALUES ('fixture-snapshot','fixture','2026-11-16T20:15:00Z',?,2,'synthetic-v1')", ['a' * 64])

    def tearDown(self):
        self.db.close()

    def article(self):
        self.db.execute("INSERT INTO intelligence_articles VALUES (?,'fixture','https://fixture.example/original','https://fixture.example/canonical','Synthetic article','2026-11-16T20:15:00Z')", ['b' * 64])

    def asset(self, namespace='repd', identifier='1234'):
        self.db.execute("INSERT INTO intelligence_assets VALUES (?,?,'fixture-snapshot','Same source name',NULL,NULL,NULL,NULL)", [namespace, identifier])

    def test_names_are_not_identities_and_domains_remain_distinct(self):
        self.asset('repd', '1234')
        self.asset('gridatlas-substation', '1234')
        self.asset('offtaker', '1234')
        self.assertEqual(self.db.execute('SELECT count(*) FROM intelligence_assets').fetchone()[0], 3)

    def test_unknown_geometry_is_retained_and_half_coordinates_reject(self):
        self.asset()
        self.assertEqual(self.db.execute('SELECT count(*) FROM intelligence_assets WHERE latitude IS NULL').fetchone()[0], 1)
        with self.assertRaises(duckdb.ConstraintException):
            self.db.execute("INSERT INTO intelligence_assets VALUES ('repd','5678','fixture-snapshot',NULL,NULL,NULL,1,NULL)")

    def test_orphan_snapshots_and_duplicate_article_identity_reject(self):
        with self.assertRaises(duckdb.ConstraintException):
            self.db.execute("INSERT INTO intelligence_snapshots VALUES ('orphan','missing','2026-11-16T20:15:00Z',?,0,'fixture')", ['c' * 64])
        self.article()
        with self.assertRaises(duckdb.ConstraintException):
            self.article()

    def test_fractional_timestamp_ordering_is_temporal_not_lexical(self):
        self.article()
        with self.assertRaises(duckdb.ConstraintException):
            self.db.execute("INSERT INTO intelligence_article_observations VALUES (?,'fixture-snapshot','fixture','2026-11-16T20:15:00Z','2026-11-16T20:15:00.100Z','2026-11-16T20:15:00.100Z','INSTANT')", ['b' * 64])
        self.db.execute("INSERT INTO intelligence_article_observations VALUES (?,'fixture-snapshot','fixture','2026-11-16T20:15:00.100Z','2026-11-16T20:15:00Z','2026-11-16T20:15:00Z','INSTANT')", ['b' * 64])

    def test_missing_dates_remain_unknown(self):
        self.article()
        self.db.execute("INSERT INTO intelligence_article_observations VALUES (?,'fixture-snapshot','fixture','2026-11-16T20:15:00Z',NULL,NULL,'UNKNOWN')", ['b' * 64])
        self.assertIsNone(self.db.execute('SELECT published_at FROM intelligence_article_observations').fetchone()[0])

    def test_links_require_exact_asset_identity_and_cannot_assert_verified(self):
        self.article()
        self.asset()
        values = ['c' * 64, 'b' * 64]
        with self.assertRaises(duckdb.ConstraintException):
            self.db.execute("INSERT INTO intelligence_project_links VALUES (?,?,'repd','1234','fixture-snapshot','SPATIAL_SCREENING','VERIFIED','https://fixture.example/evidence',NULL,'2026-11-16T20:15:00Z')", values)
        self.db.execute("INSERT INTO intelligence_project_links VALUES (?,?,'repd','1234','fixture-snapshot','SPATIAL_SCREENING','WITHHELD','https://fixture.example/evidence',NULL,'2026-11-16T20:15:00Z')", values)

    def test_accepted_rights_requires_a_receipt_but_unknown_source_metadata_can_be_catalogued(self):
        with self.assertRaises(duckdb.ConstraintException):
            self.db.execute("INSERT INTO intelligence_sources VALUES ('accepted','Fixture','https://fixture.example/source','ACCEPTED',NULL)")
        self.assertEqual(self.db.execute("SELECT rights_status FROM intelligence_sources WHERE source_id='fixture'").fetchone()[0], 'UNKNOWN')

    def test_metrics_cannot_silently_claim_connection_or_untyped_capacity(self):
        self.asset()
        for kind, value, unit in [('available_capacity_mw', 10, 'MW'), ('published_rating', 10, None)]:
            with self.assertRaises(duckdb.ConstraintException):
                self.db.execute("INSERT INTO intelligence_reported_metrics VALUES ('m','fixture-snapshot','repd','1234','fixture-snapshot',?,?,?,'Published source quantity','Fixture section')", [kind,value,unit])

    def test_artifact_paths_and_hashes_are_bounded_to_declared_data_area(self):
        for path in ['../external.parquet','data/../external.parquet','data\\external.parquet']:
            with self.assertRaises(duckdb.ConstraintException):
                self.db.execute("INSERT INTO intelligence_parquet_parts VALUES ('fixture-snapshot','articles',?,'article-v1',?,10,1)", [path,'d' * 64])
        self.db.execute("INSERT INTO intelligence_parquet_parts VALUES ('fixture-snapshot','articles','data/articles/month=2026-11/part-1.parquet','article-v1',?,10,1)", ['d' * 64])

    def test_observation_cannot_silently_change_article_publisher(self):
        self.article()
        self.db.execute("INSERT INTO intelligence_sources VALUES ('other','Other fixture','https://other.example/source','UNKNOWN',NULL)")
        self.db.execute("INSERT INTO intelligence_snapshots VALUES ('other-snapshot','other','2026-11-16T20:15:00Z',?,1,'fixture')", ['e' * 64])
        with self.assertRaises(duckdb.ConstraintException):
            self.db.execute("INSERT INTO intelligence_article_observations VALUES (?,'other-snapshot','other','2026-11-16T20:15:00Z',NULL,NULL,'UNKNOWN')", ['b' * 64])

    def test_hundred_thousand_synthetic_articles_roundtrip_through_zstd_parquet(self):
        self.db.execute("""INSERT INTO intelligence_articles
          SELECT sha256('synthetic-' || i::VARCHAR), 'fixture',
            'https://fixture.example/original/' || i::VARCHAR,
            'https://fixture.example/canonical/' || i::VARCHAR,
            'SYNTHETIC article ' || i::VARCHAR, TIMESTAMPTZ '2026-11-16T20:15:00Z'
          FROM range(100000) t(i)""")
        with tempfile.TemporaryDirectory() as folder:
            path = str(pathlib.Path(folder) / 'articles.parquet')
            self.db.sql('SELECT * FROM intelligence_articles ORDER BY article_id').write_parquet(path, compression='zstd')
            self.assertEqual(self.db.execute('SELECT count(*),count(DISTINCT article_id) FROM read_parquet(?)', [path]).fetchone(), (100000,100000))
            self.assertEqual(self.db.execute('SELECT DISTINCT compression FROM parquet_metadata(?)', [path]).fetchall(), [('ZSTD',)])
            for left,right,params in [('intelligence_articles','read_parquet(?)',[path]),('read_parquet(?)','intelligence_articles',[path])]:
                self.assertEqual(self.db.execute(f'SELECT count(*) FROM (SELECT * FROM {left} EXCEPT ALL SELECT * FROM {right})', params).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
