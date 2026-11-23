#!/usr/bin/env python3
"""Kaggle accelerator-quota reader (weekly GPU/TPU hours).

Why this exists: the Arena sandbox cannot reach kaggle.com / api.kaggle.com, so
the quota can only be read from inside a GitHub Actions runner (the same relay
that drives the GPU kernels). This script is the read-only half of that relay.

Two independent sources, tried in order:

1. Raw Connect-RPC call (exact seconds, no rounding):
       POST https://api.kaggle.com/v1/kernels.KernelsApiService/GetAcceleratorQuotaStatistics
       Authorization: Bearer <KGAT_ API token>
       Body: {}
   Response shape (verified against the official `kaggle quota` CLI, v2.2.x):
       {"quotaRefreshTime": "2026-11-21T00:00:00Z",
        "gpuQuota": {"timeUsed": "8737.256s", "timeReserved": "0s",
                     "totalTimeAllowed": "108000s", ...},
        "tpuQuota": {...}}

2. `kaggle quota --format json` (what the installed CLI prints), parsed back into
   hours. Only used when (1) is unavailable — it rounds to 0.01 h.

Nothing here writes credentials; the token is read from the environment
(``KAGGLE_API_TOKEN``) or from ``~/.kaggle/access_token`` and never printed.

Usage (inside the runner, or locally if kaggle.com is reachable):

    python cloud-gpu/scripts/kaggle_quota_report.py --label before --out q_before.json
    python cloud-gpu/scripts/kaggle_quota_report.py --label after  --out q_after.json \
        --compare q_before.json

``--compare`` adds a ``delta`` block (GPU seconds/hours consumed between the two
snapshots). Exit code is 0 even when the quota cannot be read: the caller
decides what to do with ``source == "none"``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.kaggle.com/v1/kernels.KernelsApiService/GetAcceleratorQuotaStatistics"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_token() -> str | None:
    """Bare API token from env or the CLI's access_token file (never logged)."""
    for key in ("KAGGLE_API_TOKEN", "KAGGLE_ACCESS_TOKEN"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    path = os.path.expanduser("~/.kaggle/access_token")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                value = f.read().strip()
        except OSError:
            return None
        if value:
            return value
    return None


def duration_seconds(value) -> float | None:
    """Protobuf-duration-ish value -> seconds ("8737.256s", 8737.256, {"seconds": 1})."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)s\s*", value)
        if match:
            return float(match.group(1))
        try:
            return float(value)
        except ValueError:
            return None
    if isinstance(value, dict) and "seconds" in value:
        inner = duration_seconds(value["seconds"])
        if inner is None:
            return None
        return inner + float(value.get("nanos", 0) or 0) / 1e9
    return None


def hours(value) -> str | None:
    seconds = duration_seconds(value)
    return None if seconds is None else f"{seconds / 3600:.2f}h"


def fetch_direct(token: str, timeout: int = 60) -> dict:
    """Exact-seconds read via the documented Connect-RPC endpoint."""
    body = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_cli(timeout: int = 90) -> dict:
    """`kaggle quota --format json`, normalised to the same report shape."""
    if not shutil.which("kaggle"):
        raise RuntimeError("kaggle CLI not installed")
    proc = subprocess.run(
        ["kaggle", "quota", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text.startswith("["):
        raise RuntimeError(f"kaggle quota failed: {(proc.stderr or text).strip()[:300]}")
    rows = json.loads(text)

    def row(resource: str) -> dict | None:
        for item in rows:
            if str(item.get("resource", "")).upper() == resource:
                return item
        return None

    out: dict = {"quotaRefreshTime": None}
    for name, key in (("GPU", "gpuQuota"), ("TPU", "tpuQuota")):
        item = row(name)
        if not item:
            continue
        used = float(str(item.get("used", "0")).rstrip("h") or 0) * 3600
        total = float(str(item.get("total", "0")).rstrip("h") or 0) * 3600
        out[key] = {
            "timeUsed": f"{used:.0f}s",
            "totalTimeAllowed": f"{total:.0f}s",
            "timeReserved": "0s",
        }
        out["quotaRefreshTime"] = out["quotaRefreshTime"] or item.get("refreshAt")
    if "gpuQuota" not in out and "tpuQuota" not in out:
        raise RuntimeError("kaggle quota returned no accelerator rows")
    return out


def normalise(raw: dict, source: str, label: str) -> dict:
    """Raw endpoint payload -> the report the workflow commits."""
    report: dict = {
        "label": label,
        "captured_at_utc": utc_now(),
        "source": source,
        "refresh_at": raw.get("quotaRefreshTime") or raw.get("quota_refresh_time"),
        "accelerators": {},
        "raw": raw,
    }
    for key, name in (("gpuQuota", "gpu"), ("tpuQuota", "tpu")):
        quota = raw.get(key)
        if not isinstance(quota, dict):
            continue
        used = duration_seconds(quota.get("timeUsed"))
        total = duration_seconds(quota.get("totalTimeAllowed"))
        reserved = duration_seconds(quota.get("timeReserved")) or 0.0
        if used is None or total is None:
            continue
        report["accelerators"][name] = {
            "used_seconds": round(used, 3),
            "used_hours": round(used / 3600, 4),
            "reserved_seconds": round(reserved, 3),
            "total_seconds": round(total, 3),
            "total_hours": round(total / 3600, 4),
            "remaining_seconds": round(max(total - used - reserved, 0.0), 3),
            "remaining_hours": round(max(total - used - reserved, 0.0) / 3600, 4),
        }
    return report


def collect(label: str) -> dict:
    errors: list[str] = []
    token = read_token()
    if token:
        try:
            return normalise(fetch_direct(token), "api.kaggle.com (exact seconds)", label)
        except urllib.error.HTTPError as exc:
            errors.append(f"direct: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001 - any transport failure falls back to the CLI
            errors.append(f"direct: {type(exc).__name__}: {exc}")
    else:
        errors.append("direct: no KAGGLE_API_TOKEN / ~/.kaggle/access_token")
    try:
        return normalise(fetch_cli(), "kaggle CLI (0.01 h)", label)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cli: {type(exc).__name__}: {exc}")
    return {
        "label": label,
        "captured_at_utc": utc_now(),
        "source": "none",
        "accelerators": {},
        "errors": errors,
    }


def compare(after: dict, before: dict) -> dict:
    delta: dict = {"from": before.get("captured_at_utc"), "to": after.get("captured_at_utc"), "accelerators": {}}
    for name, aft in (after.get("accelerators") or {}).items():
        bef = (before.get("accelerators") or {}).get(name)
        if not bef:
            continue
        used = aft["used_seconds"] - bef["used_seconds"]
        delta["accelerators"][name] = {
            "used_seconds_delta": round(used, 3),
            "used_hours_delta": round(used / 3600, 4),
            "used_seconds_before": bef["used_seconds"],
            "used_seconds_after": aft["used_seconds"],
            "remaining_hours_before": bef["remaining_hours"],
            "remaining_hours_after": aft["remaining_hours"],
        }
    return delta


def main() -> int:
    parser = argparse.ArgumentParser(description="Read the weekly Kaggle GPU/TPU accelerator quota.")
    parser.add_argument("--label", default="snapshot", help="label for this snapshot (before/after/...)")
    parser.add_argument("--out", default="", help="write the report JSON here")
    parser.add_argument("--compare", default="", help="earlier report JSON; adds a delta block")
    parser.add_argument("--summary-out", default="", help="write a trimmed summary (latest snapshot) here")
    args = parser.parse_args()

    report = collect(args.label)
    if args.compare and os.path.exists(args.compare):
        try:
            with open(args.compare, "r", encoding="utf-8") as f:
                report["delta"] = compare(report, json.load(f))
        except Exception as exc:  # noqa: BLE001
            report["delta_error"] = f"{type(exc).__name__}: {exc}"

    blob = json.dumps(report, ensure_ascii=False, indent=2)
    print(blob, flush=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(blob + "\n")

    if args.summary_out:
        acc = report.get("accelerators") or {}
        summary = {
            "label": args.label,
            "captured_at_utc": report.get("captured_at_utc"),
            "refresh_at": report.get("refresh_at"),
            "source": report.get("source"),
            "gpu": acc.get("gpu"),
            "tpu": acc.get("tpu"),
            "delta": report.get("delta"),
            "errors": report.get("errors"),
        }
        with open(args.summary_out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
            f.write("\n")

    gpu = (report.get("accelerators") or {}).get("gpu")
    if gpu:
        delta = ((report.get("delta") or {}).get("accelerators") or {}).get("gpu")
        line = (
            f"GPU {gpu['used_hours']:.2f}h used / {gpu['total_hours']:.2f}h allowed "
            f"({gpu['remaining_hours']:.2f}h left, refresh {report.get('refresh_at')})"
        )
        if delta:
            line += f" | since {report['delta']['from']}: +{delta['used_hours_delta']:.4f}h"
        print(line, flush=True)
        return 0
    print("quota unavailable (see errors)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
