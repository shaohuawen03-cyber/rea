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
import subprocess
import sys


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("refs", nargs="*", help="kernel refs '<owner>/<slug>'")
    parser.add_argument("--mine", action="store_true", help="also list my recent kernels")
    parser.add_argument("--out", default="", help="write the report JSON here")
    args = parser.parse_args()

    report: dict = {"kernels": {}, "mine": []}
    for ref in args.refs:
        res = run(["kaggle", "kernels", "status", ref])
        status = (res.stdout or res.stderr).strip()
        report["kernels"][ref] = status
        print(f"{ref}: {status}")

    if args.mine:
        res = run(["kaggle", "kernels", "list", "-m", "--page-size", "15", "--format", "json"])
        text = (res.stdout or "").strip()
        rows = []
        if text.startswith("["):
            try:
                rows = json.loads(text)
            except json.JSONDecodeError:
                rows = []
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
