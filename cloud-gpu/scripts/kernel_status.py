#!/usr/bin/env python3
"""Print status for a list of Kaggle kernels (and my recent kernels).

Read-only helper used by the gpu-quota workflow when `check_kernels` is present
in `cloud-gpu/jobs/quota-request.json`. It answers "who is holding the GPU
sessions?" when `kaggle kernels push` reports
"Maximum batch GPU session count of 2 reached".
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("refs", nargs="*", help="kernel refs '<owner>/<slug>'")
    parser.add_argument("--mine", action="store_true", help="also list my recent kernels")
    parser.add_argument("--out", default="", help="write the report JSON here")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="also call GetKernelSessionStatus directly: an empty {} means NO active session "
        "(the CLI then prints its default QUEUED, which looks like a queue but is not one)",
    )
    args = parser.parse_args()

    report: dict = {"kernels": {}, "mine": [], "raw_session_status": {}}
    for ref in args.refs:
        res = run(["kaggle", "kernels", "status", ref])
        status = (res.stdout or res.stderr).strip()
        report["kernels"][ref] = status
        print(f"{ref}: {status}")
        if args.raw:
            owner, _, slug = ref.partition("/")
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
                    timeout=60,
                )
                body = resp.text.strip()
                report["raw_session_status"][ref] = {"status_code": resp.status_code, "body": body}
                note = "NO ACTIVE SESSION (CLI's QUEUED is the default enum value)" if body in ("{}", "") else body[:300]
                print(f"  raw GetKernelSessionStatus: HTTP {resp.status_code} {note}")
            except Exception as exc:  # noqa: BLE001
                report["raw_session_status"][ref] = {"error": f"{type(exc).__name__}: {exc}"}

    if args.mine:
        res = run(["kaggle", "kernels", "list", "-m", "--page-size", "15", "--format", "json"])
        text = (res.stdout or "").strip()
        rows = []
        if text.startswith("["):
            try:
                rows = json.loads(text)
            except json.JSONDecodeError:
                rows = []
        refs = [row.get("ref") for row in rows if row.get("ref")]
        statuses = {}
        for ref in refs[:10]:
            res = run(["kaggle", "kernels", "status", ref])
            statuses[ref] = (res.stdout or res.stderr).strip()
            print(f"{ref}: {statuses[ref][:160]}")
        report["statuses"] = statuses
        report["mine"] = [
            {
                "ref": row.get("ref"),
                "lastRunTime": row.get("lastRunTime") or row.get("lastRunTimeFormatted"),
                "totalVotes": row.get("totalVotes"),
            }
            for row in rows
        ]
        for row in report["mine"]:
            print(f"- {row['ref']} lastRun={row['lastRunTime']}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
