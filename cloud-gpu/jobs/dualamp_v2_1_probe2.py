#!/usr/bin/env python3
"""Probe 2: find the *working* bulk path for curated AMP records (CPU only).

Probe 1 showed GET on https://dbaasp.org/api/v1 returns an empty 200 body, i.e. the
API wants a POST. This run tries the documented POST form and, failing that, hunts
the bulk-download entries of DRAMP / APD3 so the v2.1 positive set can be rebuilt
from structured, experimentally annotated records instead of 16 full-text hits.

Nothing sequence-level is written to the public report: only counts, HTTP status,
column headers and the first non-sensitive field names.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request

OUT = "/kaggle/working"
UA = {"User-Agent": "Mozilla/5.0 (compatible; rea-v2-probe/1.0)"}


def _call(url: str, data: dict | None = None, timeout: int = 90) -> dict:
    payload = urllib.parse.urlencode(data).encode() if data else None
    headers = dict(UA)
    if payload:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(8_000_000)
            return {
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "bytes": len(raw),
                "raw": raw,
            }
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()[:2000]
        except Exception:  # noqa: BLE001
            pass
        return {"status": exc.code, "error": "HTTPError", "raw": body,
                "content_type": exc.headers.get("Content-Type", "") if exc.headers else ""}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}", "raw": b""}


def shape(result: dict, keep_preview: int = 600) -> dict:
    raw = result.pop("raw", b"")
    text = raw.decode("utf-8", "replace")
    out = {k: v for k, v in result.items()}
    out["preview"] = text[:keep_preview]
    body = text.lstrip()
    if body.startswith("{"):
        try:
            parsed = json.loads(text)
            out["json_top_keys"] = list(parsed)[:15] if isinstance(parsed, dict) else "list"
            first = next(iter(parsed.values()), None) if isinstance(parsed, dict) else None
            if isinstance(first, list) and first and isinstance(first[0], dict):
                out["json_item_keys"] = list(first[0])[:40]
                out["json_item_count"] = len(first)
            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                out["json_item_keys"] = list(parsed[0])[:40]
                out["json_item_count"] = len(parsed)
        except Exception as exc:  # noqa: BLE001
            out["json_error"] = str(exc)[:120]
    elif body.startswith("[") or body.startswith(">"):
        out["looks_like_fasta"] = body.startswith(">")
        out["first_line"] = body.splitlines()[0][:120] if body.strip() else ""
        out["line_count"] = body.count("\n")
    return out


def main() -> int:
    report: dict = {
        "stage": "v2_1_data_source_probe_2",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "probes": {},
    }

    # --- DBAASP: documented POST form -------------------------------------
    dbaasp_cases = {
        "dbaasp_post_lookup": ("https://dbaasp.org/api/v1",
                               {"query": "lookup", "lookup_type": "target_group", "format": "json"}),
        "dbaasp_post_card": ("https://dbaasp.org/api/v1",
                             {"query": "peptide_card", "peptide_id": "107", "format": "json"}),
        "dbaasp_post_ranking": ("https://dbaasp.org/api/v1",
                                {"query": "ranking_search", "target_species_id": "3232",
                                 "activity_measure_id": "72", "operation": "<=",
                                 "activity": "32", "format": "json"}),
        "dbaasp_post_search_seq": ("https://dbaasp.org/api/v1",
                                   {"query": "search", "sequence": "KRWWKWWRR", "format": "json"}),
    }
    for name, (url, data) in dbaasp_cases.items():
        report["probes"][name] = shape(_call(url, data))

    # --- DRAMP / APD3 bulk entry points ------------------------------------
    pages = {
        "dramp_downloads": "https://dramp.cpu-bioinfor.org/downloads/",
        "dramp_downloads2": "https://dramp.cpu-bioinfor.org/browse/downloads.php",
        "apd3_download": "https://aps.unmc.edu/downloads",
        "apd3_api": "https://aps.unmc.edu/api",
    }
    for name, url in pages.items():
        res = _call(url)
        raw = res.get("raw", b"")
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else ""
        entry = shape(res, keep_preview=200)
        links = sorted({m for m in re.findall(r'href="([^"]+)"', text)
                        if re.search(r"(?i)(download|\.txt|\.csv|\.xlsx|\.fasta|\.zip|api)", m)})
        entry["links"] = links[:25]
        report["probes"][name] = entry

    report["status"] = "PROBE_DONE"
    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "v2_1_probe2_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    for k, v in report["probes"].items():
        brief = {kk: vv for kk, vv in v.items() if kk != "preview"}
        print(f"[probe2] {k}: {json.dumps(brief, ensure_ascii=False)[:500]}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
