"""Offline synthetic checks for address provenance, uncertainty and source joins."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from datacentres.locations.build import build, encoded, point, sha, write_parquet
from datacentres.locations.pipeline import Collector, load_snapshot, main


def claim(prop, value, rank='normal'):
    return {'id': 'Q1$' + prop, 'rank': rank, 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}},
            'references': [{'snaks': {'P854': [{'snaktype': 'value', 'datavalue': {'value': 'https://fixture.example/source'}}]}}]}


def fixtures():
    cohort = {'schema': 'globalgrid2050.global-datacentres.projects.v1', 'record_count': 1,
              'records': [{'id': 'DC-WD-Q1', 'name': 'Synthetic DC',
                           'country': {'code': 'GB', 'wikidata_id': 'Q145', 'name': 'United Kingdom'},
                           'source': {'revision_url': 'https://www.wikidata.org/w/index.php?title=Q1&oldid=1'}}]}
    entity = {'id': 'Q1', 'lastrevid': 2, 'claims': {
        'P17': [claim('P17', {'id': 'Q145'})],
        'P6375': [claim('P6375', {'text': '1 Synthetic Street, London SW1A 1AA', 'language': 'en'})]}}
    geocodes = {'SW1A 1AA': {'postcode': 'SW1A 1AA', 'latitude': 51.501, 'longitude': -0.141,
                            'quality': 1, 'country': 'England'}}
    return cohort, {'Q1': entity}, geocodes, {'retrieved_at': '2026-11-17T00:00:00Z'}


def make_snapshot(folder):
    cohort, entities, geocodes, _ = fixtures()
    payloads = {'cohort.json': cohort, 'wikidata-000.json': {'entities': entities},
                'postcodes-000.json': {'status': 200, 'result': [{'query': k, 'result': v} for k, v in geocodes.items()]}}
    payloads['inputs.json'] = {'cohort': {'url': 'https://fixture.example/cohort', 'sha256': sha(encoded(cohort))}, 'references': []}
    resources = []
    for name, data in payloads.items():
        raw = encoded(data)
        (folder / name).write_bytes(raw)
        resources.append({'path': name, 'bytes': len(raw), 'sha256': sha(raw),
                          'request_json': {'postcodes': list(geocodes)} if name.startswith('postcodes') else None})
    snapshot = {'retrieved_at': '2026-11-17T00:00:00Z', 'resources': resources}
    (folder / 'source-snapshot.json').write_bytes(encoded(snapshot))


class AddressLocationTests(unittest.TestCase):
    def setUp(self):
        self.cohort, self.entities, self.geocodes, self.receipt = fixtures()

    def run_build(self, references=()):
        return build(self.cohort, self.entities, self.geocodes, self.receipt, references)

    def test_street_address_claim_and_postcode_precision_remain_attributable(self):
        doc = self.run_build()
        p = doc['features'][0]['properties']
        self.assertEqual(doc['counts']['postcode_centroids'], 1)
        self.assertEqual(p['addresses'][0]['statement_id'], 'Q1$P6375')
        self.assertEqual(p['addresses'][0]['reference_urls'], ['https://fixture.example/source'])
        self.assertEqual(p['location_precision'], 'POSTCODE_CENTROID_APPROXIMATE')
        self.assertEqual(p['source_statement_licence'], 'CC0-1.0')
        self.assertIn('OS OpenData', p['geometry_licence'])
        self.assertEqual(len(p['geometry_attribution']), 4)
        self.assertNotIn('source_licence', p)
        self.assertIn('oldid=2', p['source_revision_url'])
        self.assertIn('oldid=1', p['original_pipeline_revision_url'])
        self.assertFalse(p['grid_connection_assessed'])
        self.assertIn('longitude=-0.141', p['screening_url'])
        self.assertNotIn('repd_ref', p['screening_url'])

    def test_missing_address_or_point_is_null_and_source_record_is_retained(self):
        self.entities['Q1']['claims'].pop('P6375')
        doc = self.run_build()
        self.assertEqual(doc['counts']['records'], 1)
        self.assertIsNone(doc['features'][0]['geometry'])
        self.assertIsNone(doc['features'][0]['properties']['screening_url'])

    def test_preferred_wikidata_point_beats_postcode_and_preserves_precision(self):
        values = {'longitude': -1, 'latitude': 52, 'precision': 0.01, 'globe': 'http://www.wikidata.org/entity/Q2'}
        self.entities['Q1']['claims']['P625'] = [claim('P625-old', {**values, 'longitude': -2}), claim('P625', values, 'preferred')]
        p = self.run_build()['features'][0]['properties']
        self.assertEqual(p['location_precision'], 'WIKIDATA_REPORTED_POINT')
        self.assertEqual(p['location_source']['precision_degrees'], 0.01)

    def test_conflicting_points_are_not_silently_replaced_by_a_postcode(self):
        value = {'longitude': -1, 'latitude': 52, 'precision': 0.01, 'globe': 'http://www.wikidata.org/entity/Q2'}
        self.entities['Q1']['claims']['P625'] = [claim('P625-a', value), claim('P625-b', {**value, 'longitude': -2})]
        self.assertIsNone(self.run_build()['features'][0]['geometry'])

    def test_multiple_postcodes_require_review(self):
        self.entities['Q1']['claims']['P281'] = [claim('P281', 'M1 1AA')]
        self.assertIsNone(self.run_build()['features'][0]['geometry'])

    def test_northern_ireland_postcode_scope_is_not_silently_licensed(self):
        self.entities['Q1']['claims']['P6375'][0]['mainsnak']['datavalue']['value']['text'] = 'Synthetic Belfast BT1 1AA'
        self.geocodes['BT1 1AA'] = {'postcode': 'BT1 1AA', 'latitude': 54.6, 'longitude': -5.9, 'country': 'Northern Ireland'}
        doc = self.run_build()
        self.assertIsNone(doc['features'][0]['geometry'])
        self.assertIn('distinct reuse terms', doc['features'][0]['properties']['notes'][0])

    def test_geocoder_cannot_substitute_another_postcode_or_country(self):
        for wrong in ({'postcode': 'M1 1AA'}, {'country': 'France'}, {'latitude': 48.8566, 'longitude': 2.3522}):
            with self.subTest(wrong=wrong):
                original = copy.deepcopy(self.geocodes)
                self.geocodes['SW1A 1AA'].update(wrong)
                with self.assertRaises(ValueError):
                    self.run_build()
                self.geocodes = original

    def test_nonfinite_false_origin_boolean_and_out_of_range_points_reject(self):
        for lon, lat in [(0, 0), (float('nan'), 52), (-1, float('inf')), (True, 52), (181, 52), (-1, 91)]:
            with self.subTest(lon=lon, lat=lat), self.assertRaises(ValueError):
                point(lon, lat)

    def test_source_country_cannot_drift_from_original_country_identity(self):
        self.entities['Q1']['claims']['P17'] = [claim('P17', {'id': 'Q142'})]
        with self.assertRaisesRegex(ValueError, 'country conflicts'):
            self.run_build()

    def test_duplicate_or_missing_source_identity_fails(self):
        self.cohort['records'] *= 2
        self.cohort['record_count'] = 2
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.run_build()

    def test_name_candidate_does_not_assign_geometry_or_verified_identity(self):
        self.entities['Q1']['claims'].pop('P6375')
        refs = [{'id': 'synthetic-source', 'url': 'https://fixture.example/data', 'sha256': 'a'*64,
                 'licence': 'SYNTHETIC', 'attribution': 'Synthetic fixture', 'document': {'features': [
                     {'type': 'Feature', 'geometry': point(-1, 52), 'properties': {'id': 'OT-C-1', 'name': 'Synthetic DC', 'operator:wikidata': 'Q1'}}]}}]
        f = self.run_build(refs)['features'][0]
        self.assertIsNone(f['geometry'])
        self.assertEqual(f['properties']['cross_references'][0]['method'], 'EXACT_NAME_CANDIDATE')
        self.assertFalse(f['properties']['cross_references'][0]['geometry_adopted'])

    def test_explicit_source_qid_is_separate_from_operator_and_name_matching(self):
        refs = [{'id': 'synthetic-source', 'url': 'https://fixture.example/data', 'sha256': 'a'*64,
                 'licence': 'SYNTHETIC', 'attribution': 'Synthetic fixture', 'document': {'features': [
                     {'type': 'Feature', 'geometry': point(-1, 52), 'properties': {'id': 'wd:Q1', 'name': 'Another label', 'sources': [{'source': 'Wikidata', 'source_id': 'wd:Q1'}]}}]}}]
        ref = self.run_build(refs)['features'][0]['properties']['cross_references'][0]
        self.assertEqual(ref['method'], 'EXPLICIT_WIKIDATA_SOURCE_ID')
        self.assertGreater(ref['distance_m'], 1000)
        self.assertFalse(ref['geometry_adopted'])

    def test_collector_refuses_arbitrary_source_or_internal_endpoint(self):
        with tempfile.TemporaryDirectory() as folder, patch('datacentres.locations.pipeline.urlopen') as network:
            with self.assertRaises(ValueError):
                Collector(Path(folder)).fetch('x.json', 'https://127.0.0.1/private')
            network.assert_not_called()

    def test_collector_validates_path_before_network_or_existing_file_writes(self):
        with tempfile.TemporaryDirectory() as folder, patch('datacentres.locations.pipeline.urlopen') as network:
            root = Path(folder)
            target = root / 'accepted.json'
            target.write_bytes(b'accepted bytes')
            stage = root / 'scratch'
            stage.mkdir()
            for name in ('../accepted.json', 'references/../../accepted.json', 'references/C:accepted.geojson'):
                with self.assertRaisesRegex(ValueError, 'Unsafe'):
                    Collector(stage).fetch(name, 'https://www.wikidata.org/w/api.php')
            self.assertEqual(target.read_bytes(), b'accepted bytes')
            network.assert_not_called()

    def test_collector_cannot_replace_an_existing_captured_resource(self):
        with tempfile.TemporaryDirectory() as folder, patch('datacentres.locations.pipeline.urlopen') as network:
            root = Path(folder)
            (root / 'cohort.json').write_bytes(b'accepted source bytes')
            with self.assertRaises(FileExistsError):
                Collector(root).fetch('cohort.json', 'https://raw.githubusercontent.com/owner/repo/pinned/file')
            self.assertEqual((root / 'cohort.json').read_bytes(), b'accepted source bytes')
            network.assert_not_called()

    def test_offline_snapshot_requires_actual_source_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_snapshot(root)
            self.assertEqual(load_snapshot(root)['counts']['with_geometry'], 1)
            (root / 'wikidata-000.json').write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'bytes'):
                load_snapshot(root)

    def test_snapshot_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            make_snapshot(root)
            p = root / 'source-snapshot.json'
            d = json.loads(p.read_bytes())
            d['resources'][0]['path'] = '../cohort.json'
            p.write_bytes(encoded(d))
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                load_snapshot(root)

    def test_existing_generation_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'accepted.txt').write_text('accepted bytes')
            with self.assertRaises(FileExistsError):
                main(['build', '--output', str(root)])
            self.assertEqual((root / 'accepted.txt').read_text(), 'accepted bytes')

    def test_offline_replays_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source'
            source.mkdir()
            make_snapshot(source)
            for name in ('first', 'second'):
                main(['build', '--snapshot', str(source), '--output', str(root / name)])
            self.assertEqual((root / 'first/datacentre-locations.geojson').read_bytes(), (root / 'second/datacentre-locations.geojson').read_bytes())

    @unittest.skipUnless(importlib.util.find_spec('duckdb'), 'Optional DuckDB is installed by the address workflow')
    def test_full_feature_rows_and_nulls_roundtrip_in_zstd_parquet(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'locations.parquet'
            proof = write_parquet(self.run_build(), p)
            self.assertEqual(proof['rows'], 1)
            self.assertEqual(proof['full_row_readback'], 'PASS')

    def test_committed_actual_generation_rebuilds_from_its_complete_source_archive(self):
        folder = Path(__file__).parents[1] / 'datacentres/locations/data/20261117T002200Z'
        manifest = json.loads((folder / 'manifest.json').read_bytes())
        source = folder / manifest['source_archive']['path']
        self.assertEqual(sha(source.read_bytes()), manifest['source_archive']['sha256'])
        for name, proof in manifest['products'].items():
            self.assertEqual(sha((folder / name).read_bytes()), proof['sha256'])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with tarfile.open(source) as archive:
                archive.extractall(root, filter='data')
            document = load_snapshot(root)
            self.assertEqual(encoded(document), (folder / 'datacentre-locations.geojson').read_bytes())
            cohort = json.loads((root / 'cohort.json').read_bytes())
            self.assertEqual({f['id'] for f in document['features']}, {r['id'] for r in cohort['records']})
            if importlib.util.find_spec('duckdb'):
                path = root / 'reproduced.parquet'
                write_parquet(document, path)
                self.assertEqual(path.read_bytes(), (folder / 'datacentre-locations.parquet').read_bytes())


if __name__ == '__main__':
    unittest.main()
