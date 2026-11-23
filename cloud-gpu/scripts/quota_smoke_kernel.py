#!/usr/bin/env python3
"""Push a tiny dependency-free GPU kernel and wait for it (quota-smoke helper).

Used by `.github/workflows/gpu-quota.yml`:

    python cloud-gpu/scripts/quota_smoke_kernel.py --slug quota-smoke-42 --out-dir cloud-gpu/results

It exists so the workflow can stay plain YAML (no multi-line heredocs inside a
`run:` block scalar). Exits 0 even when the kernel is skipped or fails: the quota
snapshot is the primary output, the smoke is only a way to make the balance move.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

PAYLOAD = os.path.join("cloud-gpu", "jobs", "quota_smoke.py")


def kaggle_username() -> str:
    path = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                name = (json.load(f).get("username") or "").strip()
            if name:
                return name
        except (OSError, json.JSONDecodeError):
            pass
    return (os.environ.get("KAGGLE_USERNAME") or "").strip()


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True, help="Kernel slug (lowercase, unique)")
    parser.add_argument("--out-dir", default="cloud-gpu/results")
    parser.add_argument("--prefix", default="quota-smoke")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--poll-timeout", type=int, default=1500)
    args = parser.parse_args()

    user = kaggle_username()
    if not user:
        print("no Kaggle username available; skipping smoke kernel", file=sys.stderr)
        return 0

    ref = f"{user}/{args.slug}"
    stage = tempfile.mkdtemp(prefix="quota_smoke_")
    try:
        shutil.copy2(PAYLOAD, os.path.join(stage, "entry_main.py"))
        metadata = {
            "id": ref,
            "title": " ".join(args.slug.split("-")),
            "code_file": "entry_main.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": "true",
            "enable_gpu": "true",
            "enable_internet": "false",
            "machine_shape": "NvidiaTeslaT4",
            "dataset_sources": [],
            "competition_sources": [],
            "kernel_sources": [],
            "model_sources": [],
        }
        with open(os.path.join(stage, "kernel-metadata.json"), "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        # The payload takes --seconds; kernels run it bare, so bake the value in.
        with open(os.path.join(stage, "entry_main.py"), "a", encoding="utf-8") as f:
            f.write(f"\n# quota-smoke hint: --seconds {args.seconds}\n")
        pushed = run(["kaggle", "kernels", "push", "-p", stage, "--accelerator", "NvidiaTeslaT4"], timeout=180)
        print((pushed.stdout or pushed.stderr).strip(), flush=True)
        if pushed.returncode != 0:
            return 0

        status = "queued"
        deadline = time.time() + args.poll_timeout
        while time.time() < deadline:
            time.sleep(20)
            probe = run(["kaggle", "kernels", "status", ref], timeout=60)
            status = (probe.stdout or probe.stderr).strip()
            print(f"status: {status}", flush=True)
            low = status.lower()
            if "complete" in low or "error" in low or "cancel" in low:
                break

        os.makedirs(args.out_dir, exist_ok=True)
        fetched = tempfile.mkdtemp(prefix="quota_out_")
        try:
            run(["kaggle", "kernels", "output", ref, "-p", fetched, "-o"], timeout=900)
            for path in sorted(glob.glob(os.path.join(fetched, "**", "*"), recursive=True)):
                if not os.path.isfile(path):
                    continue
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                name = os.path.basename(path)
                shutil.copy2(path, os.path.join(args.out_dir, f"{args.prefix}-{name}"))
        finally:
            shutil.rmtree(fetched, ignore_errors=True)

        with open(os.path.join(args.out_dir, f"{args.prefix}-url.txt"), "w", encoding="utf-8") as f:
            f.write(f"https://www.kaggle.com/code/{ref}\n")
        with open(os.path.join(args.out_dir, f"{args.prefix}-status.json"), "w", encoding="utf-8") as f:
            json.dump({"kernel_ref": ref, "final_status": status, "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, f, indent=2)
            f.write("\n")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
