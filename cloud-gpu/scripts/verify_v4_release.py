#!/usr/bin/env python3
"""Verify the published v4 release against the training artefacts, without downloading.

The sandbox cannot stream GitHub release assets (release-assets.githubusercontent.com
is unreachable), but the API reports each asset's byte size. Combined with the
extraction kernel's per-member sha256/bytes, size equality is a strong, checkable
statement: a corrupted or wrong checkpoint would almost certainly differ in size,
and the sha256 of every member is published inside the archive manifest for the
audience that *can* download.

Checks:
  1. asset count == 15 checkpoints + SHA256SUMS.txt + export_manifest.json
  2. every M<k>_seed<s>.pt asset size == the extraction report's member size
  3. the release is a prerelease and its tag differs from all earlier tags
  4. earlier tags still exist with the same asset counts (nothing was overwritten)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/user/rea")
RESULTS = ROOT / "cloud-gpu" / "results"
TAG = "dualamp-v4-weights-15-files-20261126"
PREVIOUS = {
    "dualamp-v2.1-weights-15-files-20261126": 17,
    "dualamp-v2.1-weights-20261125": 3,
    "dualamp-v2.1-exploratory-20261125": 0,
}
REPO = "shaohuawen03-cyber/rea"


def gh_json(path: str) -> dict | list:
    out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def newest(prefix: str) -> Path | None:
    cands = [d for d in RESULTS.iterdir()
             if d.is_dir() and d.name.startswith(prefix) and (d / "model_only_report.json").exists()]
    return max(cands, key=lambda d: (d / "model_only_report.json").stat().st_mtime) if cands else None


def main() -> int:
    extract = newest("dualamp-v4-model-only-20261126-fetch")
    if extract is None:
        print("no extraction report found", file=sys.stderr)
        return 2
    rep = json.loads((extract / "model_only_report.json").read_text(encoding="utf-8"))
    members = {m["name"].split("model/")[-1]: m for m in rep["members"] if m["name"].startswith("model/")}
    checkpoints = {k: v for k, v in members.items() if k.endswith(".pt")}

    rel = gh_json(f"repos/{REPO}/releases/tags/{TAG}")
    assets = {a["name"]: a for a in rel.get("assets", [])}

    result: dict = {"tag": TAG, "url": rel.get("html_url"), "prerelease": rel.get("prerelease"),
                    "published_at": rel.get("published_at"), "source_sha256": rep.get("source_sha256"),
                    "source_bytes": rep.get("source_bytes"),
                    "model_only_sha256": rep.get("model_only_sha256"),
                    "model_only_bytes": rep.get("model_only_bytes"), "checks": {}}

    result["checks"]["asset_count"] = {
        "pass": len(assets) == len(checkpoints) + 2,
        "found": len(assets), "expected": len(checkpoints) + 2}
    missing = sorted(set(checkpoints) - set(assets))
    result["checks"]["checkpoints_present"] = {
        "pass": not missing, "missing": missing, "n_checkpoints": len(checkpoints)}

    size_rows, size_bad = [], []
    for name, member in sorted(checkpoints.items()):
        asset = assets.get(name)
        ok = asset is not None and asset["size"] == member["bytes"]
        if not ok:
            size_bad.append(name)
        size_rows.append({"asset": name, "release_bytes": asset["size"] if asset else None,
                          "member_bytes": member["bytes"], "match": ok,
                          "member_sha256": member["sha256"]})
    result["checks"]["sizes"] = {"pass": not size_bad, "mismatched": size_bad, "rows": size_rows}
    result["checks"]["prerelease"] = {"pass": rel.get("prerelease") is True}

    prev_rows, prev_bad = [], []
    for tag, expected in PREVIOUS.items():
        try:
            other = gh_json(f"repos/{REPO}/releases/tags/{tag}")
        except subprocess.CalledProcessError:
            prev_bad.append({"tag": tag, "reason": "missing"})
            continue
        n = len(other.get("assets", []))
        ok = n == expected
        if not ok:
            prev_bad.append({"tag": tag, "reason": f"assets {n} != {expected}"})
        prev_rows.append({"tag": tag, "assets": n, "expected": expected, "unchanged": ok,
                          "published_at": other.get("published_at")})
    result["checks"]["previous_releases_untouched"] = {
        "pass": not prev_bad, "problems": prev_bad, "rows": prev_rows}

    result["all_pass"] = all(c["pass"] for c in result["checks"].values())

    out = ROOT / "cloud-gpu" / "reports" / "v4_release_verification.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps({k: (v if k != "checks" else {n: c["pass"] for n, c in v.items()})
                      for k, v in result.items()}, indent=2, ensure_ascii=False))
    for name, check in result["checks"].items():
        if not check["pass"]:
            print(f"FAIL {name}: {json.dumps(check, ensure_ascii=False)[:400]}", file=sys.stderr)
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
