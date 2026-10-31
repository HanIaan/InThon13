"""Build Seoul percentile calibration for indicator scores from a precomputed snapshot.

Run from backend/: python -m scripts.calibrate_seoul <cell_indicators.sqlite>
Writes app/core/calibration_seoul.json: for each indicator/unit/time_slot the raw values at
the 5th, 10th, ..., 95th percentiles of Seoul cells. Scoring maps p5 -> 100 and p95 -> 0
(see core/scoring.py), so scores mean "relative position within Seoul".
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np

UNITS = {"noise": "dB", "light": "nW/cm2/sr", "crowd": "persons/km2"}
PCTS = list(range(5, 100, 5))


def main(snapshot: str) -> None:
    con = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    values: dict[tuple[str, str], list[float]] = {}
    for slot, payload in con.execute("select time_slot, payload from cell_indicators"):
        cell = json.loads(payload)
        for ind, unit in UNITS.items():
            v = cell[ind]
            if v["raw_value"] is not None and v.get("raw_unit") == unit and v["status"] in ("observed", "estimated"):
                values.setdefault((ind, slot), []).append(v["raw_value"])
    out = {"generated": date.today().isoformat(), "source": Path(snapshot).name, "percentiles": PCTS, "breakpoints": {}}
    for (ind, slot), vals in sorted(values.items()):
        out["breakpoints"].setdefault(ind, {"unit": UNITS[ind], "slots": {}})
        out["breakpoints"][ind]["slots"][slot] = {
            "n": len(vals), "values": [round(float(x), 3) for x in np.percentile(vals, PCTS)]}
    dest = Path(__file__).resolve().parents[1] / "app" / "core" / "calibration_seoul.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main(sys.argv[1])
