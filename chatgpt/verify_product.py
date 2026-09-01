#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    path = Path(sys.argv[1])
    raw = path.read_bytes()
    data = json.loads(raw)
    require(raw.endswith(b"\n"), "canonical product must end LF")
    require(data["schema"] == "data-grid-gb.etys.normalized.v1", "schema")
    require(data["claim_boundary"] == "topology_and_equipment_parameters_not_a_solved_power_flow_case", "claim boundary")
    require("Mobile is the sales surface; it must answer immediately." in
            (ROOT / "PRODUCT_CONTRACT.md").read_text(encoding="utf-8"), "mobile acceptance phrase")
    require(data["counts"]["sites"] == len(data["sites"]), "site count")
    require(data["counts"]["fault_scenarios"] == len(data["fault_scenarios"]), "fault count")
    cottam = [x for x in data["fault_scenarios"] if x["demand_case"] == "peak"
              and x["winter"] == "2025/26" and x["node"] == "COTT4 M1"]
    require(len(cottam) == 1, "exact Cottam peak scenario")
    require(abs(cottam[0]["three_phase_initial_peak_current_ka"] - 109.219270174868) < 1e-10,
            "Cottam exact metric semantics")
    require("three_phase_rms_break_current_ka" in cottam[0], "separate RMS metric")
    sidecar = path.with_suffix(path.suffix + ".sha256").read_text(encoding="ascii").split()[0]
    require(sidecar == hashlib.sha256(raw).hexdigest(), "sidecar hash")
    print(f"PASS {path} {sidecar} counts={data['counts']}")


if __name__ == "__main__":
    main()
