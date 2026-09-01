#!/usr/bin/env node
/** Build a browser-consumable, one-hop ETYS neighbourhood for every safe click target. */
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const networkPath = resolve(REPO, 'derived/gb-transmission-network.v1.json');
const pointsPath = resolve(REPO, 'derived/connection-points.v3.json');
const outputPath = resolve(REPO, 'chatgpt/derived/map-click-network.v1.json');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const canonical = value => JSON.stringify(value) + '\n';
const [networkBytes, pointsBytes] = await Promise.all([readFile(networkPath), readFile(pointsPath)]);
const network = JSON.parse(networkBytes);
const points = JSON.parse(pointsBytes);
if (network.schema !== 'data-grid-gb.transmission-network.v1') throw new Error('unrecognised network schema');
if (points.schema !== 'data-grid-gb.connection-points.v3') throw new Error('unrecognised connection-point schema');

const sites = new Map(network.sites.map(site => [site.code, site]));
const nodes = new Map(network.nodes.map(node => [node.node, node]));
const pointByCode = new Map(points.connection_points.map(point => [point.site_code, point]));
const nodeSite = nodeName => nodes.get(nodeName)?.site_code || null;
const siteSummary = code => {
  const site = sites.get(code);
  const point = pointByCode.get(code);
  return site ? {
    site_code: code, name: site.name, transmission_owner: site.transmission_owner,
    voltages_kv: site.voltages_kv,
    location: point?.location || null
  } : null;
};

const bySite = new Map(points.connection_points.map(point => [point.site_code, {
  site_code: point.site_code,
  name: point.name,
  transmission_owner: point.transmission_owner,
  voltages_kv: point.voltages_kv,
  location: point.location || null,
  fault_current_by_voltage: point.fault_current_by_voltage || {},
  published_site_summary: {
    circuits: point.circuits, transformers: point.transformers,
    planned_changes: point.planned_changes,
    planned_change_years: point.planned_change_years,
    reactive_compensation: point.reactive_compensation
  },
  existing_circuits: [], transformers: [], reactive_compensation: [],
  interconnectors: [], planned_changes: []
}]));

function add(code, field, value) { if (bySite.has(code)) bySite.get(code)[field].push(value); }
for (const circuit of network.circuits) {
  const a = nodeSite(circuit.node_1), b = nodeSite(circuit.node_2);
  for (const [local, remote, localNode, remoteNode] of [
    [a, b, circuit.node_1, circuit.node_2], [b, a, circuit.node_2, circuit.node_1]
  ]) add(local, 'existing_circuits', {
    local_node: localNode, remote_node: remoteNode,
    local_voltage_kv: nodes.get(localNode)?.voltage_consistent_with_site === true
      ? nodes.get(localNode).voltage_kv : null,
    remote_voltage_kv: nodes.get(remoteNode)?.voltage_consistent_with_site === true
      ? nodes.get(remoteNode).voltage_kv : null,
    remote_site: remote ? siteSummary(remote) : null,
    ohl_km: circuit.ohl_km, cable_km: circuit.cable_km,
    circuit_type: circuit.circuit_type,
    impedance_pct_100mva: { r: circuit.r_pct_100mva, x: circuit.x_pct_100mva, b: circuit.b_pct_100mva },
    seasonal_rating_mva: { winter: circuit.winter_mva ?? null, spring: circuit.spring_mva ?? null,
      summer: circuit.summer_mva ?? null, autumn: circuit.autumn_mva ?? null }
  });
}
for (const transformer of network.transformers) {
  const a = nodeSite(transformer.node_1), b = nodeSite(transformer.node_2);
  for (const code of new Set([a, b].filter(Boolean))) add(code, 'transformers', {
    node_1: transformer.node_1, node_2: transformer.node_2,
    voltage_1_kv: nodes.get(transformer.node_1)?.voltage_kv ?? null,
    voltage_2_kv: nodes.get(transformer.node_2)?.voltage_kv ?? null,
    impedance_pct_100mva: { r: transformer.r_pct_100mva, x: transformer.x_pct_100mva,
      b: transformer.b_pct_100mva }, rating_mva: transformer.rating_mva
  });
}
for (const unit of network.reactive_compensation) {
  const code = nodeSite(unit.node);
  add(code, 'reactive_compensation', { node: unit.node, unit: unit.unit, type: unit.type,
    connection_kv: unit.connection_kv, mvar_generation: unit.mvar_generation,
    mvar_absorption: unit.mvar_absorption });
}
for (const link of network.interconnectors) {
  for (const code of new Set([nodeSite(link.node_1), nodeSite(link.node_2)].filter(Boolean))) {
    add(code, 'interconnectors', link);
  }
}
for (const change of network.planned_changes) {
  const a = nodeSite(change.node_1), b = nodeSite(change.node_2);
  const landings = a === b
    ? [[a, change.node_1, change.node_2, b]]
    : [[a, change.node_1, change.node_2, b], [b, change.node_2, change.node_1, a]];
  for (const [code, localNode, remoteNode, remoteCode] of landings) if (code) {
    add(code, 'planned_changes', {
      ...change,
      local_node: localNode, remote_node: remoteNode,
      local_voltage_kv: nodes.get(localNode)?.voltage_consistent_with_site === true
        ? nodes.get(localNode).voltage_kv : null,
      remote_voltage_kv: nodes.get(remoteNode)?.voltage_consistent_with_site === true
        ? nodes.get(remoteNode).voltage_kv : null,
      remote_site: siteSummary(remoteCode)
    });
  }
}
for (const record of bySite.values()) {
  for (const field of [
    'existing_circuits', 'transformers', 'reactive_compensation', 'interconnectors', 'planned_changes'
  ]) record[field].sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
  record.projection_reconciliation = {
    planned_changes_published: record.published_site_summary.planned_changes,
    planned_change_appearances: record.planned_changes.length,
    unresolved_planned_change_appearances: Math.max(0,
      record.published_site_summary.planned_changes - record.planned_changes.length)
  };
}

const connectionPoints = [...bySite.values()].sort((a, b) => a.site_code.localeCompare(b.site_code));
const product = {
  schema: 'data-grid-gb.map-click-network.v1',
  source: {
    network: { schema: network.schema, sha256: sha(networkBytes) },
    connection_points: { schema: points.schema, sha256: sha(pointsBytes) },
    publisher: 'NESO ETYS 2025; geometry from OpenStreetMap contributors via GridAtlas'
  },
  purpose: 'one-hop published network context for a selected connection point',
  claim_boundary: 'Topology, parameters, ratings, fault current and planned changes are published facts. This is not solved power flow, available headroom, queue position, a connection offer or a connection assessment.',
  impedance_base: 'percent on 100 MVA',
  counts: {
    connection_points: connectionPoints.length,
    located: connectionPoints.filter(point => point.location).length,
    circuit_appearances: connectionPoints.reduce((n, point) => n + point.existing_circuits.length, 0),
    transformer_appearances: connectionPoints.reduce((n, point) => n + point.transformers.length, 0),
    planned_change_appearances: connectionPoints.reduce((n, point) => n + point.planned_changes.length, 0)
  },
  connection_points: connectionPoints
};
const text = canonical(product);
await writeFile(outputPath, text, 'utf8');
await writeFile(`${outputPath}.sha256`, `${sha(Buffer.from(text))}  map-click-network.v1.json\n`, 'utf8');
console.log(JSON.stringify({ status: 'BUILT', ...product.counts, bytes: Buffer.byteLength(text) }, null, 2));
