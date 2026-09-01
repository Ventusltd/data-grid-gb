# ChatGPT feed lane

This directory is an independent, collision-free contribution lane for the
shared `data-grid-gb` build. It turns authoritative NESO ETYS workbooks into a
deterministic, provenance-carrying data product.

It deliberately distinguishes:

- topology and equipment parameters from a solved power-flow case;
- every fault-current metric from every other metric;
- present equipment from planned changes;
- authoritative electrical identity from separately sourced map coordinates.

No coordinate is inferred from an ETYS site code. No project-to-substation
relationship is inferred from proximity.

## Build

```powershell
python chatgpt/ingest_etys.py `
  --appendix-b path/to/ETYS2025-AppendixB-system-technical-data.xlsx `
  --fault-peak path/to/ETYS2025-AppendixD-fault-levels-peak.xlsx `
  --fault-minimum path/to/ETYS2025-AppendixD-fault-levels-minimum.xlsx `
  --output chatgpt/derived/etys-2025.normalized.json
```

The command refuses unknown bytes, schema drift, duplicate semantic keys, and
unlabelled fault-current columns. Output is canonical UTF-8 JSON with LF and a
sidecar SHA-256 file.

## Acceptance

```powershell
python -m unittest discover -s chatgpt/tests -v
python chatgpt/verify_product.py chatgpt/derived/etys-2025.normalized.json
```

The verifier requires the exact phrase **Mobile is the sales surface; it must
answer immediately.** in the product contract, as requested, while keeping
network facts asynchronous and provenance-bound.
