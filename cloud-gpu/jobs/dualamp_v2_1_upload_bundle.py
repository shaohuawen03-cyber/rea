#!/usr/bin/env python3
"""Upload an already-built private v2.1 model bundle with a fresh resumable session.

Why this exists: the upload session is single-use and expires, so a run whose session
was spent elsewhere must not re-train 90 GPU-minutes to ship 760 MB of weights. This
job reads the bundle a run left in its kernel output, verifies it against the report's
recorded sha256, and streams it to the private Drive session from EVIDENCE_LEDGER_UPLOAD_URI.

Nothing here touches git: the bundle is sequences + weights, which the runbook forbids
in the public repo.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

OUT = Path("/kaggle/working")
REPORT = os.environ.get("V21_REPORT_JSON", "v2_1_training_report.json")
BUNDLE = os.environ.get("V21_BUNDLE_ZIP", "dualamp_v2_1_model_bundle.zip")


def locate(name: str) -> Path | None:
    """Find a file in this kernel's output or in any mounted prior kernel output.

    Kernel output does not survive between runs, so the bundle is mounted from the
    training kernel via `kernel_sources` (read-only under /kaggle/input).
    """
    for root in (OUT, Path("/kaggle/input")):
        if not root.exists():
            continue
        direct = root / name
        if direct.exists():
            return direct
        hits = sorted(root.rglob(name))
        if hits:
            return hits[0]
    return None


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(uri: str, path: Path) -> dict:
    import requests

    size = path.stat().st_size
    offset, resp = 0, None
    with path.open("rb") as f:
        while offset < size:
            chunk = f.read(8 * 1024 * 1024)
            final = offset + len(chunk) >= size
            headers = {
                "Content-Type": "application/zip",
                "Content-Length": str(len(chunk)),
                "X-Goog-Upload-Offset": str(offset),
                "X-Goog-Upload-Command": "upload, finalize" if final else "upload",
                "X-Goog-Upload-Total-Size": str(size),
            }
            resp = requests.put(uri, headers=headers, data=chunk, timeout=600)
            status = resp.headers.get("X-Goog-Upload-Status", "")
            if resp.status_code not in (200, 201) or status not in ("active", "final"):
                raise RuntimeError(f"upload failed: HTTP {resp.status_code} status={status}")
            offset += len(chunk)
            print(f"[upload] {offset}/{size} bytes", flush=True)
    return {"bytes": size, "drive_file": resp.json() if resp is not None else {}}


def main() -> int:
    uri = (os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
    bundle = locate(BUNDLE)
    report_path = locate(REPORT)
    if bundle is None:
        raise SystemExit(f"no {BUNDLE} in {OUT} or /kaggle/input")
    if report_path is None:
        raise SystemExit(f"no {REPORT} in {OUT} or /kaggle/input")
    print(f"[locate] bundle={bundle} report={report_path}", flush=True)
    payload = json.loads(report_path.read_text())
    expected = ((payload.get("private_model_bundle") or {}).get("local_sha256") or "").strip()
    actual = sha(bundle)
    if expected and expected != actual:
        raise SystemExit(f"bundle sha256 mismatch: expected {expected}, got {actual}")
    result = {"bundle": BUNDLE, "bytes": bundle.stat().st_size, "sha256": actual,
              "expected_sha256": expected or "(not recorded)", "sha_match": bool(expected) and expected == actual}
    if not uri:
        result["skipped"] = "no upload session in env"
        print(json.dumps(result, indent=2), flush=True)
        raise SystemExit(1)
    result["upload"] = upload(uri, bundle)
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
