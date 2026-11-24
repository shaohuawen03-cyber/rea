#!/usr/bin/env python3
"""Probe public AMP data sources from inside Kaggle (no GPU, no quota).

Why: v2 has only 16 strict positives because full-text verification is expensive.
The fix is to pull experimentally-measured peptide records from curated public
databases (sequence + MIC + target organism + primary reference), which is exactly
the evidence the auto rule wants. This probe checks reachability and response shape
before spending a GPU run on it. No sequences are written to the public report.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import traceback
import urllib.error
import urllib.request

OUT = "/kaggle/working"
UA = {"User-Agent": "Mozilla/5.0 (compatible; rea-v2-probe/1.0)"}


def get(url: str, timeout: int = 60) -> dict:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(4_000_000)
            return {
                "url": url[:160],
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "bytes": len(raw),
                "body_head": raw[:1200].decode("utf-8", "replace"),
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read()[:600].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        return {"url": url[:160], "status": exc.code, "error": "HTTPError", "body_head": body}
    except Exception as exc:  # noqa: BLE001
        return {"url": url[:160], "error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def main() -> int:
    report: dict = {
        "stage": "v2_1_data_source_probe",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "python": sys.version.split()[0],
        "probes": {},
    }
    probes = {
        "dbaasp_lookup": "https://dbaasp.org/api/v1?query=lookup&lookup_type=target_group&format=json",
        "dbaasp_card": "https://dbaasp.org/api/v1?query=peptide_card&peptide_id=107&format=json",
        "dbaasp_ranking": (
            "https://dbaasp.org/api/v1?query=ranking_search&target_species_id=3232"
            "&activity_measure_id=72&operation=%3C=&activity=0.5&format=json"
        ),
        "dbaasp_api_page": "https://dbaasp.org/api",
        "europepmc": (
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
            "?query=antimicrobial%20peptide%20MIC&format=json&pageSize=1"
        ),
        "ncbi_eutils": (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            "?db=pubmed&term=antimicrobial+peptide&retmode=json&retmax=1"
        ),
        "dramp": "https://dramp.cpu-bioinfor.org/",
        "apd3": "https://aps.unmc.edu/",
    }
    for name, url in probes.items():
        result = get(url)
        body = result.pop("body_head", "")
        result["body_head"] = body[:400]
        if body.lstrip().startswith("{"):
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    result["json_top_keys"] = list(parsed)[:12]
                    first = next(iter(parsed.values()), None)
                    if isinstance(first, list) and first and isinstance(first[0], dict):
                        result["json_item_keys"] = list(first[0])[:30]
            except Exception:  # noqa: BLE001
                pass
        report["probes"][name] = result
        shape = {k: v for k, v in result.items() if k != "body_head"}
        print(f"[probe] {name}: {json.dumps(shape, ensure_ascii=False)[:400]}", flush=True)

    report["reachable"] = {k: (v.get("status") == 200) for k, v in report["probes"].items()}
    report["status"] = "PROBE_DONE"
    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "v2_1_probe_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps(report["reachable"], indent=2), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
