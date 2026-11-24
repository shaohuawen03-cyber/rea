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


def kaggle_username(log=print) -> str:
    """Username for the kernel id, with every fallback logged (no secrets)."""
    path = os.path.expanduser("~/.kaggle/kaggle.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                name = (json.load(f).get("username") or "").strip()
            if name:
                log(f"username from kaggle.json: {name}")
                return name
        except (OSError, json.JSONDecodeError) as exc:
            log(f"kaggle.json unreadable: {type(exc).__name__}")
    else:
        log("no ~/.kaggle/kaggle.json (bare API token mode)")

    name = (os.environ.get("KAGGLE_USERNAME") or "").strip()
    if name:
        log(f"username from env: {name}")
        return name

    # `kernels list -m` returns rows whose `ref` is "<username>/<slug>".
    listing = run(["kaggle", "kernels", "list", "-m", "--page-size", "5", "--format", "json"], timeout=180)
    if listing.returncode == 0:
        text = (listing.stdout or "").strip()
        try:
            rows = json.loads(text) if text.startswith("[") else []
        except json.JSONDecodeError:
            rows = []
        for row in rows:
            ref = str(row.get("ref") or "")
            if "/" in ref:
                name = ref.split("/", 1)[0].strip()
                if name:
                    log(f"username from kernels list: {name}")
                    return name
        log(f"kernels list gave no refs ({len(rows)} rows)")
    else:
        log(f"kernels list failed rc={listing.returncode}: {(listing.stderr or listing.stdout).strip()[:300]}")

    # Last resort: let the CLI introspect the access token.
    probe = run(
        [
            sys.executable,
            "-c",
            "import json;from kaggle.api.kaggle_api_extended import KaggleApi;"
            "a=KaggleApi();a.authenticate();print(json.dumps(a.config_values.get('username','')))",
        ],
        timeout=120,
    )
    if probe.returncode == 0:
        for line in reversed((probe.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    name = (json.loads(line).get("username") or "").strip()
                except json.JSONDecodeError:
                    name = ""
                if name:
                    log(f"username from token introspection: {name}")
                    return name
    else:
        log(f"token introspection failed rc={probe.returncode}: {(probe.stderr or '')[-300:]}")
    return ""


def raw_session_status(owner: str, slug: str) -> str:
    """GetKernelSessionStatus body: `{}` means no session; a real queue has a body."""
    try:
        import requests

        token = ""
        for path in ("~/.kaggle/access_token", "~/.gpu-relay/kaggle_token"):
            full = os.path.expanduser(path)
            if not token and os.path.exists(full):
                token = open(full, "r", encoding="utf-8").read().strip()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.post(
            "https://api.kaggle.com/v1/kernels.KernelsApiService/GetKernelSessionStatus",
            json={"userName": owner, "kernelSlug": slug},
            headers=headers,
            timeout=45,
        )
        body = (resp.text or "").strip()
        return "no-session" if body in ("{}", "") else body[:300]
    except Exception as exc:  # noqa: BLE001
        return f"raw-error:{type(exc).__name__}"


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True, help="Kernel slug (lowercase, unique)")
    parser.add_argument("--out-dir", default="cloud-gpu/results")
    parser.add_argument("--prefix", default="quota-smoke")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--poll-timeout", type=int, default=3300, help="seconds to wait for the kernel (Kaggle GPU queues can be long)")
    parser.add_argument("--no-wait", action="store_true", help="push only; do not poll (keeps the workflow short)")
    parser.add_argument("--fetch-logs", action="store_true", help="pull the kernel session log after it finishes")
    parser.add_argument("--diagnose", action="store_true", help="also print kernel status + recent kernel list")
    args = parser.parse_args()

    if args.diagnose:
        user = kaggle_username(log=print)
        if user:
            ref = f"{user}/{args.slug}"
            status = run(["kaggle", "kernels", "status", ref], timeout=120)
            print(f"--- status {ref}\n{(status.stdout or status.stderr).strip()}", flush=True)
            listing = run(
                ["kaggle", "kernels", "list", "-m", "--page-size", "10", "--search", args.slug],
                timeout=180,
            )
            print(f"--- list {args.slug}\n{(listing.stdout or listing.stderr).strip()}", flush=True)
        else:
            print("--- no username for diagnosis", flush=True)
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, f"{args.prefix}-push.txt")

    def log(line: str) -> None:
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")

    open(log_path, "w", encoding="utf-8").close()
    user = kaggle_username(log)
    log(f"username={user!r} payload={PAYLOAD} seconds={args.seconds}")
    if not user:
        log("no Kaggle username available; skipping smoke kernel")
        return 0

    ref = f"{user}/{args.slug}"
    stage = tempfile.mkdtemp(prefix="quota_smoke_")
    try:
        # Kernel has no CLI args, so stage a wrapper that runs the payload with
        # the requested duration instead of appending a comment that does nothing.
        shutil.copy2(PAYLOAD, os.path.join(stage, "payload.py"))
        payload_args = ["--seconds", str(args.seconds)]
        wrapper = (
            "import runpy, sys\n"
            f"sys.argv = ['payload.py', *{payload_args!r}]\n"
            "runpy.run_path('payload.py', run_name='__main__')\n"
        )
        with open(os.path.join(stage, "entry_main.py"), "w", encoding="utf-8") as f:
            f.write(wrapper)
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
        pushed = run(["kaggle", "kernels", "push", "-p", stage, "--accelerator", "NvidiaTeslaT4"], timeout=300)
        log(f"push rc={pushed.returncode}")
        log((pushed.stdout or "").strip())
        log((pushed.stderr or "").strip())
        if pushed.returncode != 0:
            text = f"{pushed.stdout or ''}{pushed.stderr or ''}"
            if "Maximum batch GPU session count" in text:
                log("GPU_SESSION_LIMIT: the account already holds the maximum number of GPU sessions")
                log("(quota only moves when those sessions run/finish; free a slot and retry)")
            else:
                log("kaggle kernels push FAILED")
            return 0

        if args.no_wait:
            log(f"pushed (no-wait); kernel URL: https://www.kaggle.com/code/{ref}")
            with open(os.path.join(args.out_dir, f"{args.prefix}-url.txt"), "w", encoding="utf-8") as f:
                f.write(f"https://www.kaggle.com/code/{ref}\n")
            status_blob = {
                "kernel_ref": ref,
                "final_status": "pushed_no_wait",
                "raw_session_status": raw_session_status(user, slug),
                "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            try:
                with open(os.path.join(args.out_dir, f"{args.prefix}-status.json"), "w", encoding="utf-8") as f:
                    json.dump(status_blob, f, indent=2)
                    f.write("\n")
            except OSError as exc:
                log(f"could not write status file: {exc}")
            log(f"status json: {json.dumps(status_blob)}")
            return 0

        status = "queued"
        deadline = time.time() + args.poll_timeout
        while time.time() < deadline:
            time.sleep(20)
            probe = run(["kaggle", "kernels", "status", ref], timeout=60)
            status = (probe.stdout or probe.stderr).strip()
            raw = raw_session_status(user, slug)
            log(f"status: {status} | raw: {raw}")
            low = status.lower()
            if "complete" in low or "error" in low or "cancel" in low:
                break

        os.makedirs(args.out_dir, exist_ok=True)
        fetched = tempfile.mkdtemp(prefix="quota_out_")
        try:
            got = run(["kaggle", "kernels", "output", ref, "-p", fetched, "-o"], timeout=900)
            log(f"output rc={got.returncode} {(got.stdout or got.stderr).strip()[:800]}")
            log(f"files: {sorted(os.listdir(fetched)) if os.path.isdir(fetched) else 'none'}")
            for path in sorted(glob.glob(os.path.join(fetched, "**", "*"), recursive=True)):
                if not os.path.isfile(path):
                    continue
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                name = os.path.basename(path)
                shutil.copy2(path, os.path.join(args.out_dir, f"{args.prefix}-{name}"))
        finally:
            shutil.rmtree(fetched, ignore_errors=True)

        if args.fetch_logs:
            got = run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "kernel_logs.py"), ref,
                 "--tail", "6000",
                 "--out", os.path.join(args.out_dir, f"{args.prefix}-kernel.log")],
                timeout=300,
            )
            log(f"log fetch rc={got.returncode}: {(got.stdout or got.stderr).strip()[:800]}")

        if args.fetch_logs:
            logs = run(
                [
                    sys.executable,
                    os.path.join(os.path.dirname(__file__), "kernel_logs.py"),
                    ref,
                    "--tail",
                    "6000",
                    "--out",
                    os.path.join(args.out_dir, f"{args.prefix}-kernel.log"),
                ],
                timeout=300,
            )
            log(f"log fetch rc={logs.returncode}: {(logs.stdout or logs.stderr).strip()[:600]}")

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
