"""Offline DC address/location projection. No identity or grid-capacity approval."""
from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import urlencode

SCHEMA = 'data-grid-gb.dc-address-locations.v1'
POSTCODE = re.compile(r'\b(?:GIR\s?0AA|[A-PR-UWYZ][A-HK-Y]?\d[\dA-HJKPSTUW]?\s?\d[ABD-HJLNP-UW-Z]{2})\b', re.I)
QID = re.compile(r'Q[1-9]\d*\Z')
FOOTNOTE = 'Screening only; not a grid-capacity assessment.'
GEOCODER_LICENCE = 'https://postcodes.io/docs/licences/'
GB_ATTRIBUTION = ['Contains Ordnance Survey data © Crown copyright and database right 2025',
                  'Contains Royal Mail data © Royal Mail copyright and database right 2025',
                  'Contains National Statistics data © Crown copyright and database right 2025',
                  'Contains NRS data © Crown copyright and database right 2025']


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def postcode(value):
    compact = re.sub(r'\s+', '', value.upper())
    return compact[:-3] + ' ' + compact[-3:]


def point(lon, lat):
    if (type(lon) not in (int, float) or type(lat) not in (int, float)
            or not math.isfinite(lon) or not math.isfinite(lat)
            or not (-180 <= lon <= 180 and -90 <= lat <= 90) or (lon, lat) == (0, 0)):
        raise ValueError('Invalid geographic point')
    return {'type': 'Point', 'coordinates': [lon, lat]}


def statements(entity, prop):
    rows = [s for s in entity.get('claims', {}).get(prop, [])
            if s.get('rank') != 'deprecated' and s.get('mainsnak', {}).get('snaktype') == 'value']
    preferred = [s for s in rows if s.get('rank') == 'preferred']
    return preferred or rows


def statement_value(statement):
    return statement['mainsnak']['datavalue']['value']


def text_claims(entity, prop):
    result = []
    for s in statements(entity, prop):
        value = statement_value(s)
        text = value.get('text') if isinstance(value, dict) else value
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Invalid textual address claim')
        refs = sorted({r['datavalue']['value'] for ref in s.get('references', [])
                       for r in ref.get('snaks', {}).get('P854', [])
                       if r.get('snaktype') == 'value' and isinstance(r.get('datavalue', {}).get('value'), str)})
        result.append({'statement_id': s['id'], 'rank': s['rank'], 'text': text,
                       'language': value.get('language') if isinstance(value, dict) else None,
                       'reference_urls': refs})
    return sorted(result, key=lambda x: (x['text'], x['statement_id']))


def address_metadata(entity, country):
    addresses = text_claims(entity, 'P6375') + text_claims(entity, 'P969')
    declared = text_claims(entity, 'P281')
    # Only a full, unambiguous GB postcode can request a GB lookup. No address guessing.
    candidates = set()
    if country in ('GB', 'UK'):
        for s in addresses + declared:
            candidates.update(postcode(m.group()) for m in POSTCODE.finditer(s['text']))
    return addresses, declared, sorted(candidates)


def screening_url(geometry, name):
    if geometry is None:
        return None
    lon, lat = geometry['coordinates']
    point(lon, lat)
    return 'https://ventusltd.github.io/gridatlas/atlas/?' + urlencode({
        'longitude': lon, 'latitude': lat, 'zoom': 12, 'project': name,
        'technology': 'datacentre'})


def distance_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def source_qids(properties):
    """Facility IDs only. Deliberately ignores operator:wikidata and arbitrary text."""
    ids = set()
    for key in ('wikidata', 'wikidata_id'):
        value = properties.get(key)
        if isinstance(value, str) and QID.fullmatch(value):
            ids.add(value)
    for row in properties.get('sources', []) + properties.get('records', []):
        if not isinstance(row, dict) or row.get('source', '').lower() != 'wikidata':
            continue
        value = row.get('source_id', row.get('id', ''))
        value = value.removeprefix('wd:') if isinstance(value, str) else ''
        if QID.fullmatch(value):
            ids.add(value)
    return ids


