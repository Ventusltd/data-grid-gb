"""Bounded manual address stage beside the existing DC-A4 producer.

Collects Wikidata structured facts and GB postcode points, then rebuilds offline.
Never scrapes linked publisher bodies or edits accepted data generations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import io
import json
import os
from pathlib import Path
import re
import tempfile
import tarfile
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .build import address_metadata, build, encoded, postcode, sha, write_parquet

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_REQUESTS = 32
PROPERTIES = ('P17', 'P625', 'P6375', 'P969', 'P281', 'P856')
USER_AGENT = 'GlobalGrid2050-DC-addresses/1.0 (https://github.com/Ventusltd/data-grid-gb)'


def resource_path(folder, relative):
    allowed = r'(?:inputs\.json|cohort\.json|(?:wikidata|postcodes)-[0-9]{3}\.json|references/[a-z][a-z0-9_-]{0,79}\.geojson)'
    if not isinstance(relative, str) or not re.fullmatch(allowed, relative):
        raise ValueError('Unsafe snapshot resource path')
    path = folder / relative
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Unsafe snapshot resource path')
    return path


class Collector:
    def __init__(self, folder):
        self.folder = folder
        self.resources = []
        self.total = 0

    def fetch(self, relative, url, payload=None, expected=None):
        path = resource_path(self.folder, relative)
        if path.exists():
            raise FileExistsError('Captured source already exists')
        if len(self.resources) >= MAX_REQUESTS:
            raise ValueError('Request budget exceeded')
        p = urlsplit(url)
        if p.scheme != 'https' or p.hostname not in ('raw.githubusercontent.com', 'www.wikidata.org', 'api.postcodes.io'):
            raise ValueError('Endpoint is outside the existing approved source stage')
        req = Request(url, data=encoded(payload) if payload is not None else None,
                      headers={'User-Agent': USER_AGENT, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        with urlopen(req, timeout=45) as response:
            if response.status != 200 or urlsplit(response.geturl()).hostname != p.hostname:
                raise ValueError('Unexpected source status or redirect')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        self.total += len(raw)
        if len(raw) > MAX_RESPONSE_BYTES or self.total > MAX_TOTAL_BYTES:
            raise ValueError('Source byte budget exceeded')
        if expected is not None and sha(raw) != expected:
            raise ValueError('Pinned source bytes changed: ' + relative)
        document = json.loads(raw)
        if isinstance(document, dict) and 'error' in document:
            raise ValueError('Source returned an API error')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        self.resources.append({'path': relative, 'url': url, 'method': 'POST' if payload else 'GET',
                               'request_json': payload, 'http_status': 200, 'sha256': sha(raw), 'bytes': len(raw)})
        return document


def extract_entities(responses):
    entities = {}
    for document in responses:
        for qid, entity in document['entities'].items():
            if qid in entities or 'missing' in entity:
                raise ValueError('Duplicate or missing source entity')
            entities[qid] = {k: entity[k] for k in ('id', 'lastrevid')}
            entities[qid]['claims'] = {p: entity.get('claims', {}).get(p, []) for p in PROPERTIES}
    return entities


def parse_geocodes(document):
    if document.get('status') != 200:
        raise ValueError('Postcode source did not succeed')
    result = {}
    for row in document.get('result', []):
        key = postcode(row['query'])
        if key in result:
            raise ValueError('Duplicate postcode response')
        result[key] = row['result']
    return result


def load_snapshot(folder):
    """Every replay rechecks the actual source bytes, never merely saved counts."""
    snapshot_bytes = (folder / 'source-snapshot.json').read_bytes()
    snapshot = json.loads(snapshot_bytes)
    documents = {}
    for r in snapshot['resources']:
        path = resource_path(folder, r['path'])
        if r['path'] in documents:
            raise ValueError('Unsafe snapshot resource path')
        raw = path.read_bytes()
        if len(raw) != r['bytes'] or sha(raw) != r['sha256']:
            raise ValueError('Snapshot resource bytes do not match receipt')
        documents[r['path']] = json.loads(raw)
    config = documents['inputs.json']
    cohort = documents['cohort.json']
    by_path = {r['path']: r for r in snapshot['resources']}
    if by_path['cohort.json']['sha256'] != config['cohort']['sha256']:
        raise ValueError('Cohort differs from the pinned input configuration')
    for ref in config['references']:
        if by_path['references/' + ref['id'] + '.geojson']['sha256'] != ref['sha256']:
            raise ValueError('Reference differs from the pinned input configuration')
    entities = extract_entities([documents[r['path']] for r in snapshot['resources'] if r['path'].startswith('wikidata-')])
    geocodes = {}
    for r in snapshot['resources']:
        if r['path'].startswith('postcodes-'):
            batch = parse_geocodes(documents[r['path']])
            requested = {postcode(p) for p in r['request_json']['postcodes']}
            if set(batch) != requested or set(geocodes) & set(batch):
                raise ValueError('Postcode response does not match requested identities')
            geocodes.update(batch)
    references = [{**r, 'document': documents['references/' + r['id'] + '.geojson']} for r in config['references']]
    receipt = {'retrieved_at': snapshot['retrieved_at'], 'snapshot_sha256': sha(snapshot_bytes),
               'cohort_sha256': config['cohort']['sha256'], 'cohort_source_url': config['cohort']['url'],
               'resources': snapshot['resources'], 'producer': 'data-grid-gb/datacentres/locations'}
    return build(cohort, entities, geocodes, receipt, references)


def collect(config_path, folder):
    config_raw = config_path.read_bytes()
    config = json.loads(config_raw)
    ids = [ref['id'] for ref in config['references']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate reference dataset identity')
    for identity in ids:
        resource_path(folder, 'references/' + identity + '.geojson')
    (folder / 'inputs.json').write_bytes(config_raw)
    c = Collector(folder)
    c.resources.append({'path': 'inputs.json', 'url': None, 'method': 'LOCAL_CONFIG',
                        'request_json': None, 'http_status': None, 'sha256': sha(config_raw), 'bytes': len(config_raw)})
    cohort = c.fetch('cohort.json', config['cohort']['url'], expected=config['cohort']['sha256'])
    rows = cohort['records']
    if not 0 < len(rows) <= 1000 or cohort['record_count'] != len(rows):
        raise ValueError('Cohort exceeds the bounded source stage')
    ids = sorted({row['id'].removeprefix('DC-WD-') for row in rows})
    if len(ids) != len(rows) or any(not re.fullmatch(r'Q[1-9]\d*', qid) for qid in ids):
        raise ValueError('Invalid source cohort identities')
    raw_entities = []
    for offset in range(0, len(ids), 50):
        url = 'https://www.wikidata.org/w/api.php?' + urlencode({'action': 'wbgetentities',
              'ids': '|'.join(ids[offset:offset+50]), 'props': 'claims|info', 'format': 'json', 'maxlag': 5})
        raw_entities.append(c.fetch(f'wikidata-{offset//50:03}.json', url))
    entities = extract_entities(raw_entities)
    if set(entities) != set(ids):
        raise ValueError('Source response does not close the requested cohort')
    postcodes = set()
    for row in rows:
        _, _, pcs = address_metadata(entities[row['id'][6:]], row['country']['code'])
        if len(pcs) == 1 and not pcs[0].startswith('BT'):
            postcodes.add(pcs[0])
    postcodes = sorted(postcodes)
    for offset in range(0, len(postcodes), 100):
        c.fetch(f'postcodes-{offset//100:03}.json',
                'https://api.postcodes.io/postcodes?filter=postcode,longitude,latitude,quality,country',
                payload={'postcodes': postcodes[offset:offset+100]})
    for ref in config['references']:
        c.fetch('references/' + ref['id'] + '.geojson', ref['url'], expected=ref['sha256'])
    snapshot = {'schema': 'data-grid-gb.dc-address-source-snapshot.v1',
                'retrieved_at': datetime.now(timezone.utc).isoformat(), 'resources': c.resources,
                'bounds': {'max_requests': MAX_REQUESTS, 'max_response_bytes': MAX_RESPONSE_BYTES,
                           'max_total_bytes': MAX_TOTAL_BYTES, 'actual_requests': len(c.resources)-1,
                           'actual_response_bytes': c.total}}
    (folder / 'source-snapshot.json').write_bytes(encoded(snapshot))


def product(folder, document, parquet=False):
    geojson = encoded(document)
    (folder / 'datacentre-locations.geojson').write_bytes(geojson)
    manifest = {'schema': 'data-grid-gb.dc-address-product-manifest.v1', 'counts': document['counts'],
                'source_snapshot_sha256': document['source_receipt']['snapshot_sha256'],
                'products': {'datacentre-locations.geojson': {'sha256': sha(geojson), 'bytes': len(geojson)}}}
    if parquet:
        p = folder / 'datacentre-locations.parquet'
        proof = write_parquet(document, p)
        second = folder / 'readback-repeat.parquet'
        write_parquet(document, second)
        if p.read_bytes() != second.read_bytes():
            raise ValueError('Independent repeated Parquet builds differ')
        second.unlink()
        manifest['products'][p.name] = {**proof, 'bytes': p.stat().st_size, 'repeated_compile': 'IDENTICAL'}
    manifest['code_sha256'] = {p.name: sha(p.read_bytes()) for p in (Path(__file__), Path(__file__).with_name('build.py'))}
    snapshot_path = folder / 'source-snapshot.json'
    if snapshot_path.exists():
        # A compact, reproducible source closure avoids repeated expanded copies of
        # the already-owned reference GeoJSON. It is not a browser dependency.
        snapshot = json.loads(snapshot_path.read_bytes())
        paths = sorted(r['path'] for r in snapshot['resources']) + ['source-snapshot.json']
        archive_path = folder / 'source-snapshot.tar.gz'
        with archive_path.open('xb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as archive:
                for relative in paths:
                    path = snapshot_path if relative == 'source-snapshot.json' else resource_path(folder, relative)
                    raw = path.read_bytes()
                    info = tarfile.TarInfo(relative)
                    info.size, info.mtime, info.mode = len(raw), 0, 0o644
                    archive.addfile(info, io.BytesIO(raw))
        manifest['source_archive'] = {'path': archive_path.name, 'sha256': sha(archive_path.read_bytes()),
                                      'bytes': archive_path.stat().st_size, 'resources': len(paths)}
    # Final manifest is the completion marker; a partially written scratch is never accepted.
    (folder / 'manifest.json').write_bytes(encoded(manifest))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('collect', 'build'))
    parser.add_argument('--inputs', type=Path, default=Path(__file__).with_name('inputs.json'))
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--output', type=Path, required=True, help='New generation folder; never an existing accepted folder')
    parser.add_argument('--parquet', action='store_true', help='Also build and fully read back with the declared DuckDB version')
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError('Output generation already exists')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dc-address-stage-', dir=args.output.parent) as scratch:
        stage = Path(scratch) / 'generation'
        stage.mkdir()
        if args.mode == 'collect':
            collect(args.inputs, stage)
            document = load_snapshot(stage)
        else:
            if args.snapshot is None:
                raise ValueError('Offline build requires --snapshot')
            document = load_snapshot(args.snapshot)
        manifest = product(stage, document, args.parquet)
        if args.output.exists():
            raise FileExistsError('Output appeared during build; no history replacement')
        os.rename(stage, args.output)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
