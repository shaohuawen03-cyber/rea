#!/usr/bin/env python3
"""Fetch a Kaggle kernel's session logs (plain text) and report its final status.

`kaggle kernels status` only says COMPLETE/ERROR; the log tells you *why* a kernel
died. That matters for the quota smoke: the first attempts used in-place matmul
(``torch.matmul(a, b, out=c)`` with c overwritten) and may have hit a CUDA error,
so we always pull the log after a run.

Usage:
    python cloud-gpu/scripts/kernel_logs.py shaohuawen/quota-smoke-24 --out logs.txt
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", help="kernel ref '<owner>/<slug>'")
    parser.add_argument("--tail", type=int, default=8000, help="characters of log to keep")
    parser.add_argument("--out", default="", help="write the log text here")
    args = parser.parse_args()

    owner, _, slug = args.ref.partition("/")
    if not owner or not slug:
        print("ref must look like '<owner>/<slug>'", file=sys.stderr)
        return 2

    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        from kagglesdk.kernels.types.kernels_api_service import (
            ApiGetKernelSessionLogsStreamRequest,
            ApiGetKernelSessionStatusRequest,
        )

        api = KaggleApi()
        api.authenticate()

        # KaggleObject freezes in __init__: assign through the property setters.
        status_req = ApiGetKernelSessionStatusRequest()
        status_req.user_name = owner
        status_req.kernel_slug = slug
        lines: list[str] = []
        try:
            with api.build_kaggle_client() as kaggle:
                resp = kaggle.kernels.kernels_api_client.get_kernel_session_status(status_req)
            lines.append(f"status: {getattr(resp, 'status', None)} failure={getattr(resp, 'failure_message', '')!r}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"status unavailable: {type(exc).__name__}: {str(exc)[:200]}")

        log_req = ApiGetKernelSessionLogsStreamRequest()
        log_req.user_name = owner
        log_req.kernel_slug = slug
        try:
            with api.build_kaggle_client() as kaggle:
                stream = kaggle.kernels.kernels_api_client.get_kernel_session_logs_stream(log_req)
            text = ""
            for attr in ("content", "text", "body", "data"):
                value = getattr(stream, attr, None)
                if isinstance(value, (bytes, bytearray)):
                    text = bytes(value).decode("utf-8", "replace")
                    break
                if isinstance(value, str) and value:
                    text = value
                    break
            if not text:
                text = repr(stream)
            lines.append("--- log tail ---")
            lines.append(text[-args.tail :])
        except Exception as exc:  # noqa: BLE001
            lines.append(f"logs unavailable: {type(exc).__name__}: {str(exc)[:300]}")

        blob = "\n".join(lines)
        print(blob, flush=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(blob + "\n")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"kernel_logs failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
