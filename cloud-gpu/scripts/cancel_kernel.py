#!/usr/bin/env python3
"""Cancel a Kaggle kernel session (frees a GPU slot) and report the result.

Why: Kaggle refuses new GPU pushes with "Maximum batch GPU session count of 2
reached" while other sessions hold the slots — including shells left open in the
web UI that are invisible to `kaggle kernels status`. This calls
`KernelsApiService/CancelKernelSession` for a specific ref.

Usage:
    python cloud-gpu/scripts/cancel_kernel.py shaohuawen/dualamp-real8-v2-retry1-20261123
    python cloud-gpu/scripts/cancel_kernel.py <ref> --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", help="kernel ref '<owner>/<slug>'")
    parser.add_argument("--dry-run", action="store_true", help="only print the resolved request")
    args = parser.parse_args()

    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.kernels.types.kernels_api_service import ApiCancelKernelSessionRequest
    except Exception as exc:  # noqa: BLE001
        print(f"kaggle SDK unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    owner, _, slug = args.ref.partition("/")
    if not owner or not slug:
        print("ref must look like '<owner>/<slug>'", file=sys.stderr)
        return 2

    api = KaggleApi()
    api.authenticate()

    request = ApiCancelKernelSessionRequest()
    for attr, value in (("owner_slug", owner), ("kernel_slug", slug)):
        if hasattr(request, attr):
            setattr(request, attr, value)

    payload = {k: v for k, v in vars(request).items() if not k.startswith("_")}
    print(f"cancel request: {json.dumps(payload, default=str)[:400]}", flush=True)
    if args.dry_run:
        return 0

    with api.build_kaggle_client() as kaggle:
        response = kaggle.kernels.kernels_api_client.cancel_kernel_session(request)
    print(f"cancel response: {response}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
