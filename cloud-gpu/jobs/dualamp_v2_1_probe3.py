#!/usr/bin/env python3
"""Probe 3: work out DRAMP's real download mechanism (CPU only).

Probe 2 found the download page listing `download.php?filename=download_data/...` links
but absolute GETs on those return 404 from inside the kernel. This probe tries the
plausible variants (Referer header, path-relative, `downloads/download.php`, POST, and
the documented GitHub mirror) so the v2.1 build can stop guessing.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request

OUT = "/kaggle/working"
UA = {
    "User-Agent": "Mozilla/5.0 (compatible; rea-v21-probe3/1.0)",
    "Referer": "https://dramp.cpu-bioinfor.org/downloads/",
    "Accept": "*/*",
}
TARGET = "download_data/DRAMP3.0_new/Antibacterial_amps.txt"


def attempt(label: str, url: str, data: dict | None = None) -> dict:
    payload = urllib.parse.urlencode(data).encode() if data else None
    headers = dict(UA)
    if payload:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read(4096)
            return {
                "label": label,
                "url": url[:180],
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "location": resp.headers.get("Location", ""),
                "head": raw[:300].decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        return {"label": label, "url": url[:180], "status": exc.code, "error": "HTTPError"}
    except Exception as exc:  # noqa: BLE001
        return {"label": label, "url": url[:180],
                "error": f"{type(exc).__name__}: {str(exc)[:140]}"}


def main() -> int:
    base = "https://dramp.cpu-bioinfor.org"
    quoted = urllib.parse.quote(TARGET, safe="")
    attempts = [
        ("abs_download.php", f"{base}/download.php?filename={TARGET}", None),
        ("abs_quoted", f"{base}/download.php?filename={quoted}", None),
        ("subdir_download.php", f"{base}/downloads/download.php?filename={TARGET}", None),
        ("subdir_quoted", f"{base}/downloads/download.php?filename={quoted}", None),
        ("subdir_direct_path", f"{base}/downloads/{TARGET}", None),
        ("root_direct_path", f"{base}/{TARGET}", None),
        ("post_download.php", f"{base}/download.php", {"filename": TARGET}),
        ("post_downloads_download.php", f"{base}/downloads/download.php", {"filename": TARGET}),
        ("page_downloads", f"{base}/downloads/", None),
        ("github_mirror", "https://raw.githubusercontent.com/DRAMP-KSU/DRAMP/master/README.md", None),
    ]
    report = {"stage": "v2_1_dramp_probe", "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "attempts": []}
    for label, url, data in attempts:
        res = attempt(label, url, data)
        report["attempts"].append(res)
        print(f"[dramp] {json.dumps(res, ensure_ascii=False)[:300]}", flush=True)
    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["works"] = [a["label"] for a in report["attempts"]
                       if a.get("status") == 200 and "text/html" not in a.get("content_type", "")]
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "v2_1_dramp_probe.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("works:", report["works"], flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