def cross_references(feature, references):
    """Retain explicit source joins; name/spatial discovery never supplies geometry."""
    result = []
    properties = feature['properties']
    qid = properties['wikidata_id']
    normalized = re.sub(r'\W+', ' ', properties['name'].casefold()).strip()
    for ref in references:
        if ref.get('country_scope', 'GB') != properties['country']['code']:
            continue
        for other in ref['document'].get('features', []):
            p = other.get('properties') or {}
            if ref.get('class_filter') and p.get('class') != ref['class_filter']:
                continue
            exact = qid in source_qids(p)
            same_name = normalized and normalized == re.sub(r'\W+', ' ', str(p.get('name', '')).casefold()).strip()
            if not exact and not same_name:
                continue
            geometry = other.get('geometry')
            gap = None
            if feature['geometry'] and geometry and geometry.get('type') == 'Point':
                coords = geometry['coordinates']
                point(*coords)
                gap = round(distance_m(feature['geometry']['coordinates'], coords), 1)
            result.append({'dataset_id': ref['id'], 'record_id': p.get('id', other.get('id')),
                           'source_sha256': ref['sha256'], 'source_url': ref['url'],
                           'method': 'EXPLICIT_WIKIDATA_SOURCE_ID' if exact else 'EXACT_NAME_CANDIDATE',
                           'review_state': 'SOURCE_ID_JOIN' if exact else 'CANDIDATE',
                           'distance_m': gap, 'source_licence': ref['licence'],
                           'source_attribution': ref['attribution'], 'geometry_adopted': False})
    return sorted(result, key=lambda x: (x['dataset_id'], str(x['record_id'])))


def build(cohort, entities, geocodes, receipt, references=()):
    if cohort.get('schema') != 'globalgrid2050.global-datacentres.projects.v1':
        raise ValueError('Expected the existing Pipeline source-indexed DC cohort')
    records = cohort.get('records', [])
    if not records or cohort.get('record_count') != len(records):
        raise ValueError('DC cohort count mismatch')
    features, seen = [], set()
    for row in sorted(records, key=lambda x: x['id']):
        rid = row['id']
        qid = rid.removeprefix('DC-WD-')
        if not QID.fullmatch(qid) or rid in seen:
            raise ValueError('Invalid or duplicate original DC source identity')
        seen.add(rid)
        entity = entities[qid]
        if entity.get('id') != qid or type(entity.get('lastrevid')) is not int:
            raise ValueError('Missing source entity/revision closure')
        source_countries = {statement_value(s).get('id') for s in statements(entity, 'P17')}
        if source_countries != {row['country']['wikidata_id']}:
            raise ValueError('Current source country conflicts with the original cohort')
        revision = f"https://www.wikidata.org/w/index.php?title={qid}&oldid={entity['lastrevid']}"
        addresses, postal_claims, postcodes = address_metadata(entity, row['country']['code'])
        geometry, precision, locator = None, 'UNKNOWN', None
        coordinate_claims = statements(entity, 'P625')
        points = []
        for s in coordinate_claims:
            v = statement_value(s)
            if v.get('globe') != 'http://www.wikidata.org/entity/Q2':
                raise ValueError('Unsupported coordinate reference globe')
            coords = point(v.get('longitude'), v.get('latitude'))
            if type(v.get('precision')) not in (int, float) or not math.isfinite(v['precision']) or v['precision'] <= 0:
                raise ValueError('Missing coordinate precision')
            points.append((coords, v['precision'], s['id']))
        notes = []
        if len(points) == 1:
            geometry, degrees, statement_id = points[0]
            precision = 'WIKIDATA_REPORTED_POINT'
            locator = {'revision_url': revision, 'statement_id': statement_id, 'precision_degrees': degrees}
        elif len(points) > 1:
            notes.append('Conflicting coordinate statements: no automatic point selection')
        elif len(postcodes) == 1:
            pc = postcodes[0]
            if pc.startswith('BT'):
                notes.append('Northern Ireland postcode geocoding withheld: distinct reuse terms')
            else:
                geo = geocodes.get(pc)
                if geo is not None:
                    if postcode(geo.get('postcode', '')) != pc:
                        raise ValueError('Geocoder returned another postcode')
                    if geo.get('country') not in ('England', 'Scotland', 'Wales'):
                        raise ValueError('Geocoder result outside supported GB licence scope')
                    if geo.get('latitude') is not None and geo.get('longitude') is not None:
                        geometry = point(geo['longitude'], geo['latitude'])
                        # A postcode/country label alone cannot bless a remote or false-origin point.
                        # This is a gross GB geographic sanity bound, not an accuracy certificate.
                        if not (-8.7 <= geo['longitude'] <= 2.0 and 49.8 <= geo['latitude'] <= 61.2):
                            raise ValueError('Geocoder coordinates are outside Great Britain bounds')
                        precision = 'POSTCODE_CENTROID_APPROXIMATE'
                        locator = {'postcode': pc, 'endpoint': 'https://api.postcodes.io/postcodes',
                                   'quality': geo.get('quality'), 'licence_url': GEOCODER_LICENCE}
        elif len(postcodes) > 1:
            notes.append('Multiple source postcodes: no automatic address selection')
        if geometry is None and not notes:
            notes.append('No supported unambiguous coordinate or geocoded postcode')
        props = {'id': rid, 'wikidata_id': qid, 'name': row['name'], 'country': row['country'],
                 'addresses': addresses, 'postal_code_statements': postal_claims,
                 'postcode_candidates': postcodes, 'location_precision': precision,
                 'location_source': locator, 'source_revision_url': revision,
                 'original_pipeline_revision_url': row['source']['revision_url'],
                 'source_statement_licence': 'CC0-1.0', 'source_statement_licence_url': 'https://www.wikidata.org/wiki/Wikidata:Copyright',
                 'geometry_licence': ('OS OpenData: GB postcode source terms' if precision == 'POSTCODE_CENTROID_APPROXIMATE' else 'CC0-1.0') if geometry else None,
                 'geometry_licence_url': (GEOCODER_LICENCE if precision == 'POSTCODE_CENTROID_APPROXIMATE' else 'https://www.wikidata.org/wiki/Wikidata:Copyright') if geometry else None,
                 'geometry_attribution': GB_ATTRIBUTION if precision == 'POSTCODE_CENTROID_APPROXIMATE' else ['Wikidata structured statements: CC0'] if geometry else [],
                 'source_retrieved_at': receipt['retrieved_at'], 'notes': notes,
                 'screening_note': FOOTNOTE, 'facility_identity_review': 'NOT_INDEPENDENTLY_VERIFIED',
                 'grid_connection_assessed': False}
        feature = {'type': 'Feature', 'id': rid, 'geometry': geometry, 'properties': props}
        props['screening_url'] = screening_url(geometry, row['name'])
        props['cross_references'] = cross_references(feature, references)
        features.append(feature)
    counts = {'records': len(features), 'with_address': sum(bool(f['properties']['addresses']) for f in features),
              'with_geometry': sum(f['geometry'] is not None for f in features),
              'postcode_centroids': sum(f['properties']['location_precision'] == 'POSTCODE_CENTROID_APPROXIMATE' for f in features),
              'unknown_geometry': sum(f['geometry'] is None for f in features)}
    return {'type': 'FeatureCollection', 'schema': SCHEMA, 'counts': counts,
            'attribution': ['Wikidata structured statements: CC0'] + GB_ATTRIBUTION +
                           sorted({r['attribution'] for r in references}),
            'source_receipt': receipt, 'screening_note': FOOTNOTE, 'features': features}


