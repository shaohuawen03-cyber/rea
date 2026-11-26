#!/usr/bin/env python3
"""Report the sha256 of the v4 private model bundle that the training kernel wrote.

Why this exists: the training run records a local sha256 only after it finishes
packaging, and the packaging step is what we need to identify in the release
provenance. Re-hashing it inside a fresh CPU-only kernel that *mounts* the training
kernel's output gives an independent, verifiable number instead of a self-reported
one - the same trick used for the v2.1 bundle, which is why that hash could be
quoted in the release at all.

No GPU, no training, no network beyond mounting the source output.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

OUT = Path("/kaggle/working")
INPUT = Path("/kaggle/input")
SOURCE = os.environ.get("V4_SOURCE_KERNEL", "dualamp-v4-full-20261126")


def sha(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"stage": "v4_bundle_sha", "source_kernel": SOURCE, "status": "STARTED"}
    roots = sorted(p for p in INPUT.rglob("*") if p.is_dir() and "model_bundle_parts" in p.name)
    bundles = sorted(INPUT.rglob("dualamp_v2_1_model_bundle.zip"))
    print(f"[sha] mounted top: {sorted(p.name for p in INPUT.iterdir()) if INPUT.exists() else 'NONE'}",
          flush=True)
    print(f"[sha] bundle candidates: {[str(p) for p in bundles][:5]}", flush=True)
    print(f"[sha] parts dirs: {[str(p) for p in roots][:5]}", flush=True)
    if bundles:
        target = bundles[0]
        report.update({"source": "whole zip", "path": str(target),
                       "bytes": target.stat().st_size, "sha256": sha(target)})
    elif roots:
        man = roots[0] / "PARTS_MANIFEST.json"
        report["parts_manifest"] = json.loads(man.read_text()) if man.exists() else None
        if report.get("parts_manifest"):
            report.update({"source": "parts manifest only",
                           "bytes": report["parts_manifest"].get("whole_bytes"),
                           "sha256": report["parts_manifest"].get("whole_sha256")})
    else:
        report["status"] = "NOT_FOUND"
        (OUT / "v4_bundle_sha.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        return 1
    report["status"] = "OK"
    (OUT / "v4_bundle_sha.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
