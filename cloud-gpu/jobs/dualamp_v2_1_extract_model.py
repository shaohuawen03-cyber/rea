#!/usr/bin/env python3
"""Extract a model-only bundle from a finished weights kernel's output.

The v2.1 private archive is ~760 MB, but only ~24 MB of it is the thing anyone
needs to run inference: ``model/*.pt`` plus the OOF score tables. The rest is an
ESM embedding cache that can be recomputed.

This kernel does not train anything and needs no GPU: it mounts the delivery
kernel's output, pulls the zip out of it, keeps ``model/`` and the provenance
block, and writes a small self-describing archive to its own kernel output so the
weights are permanently downloadable without another training run.

Env:
    V21_SOURCE_KERNEL   slug of the kernel whose output holds the zip
                        (default: dualamp-v2-1-weights-r4-20261125)
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

OUT = Path("/kaggle/working")
INPUT = Path("/kaggle/input")
SOURCE = os.environ.get("V21_SOURCE_KERNEL", "dualamp-v2-1-weights-r4-20261125")


def sha(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def find_zip() -> Path | None:
    """The mounted kernel output may be nested one or two levels deep."""
    if not INPUT.exists():
        return None
    for root, dirs, files in os.walk(INPUT):
        for name in files:
            if name == "dualamp_v2_1_model_bundle.zip":
                return Path(root) / name
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report: dict = {"stage": "dualamp_v2_1_model_only_extract",
                    "source_kernel": SOURCE, "status": "STARTED"}
    try:
        print(f"[extract] mounted items: {sorted(p.name for p in INPUT.iterdir()) if INPUT.exists() else 'NONE'}",
              flush=True)
        # Preferred: the small bundle another kernel already produced.
        existing = None
        if INPUT.exists():
            for root, _, files in os.walk(INPUT):
                if "dualamp_model_only.zip" in files:
                    existing = Path(root) / "dualamp_model_only.zip"
                    break
        if existing is not None:
            import shutil
            shutil.copy2(existing, OUT / "dualamp_model_only.zip")
            report.update({"status": "COPIED_EXISTING", "bytes": existing.stat().st_size,
                           "sha256": sha(existing)})
            (OUT / "model_only_report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2), flush=True)
            return 0

        zip_path = find_zip()
        if zip_path is None:
            raise RuntimeError(f"no model bundle under {INPUT} (mounted: "
                               f"{sorted(p.name for p in INPUT.iterdir()) if INPUT.exists() else 'none'})")
        print(f"[extract] bundle {zip_path} bytes={zip_path.stat().st_size}", flush=True)

        staging = OUT / "model_only"
        staging.mkdir(parents=True, exist_ok=True)
        kept, manifest = [], []
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            print(f"[extract] members: {len(names)}", flush=True)
            for name in names:
                keep = (name.startswith("model/")
                        or name in ("run_provenance.json",)
                        or name.startswith("reports/"))
                if not keep or name.endswith("/"):
                    continue
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, target.open("wb") as dst:
                    while True:
                        buf = src.read(8 * 1024 * 1024)
                        if not buf:
                            break
                        dst.write(buf)
                kept.append(name)
                manifest.append({"name": name, "bytes": target.stat().st_size,
                                 "sha256": sha(target)})

        if not any(m["name"].endswith(".pt") for m in manifest):
            raise RuntimeError("no .pt checkpoint inside the archive")

        small = OUT / "dualamp_model_only.zip"
        with zipfile.ZipFile(small, "w", compression=zipfile.ZIP_STORED) as z:
            for item in sorted(staging.rglob("*")):
                if item.is_file():
                    z.write(item, str(item.relative_to(staging)))

        report.update({
            "status": "COMPLETED_MODEL_ONLY_EXTRACT",
            "source_bytes": zip_path.stat().st_size,
            "source_sha256": sha(zip_path),
            "model_only_bytes": small.stat().st_size,
            "model_only_sha256": sha(small),
            "members": manifest,
            "note": ("model/ carries the trained weights and OOF score tables; the 1.4 GB "
                     "features/ embedding cache is deliberately excluded because it is "
                     "recomputable from the same encoder."),
        })
        (OUT / "model_only_report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "members"}, indent=2), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        report.update({"status": "FAILED", "error": str(exc)[:500]})
        (OUT / "model_only_report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
