#!/usr/bin/env python3
"""Move the private model bundle out of a finished Kaggle kernel's output.

The bundle is ~760 MB, so ``gpu_ctl`` deliberately does not copy it into
``cloud-gpu/results`` (2 MiB cap, and the repo is public). This utility keeps only
the single zip, and hands it straight to a pre-authorized Google Drive resumable
session when one is present in the environment.

Usage (inside the relay workflow, after the run job):

    python cloud-gpu/scripts/stage_private_bundle.py \
        --ref shaohuawen/dualamp-v2-1-weights-r4-20261125 \
        --dest /tmp/private-bundle \
        --uri-env DRIVE_BUNDLE_UPLOAD_URI

Exit codes: 0 = bundle staged (and uploaded when a URI was available),
            3 = the kernel finished but carries no bundle,
            4 = no Kaggle credentials in this environment.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

BUNDLE_NAMES = ("dualamp_v2_1_model_bundle.zip",)


def sha256(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def locate(dest: Path) -> Path | None:
    for root, _, files in os.walk(dest):
        for name in files:
            if name in BUNDLE_NAMES:
                return Path(root) / name
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="kernel ref '<owner>/<slug>'")
    ap.add_argument("--dest", default="/tmp/private-bundle")
    ap.add_argument("--uri-env", default="", help="env var holding a resumable session URI")
    args = ap.parse_args()

    owner, _, slug = args.ref.partition("/")
    if not owner or not slug:
        print("--ref must look like '<owner>/<slug>'", file=sys.stderr)
        return 2

    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
    except ImportError:
        print("kaggle package missing; cannot stage the bundle", file=sys.stderr)
        return 4

    api = KaggleApi()
    try:
        api.authenticate()
    except Exception as exc:  # noqa: BLE001
        print(f"no Kaggle credentials: {type(exc).__name__}: {str(exc)[:160]}", file=sys.stderr)
        return 4

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[stage] downloading output of {args.ref} into {dest}", flush=True)
    # The CLI pulls every output file; only the bundle is kept afterwards so the
    # 2 MiB artefacts already collected by gpu_ctl are not duplicated here.
    import subprocess

    proc = subprocess.run(
        ["kaggle", "kernels", "output", args.ref, "-p", str(dest), "-o"],
        capture_output=True, text=True, timeout=3600,
    )
    print((proc.stdout or "")[-2000:], flush=True)
    if proc.returncode != 0:
        print(f"[stage] kaggle output failed rc={proc.returncode}: {(proc.stderr or '')[-600:]}",
              flush=True)

    bundle = locate(dest)
    if bundle is None:
        print("[stage] kernel output carries no dualamp_v2_1_model_bundle.zip", flush=True)
        return 3

    size = bundle.stat().st_size
    digest = sha256(bundle)
    print(f"[stage] bundle {bundle} bytes={size} sha256={digest}", flush=True)

    uri = os.environ.get(args.uri_env, "") if args.uri_env else ""
    if uri:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from drive_put import upload_resumable  # noqa: E402

        result = upload_resumable(uri, str(bundle), mime_type="application/zip")
        print(f"[stage] uploaded to Drive: {result}", flush=True)
    else:
        print(f"[stage] no session URI in ${args.uri_env}; bundle left at {bundle}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
