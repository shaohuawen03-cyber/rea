#!/usr/bin/env python3
"""Pull only the biophys scaler (and the OOF score tables) out of a finished run.

The external-benchmark job needs the StandardScaler that was fitted on the training
split: refitting it on the external data would silently change M3's inputs and make
its scores incomparable with the published numbers. This kernel mounts a finished
training output and copies just those small files, so no GPU and no retraining.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

OUT = Path("/kaggle/working")
INPUT = Path("/kaggle/input")
SOURCE = os.environ.get("SCALER_SOURCE_KERNEL", "dualamp-v4-full-20261126")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"stage": "extract_scaler", "source_kernel": SOURCE, "status": "STARTED",
              "files": []}
    direct = sorted(INPUT.rglob("features_biophys_scaler.joblib"))
    if direct:
        shutil.copy2(direct[0], OUT / "features_biophys_scaler.joblib")
        report["files"].append({"name": "features_biophys_scaler.joblib",
                                "bytes": direct[0].stat().st_size,
                                "sha256": sha(direct[0]), "source": "mounted file"})
    else:
        bundle = sorted(INPUT.rglob("dualamp_v2_1_model_bundle.zip"))
        if not bundle:
            report["status"] = "NOT_FOUND"
            (OUT / "extract_scaler_report.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report, indent=2), flush=True)
            return 1
        with zipfile.ZipFile(bundle[0]) as z:
            for name in z.namelist():
                if name.endswith("features_biophys_scaler.joblib"):
                    target = OUT / "features_biophys_scaler.joblib"
                    with z.open(name) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    report["files"].append({"name": "features_biophys_scaler.joblib",
                                            "bytes": target.stat().st_size,
                                            "sha256": sha(target),
                                            "source": f"{bundle[0].name}:{name}"})
                    break
    report["status"] = "OK" if report["files"] else "NOT_FOUND"
    (OUT / "extract_scaler_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["files"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
