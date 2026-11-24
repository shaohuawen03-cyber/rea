#!/usr/bin/env python3
"""Download a kernel's output files (incl. kernel.log) for a finished run.

`kaggle kernels output` needs a live session; for an already-finished kernel the
file listing endpoint works and is what this uses. Files are written under
``<out-dir>/<ref-slug>/`` and a short summary is printed, so a failed smoke kernel
can be explained from the workflow log instead of guesswork.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys
import tempfile


def fetch_session_log(owner: str, slug: str, dest: str, tail_chars: int = 20000) -> bool:
    """Stream a running session's log and keep its tail.

    While a kernel is still RUNNING the output listing is empty, so the only way to
    see progress is the session log stream. The stream is FileDownload-wrapped; write
    it to a file and read the tail instead of decoding an in-memory Response.
    """
    from kaggle.api.kaggle_api_extended import KaggleApi
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionLogsStreamRequest

    api = KaggleApi()
    api.authenticate()
    request = ApiGetKernelSessionLogsStreamRequest()
    request.user_name = owner
    request.kernel_slug = slug
    tmp = tempfile.mkdtemp(prefix="kaglog_")
    try:
        with api.build_kaggle_client() as kaggle:
            kaggle.kernels.kernels_api_client.get_kernel_session_logs_stream(request, out_dir=tmp)
        chunks: list[str] = []
        for path in sorted(glob.glob(os.path.join(tmp, "*"))):
            with open(path, "rb") as f:
                chunks.append(f.read().decode("utf-8", "replace"))
        text = "".join(chunks).strip()
        if not text:
            print("[log] session log stream returned nothing")
            return False
        print(f"--- {owner}/{slug} live log (tail {tail_chars} chars) ---")
        print(text[-tail_chars:])
        os.makedirs(dest, exist_ok=True)
        with open(os.path.join(dest, "kernel.log"), "w", encoding="utf-8") as f:
            f.write(text)
        print(f"   saved {os.path.join(dest, 'kernel.log')} ({len(text)} chars)")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[log] stream failed: {type(exc).__name__}: {str(exc)[:200]}")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    listing.user_name = owner
    listing.kernel_slug = slug
    with api.build_kaggle_client() as kaggle:
        response = kaggle.kernels.kernels_api_client.list_kernel_session_output(listing)

    files = getattr(response, "files", []) or []
    kernel_log = getattr(response, "log", "") or ""
    if kernel_log:
        print(f"--- {args.ref} log (tail) ---")
        print(kernel_log[-3000:])
        path = os.path.join(dest, "kernel.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write(kernel_log)
        print(f"   saved {path}")
    if not kernel_log:
        fetch_session_log(owner, slug, dest)
    print(f"{args.ref}: {len(files)} file(s)")
    for item in files:
        name = getattr(item, "file_name", "") or getattr(item, "name", "")
        url = getattr(item, "url", "") or ""
        print(f" - {name} url={'yes' if url else 'no'}")
        if not name:
            continue
        path = os.path.join(dest, os.path.basename(name))
        text = ""
        # Preferred: the listing already carries a pre-signed download URL.
        if url:
            try:
                import urllib.request

                with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
                    raw = resp.read(args.max_bytes + 1)
                text = raw.decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                print(f"   url download failed: {type(exc).__name__}: {str(exc)[:200]}")
        if not text:
            request = ApiDownloadKernelOutputRequest()
            request.owner_slug = owner
            request.kernel_slug = slug
            request.file_path = name
            try:
                with api.build_kaggle_client() as kaggle:
                    stream = kaggle.kernels.kernels_api_client.download_kernel_output(request)
                if hasattr(stream, "text"):
                    text = stream.text or ""
                elif isinstance(stream, (bytes, bytearray)):
                    text = bytes(stream).decode("utf-8", "replace")
                if not text:
                    content = getattr(stream, "content", None)
                    if isinstance(content, (bytes, bytearray)):
                        text = bytes(content).decode("utf-8", "replace")
            except Exception as exc:  # noqa: BLE001
                print(f"   api download failed: {type(exc).__name__}: {str(exc)[:200]}")
        if not text:
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"   saved {path} ({len(text)} chars)")
        if name.endswith((".log", ".json", ".txt")):
            print(f"   --- {name} (tail) ---")
            print(text[-2000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
