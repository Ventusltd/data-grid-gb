#!/usr/bin/env python3
"""Temporary issue #76 probe: inspect 2026 DUKES 5.11 headers and identified evidence rows."""
from __future__ import annotations
import hashlib
import io
import sys
import urllib.request
from openpyxl import load_workbook
URL = "https://assets.publishing.service.gov.uk/media/6a6a365b0ddb7e4831c629f3/DUKES_5.11.xlsx"
with urllib.request.urlopen(URL, timeout=60) as response:
    data = response.read(2_000_000)
digest = hashlib.sha256(data).hexdigest()
print("DUKES bytes", len(data), "sha256", digest)
if len(data) != 1247443 or not digest.startswith("760286f4cb3168f0"):
    raise SystemExit("DUKES source changed: refuse to inspect")
wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
print("SHEETS:", wb.sheetnames)
for ws in wb:
    print("===SHEET", repr(ws.title), "rows", ws.max_row, "cols", ws.max_column)
    for i, row in enumerate(ws.iter_rows(values_only=True), 1):
        vals = [(j + 1, str(v).strip()[:85]) for j, v in enumerate(row) if v is not None and str(v).strip()]
        if i <= 14 and vals:
            print("HEADER", i, vals[:24])
        if any(k in " ".join(str(v) for v in row if v is not None).upper() for k in ("MARCHWOOD", "KILROOT", "SIZEWELL B", "DRAX")):
            print("MATCH", i, vals[:24])
