#!/usr/bin/env python3
"""Download a kernel's output files (incl. kernel.log) for a finished run.

`kaggle kernels output` needs a live session; for an already-finished kernel the
file listing endpoint works and is what this uses. Files are written under
``<out-dir>/<ref-slug>/`` and a short summary is printed, so a failed smoke kernel
can be explained from the workflow log instead of guesswork.
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref", help="kernel ref '<owner>/<slug>'")
    parser.add_argument("--out-dir", default="cloud-gpu/results")
    parser.add_argument("--max-bytes", type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()

    owner, _, slug = args.ref.partition("/")
    if not owner or not slug:
        print("ref must look like '<owner>/<slug>'", file=sys.stderr)
        return 2

    dest = os.path.join(args.out_dir, f"kernel-output-{slug}")
    os.makedirs(dest, exist_ok=True)
    from kaggle.api.kaggle_api_extended import KaggleApi
    from kagglesdk.kernels.types.kernels_api_service import (
        ApiDownloadKernelOutputRequest,
        ApiListKernelSessionOutputRequest,
    )

    api = KaggleApi()
    api.authenticate()

    listing = ApiListKernelSessionOutputRequest()
    listing.owner_slug = owner
    listing.kernel_slug = slug
    with api.build_kaggle_client() as kaggle:
        response = kaggle.kernels.kernels_api_client.list_kernel_session_output(listing)

    print(f"[debug] response type={type(response).__name__}")
    try:
        import json as _json

        print(f"[debug] response json={_json.dumps(response.to_dict())[:2000]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[debug] to_dict failed: {type(exc).__name__}: {str(exc)[:200]}")

    files = getattr(response, "files", []) or []
    kernel_log = getattr(response, "log", "") or ""
    if kernel_log:
        print(f"--- {args.ref} log (tail) ---")
        print(kernel_log[-3000:])
        path = os.path.join(dest, "kernel.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(kernel_log)
        print(f"   saved {path}")
    print(f"{args.ref}: {len(files)} file(s)")
    for item in files:
        name = getattr(item, "name", "") or getattr(item, "file_name", "")
        size = getattr(item, "total_bytes", None) or getattr(item, "size", None)
        print(f" - {name} ({size} bytes)")
        if not name or (size and size > args.max_bytes):
            continue
        request = ApiDownloadKernelOutputRequest()
        request.owner_slug = owner
        request.kernel_slug = slug
        request.file_path = name
        try:
            with api.build_kaggle_client() as kaggle:
                stream = kaggle.kernels.kernels_api_client.download_kernel_output(request)
            text = ""
            if hasattr(stream, "text"):
                text = stream.text or ""
            elif isinstance(stream, (bytes, bytearray)):
                text = bytes(stream).decode("utf-8", "replace")
            if not text:
                content = getattr(stream, "content", None)
                if isinstance(content, (bytes, bytearray)):
                    text = bytes(content).decode("utf-8", "replace")
            path = os.path.join(dest, os.path.basename(name))
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"   saved {path}")
            if name.endswith((".log", ".json", ".txt")):
                print(f"   --- {name} (tail) ---")
                print(text[-1500:])
        except Exception as exc:  # noqa: BLE001
            print(f"   download failed: {type(exc).__name__}: {str(exc)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
