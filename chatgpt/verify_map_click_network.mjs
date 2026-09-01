#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const productPath = resolve(root, 'chatgpt/derived/map-click-network.v1.json');
const bytes = await readFile(productPath);
const product = JSON.parse(bytes);
let passed = 0;
const failures = [];
function check(label, condition) {
  if (condition) { passed += 1; console.log(`  [PASS] ${label}`); }
  else { failures.push(label); console.log(`  [FAIL] ${label}`); }
}
check('schema is explicit', product.schema === 'data-grid-gb.map-click-network.v1');
check('the product refuses connection and headroom claims',
  /not solved power flow/.test(product.claim_boundary)
  && /available headroom/.test(product.claim_boundary)
  && /connection assessment/.test(product.claim_boundary));
check('both owner products are pinned by schema and SHA-256',
  product.source.network.schema === 'data-grid-gb.transmission-network.v1'
  && product.source.connection_points.schema === 'data-grid-gb.connection-points.v3'
  && /^[a-f0-9]{64}$/.test(product.source.network.sha256)
  && /^[a-f0-9]{64}$/.test(product.source.connection_points.sha256));
check('all 886 click targets survive', product.connection_points.length === 886);
check('safe location count is preserved', product.counts.located === 502);
check('every point has a stable site identity', product.connection_points.every(point =>
  point.site_code && point.name && point.transmission_owner && point.voltages_kv.length));
check('every circuit names nodes, impedance base values and four seasonal ratings',
  product.connection_points.flatMap(point => point.existing_circuits).every(circuit =>
    circuit.local_node && circuit.remote_node
    && ['r', 'x', 'b'].every(key => key in circuit.impedance_pct_100mva)
    && ['winter', 'spring', 'summer', 'autumn'].every(key => key in circuit.seasonal_rating_mva)));
check('every circuit landing carries explicit validated-or-null terminal voltages',
  product.connection_points.flatMap(point => point.existing_circuits).every(circuit =>
    'local_voltage_kv' in circuit && 'remote_voltage_kv' in circuit
    && (circuit.local_voltage_kv === null || Number.isFinite(circuit.local_voltage_kv))));
check('unknown remote identities remain null rather than guessed',
  product.connection_points.flatMap(point => point.existing_circuits)
    .every(circuit => circuit.remote_site === null || circuit.remote_site.site_code));
check('fault current remains separated by published voltage without deleting site buses', product.connection_points
  .filter(point => Object.keys(point.fault_current_by_voltage).length)
  .every(point => Object.entries(point.fault_current_by_voltage).every(([kv, scope]) =>
    Number(kv) > 0 && Object.values(scope).every(scenario =>
      scenario.voltages_kv.length === 1 && scenario.voltages_kv[0] === Number(kv)))));
check('planned equipment remains separate from existing circuits',
  product.connection_points.every(point => Array.isArray(point.existing_circuits)
    && Array.isArray(point.planned_changes)));
check('planned-change appearances are oriented to the clicked site and carry voltage',
  product.connection_points.flatMap(point => point.planned_changes).every(change =>
    change.local_node && change.remote_node && 'local_voltage_kv' in change));
const cottam = product.connection_points.find(point => point.site_code === 'COTT');
check('Cottam carries its 400 kV fault scope and published neighbourhood',
  cottam?.fault_current_by_voltage?.['400'] && cottam.existing_circuits.length === 8
  && cottam.published_site_summary.planned_changes === 17);
check('Cottam discloses the planned change its node projection cannot attach',
  cottam?.projection_reconciliation.planned_change_appearances === 16
  && cottam.projection_reconciliation.unresolved_planned_change_appearances === 1);
const westBurton = product.connection_points.find(point => point.site_code === 'WBUR');
check('West Burton keeps 132 and 400 kV fault scopes distinct',
  westBurton?.fault_current_by_voltage?.['132'] && westBurton.fault_current_by_voltage?.['400']);
const sidecar = await readFile(`${productPath}.sha256`, 'utf8');
check('the sidecar digest is correct', sidecar.split(/\s+/)[0]
  === createHash('sha256').update(bytes).digest('hex'));
check('output remains bounded for browser delivery', bytes.length < 12_000_000);
console.log(`\n${passed}/${passed + failures.length} checks passed`);
if (failures.length) process.exit(1);
console.log('map clicks receive published one-hop network facts, never inferred headroom.');
