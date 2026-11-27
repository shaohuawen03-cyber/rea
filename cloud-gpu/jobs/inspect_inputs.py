#!/usr/bin/env python3
"""Print exactly what Kaggle mounted, so a NOT_FOUND can be diagnosed instead of guessed."""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("/kaggle/working")
INPUT = Path("/kaggle/input")


def main() -> int:
    report: dict = {"input.exists": INPUT.exists(), "top": []}
    if INPUT.exists():
        for p in sorted(INPUT.iterdir()):
            report["top"].append({"name": p.name, "is_dir": p.is_dir()})
            if p.is_dir():
                kids = sorted(c.name for c in p.iterdir())[:12]
                report.setdefault("children", {})[p.name] = kids
    hits = [str(p) for p in INPUT.rglob("*scaler*")] if INPUT.exists() else []
    report["scaler_hits"] = hits[:10]
    report["zip_hits"] = [str(p) for p in INPUT.rglob("*.zip")][:10] if INPUT.exists() else []
    (OUT / "inspect_inputs.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2)[:3000], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