def write_parquet(document, path):
    """Serialize every GeoJSON feature, then compare every row after readback."""
    import duckdb
    with duckdb.connect() as db:
        db.execute('CREATE TABLE locations(id VARCHAR PRIMARY KEY, name VARCHAR, longitude DOUBLE, latitude DOUBLE, location_precision VARCHAR, feature_json VARCHAR)')
        rows = []
        for f in document['features']:
            p = f['properties']
            lon, lat = f['geometry']['coordinates'] if f['geometry'] else (None, None)
            rows.append((f['id'], p['name'], lon, lat, p['location_precision'], encoded(f).decode('utf-8')))
        db.executemany('INSERT INTO locations VALUES (?,?,?,?,?,?)', rows)
        db.sql('SELECT * FROM locations ORDER BY id').write_parquet(str(path), compression='zstd')
        actual = db.execute('SELECT * FROM read_parquet(?,hive_partitioning=false) ORDER BY id', [str(path)]).fetchall()
        if actual != sorted(rows):
            raise ValueError('Full-row Parquet readback mismatch')
        if db.execute('SELECT DISTINCT compression FROM parquet_metadata(?)', [str(path)]).fetchall() != [('ZSTD',)]:
            raise ValueError('Parquet compression mismatch')
    return {'engine': 'duckdb-' + duckdb.__version__, 'rows': len(rows), 'full_row_readback': 'PASS', 'sha256': sha(path.read_bytes())}
