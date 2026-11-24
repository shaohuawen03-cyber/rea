#!/usr/bin/env python3
"""Probe 4: diagnose why the v2.1 filters keep 0 DRAMP records (CPU only).

Parses the DRAMP 3.0 TSV dumps and reports, per filter stage, how many records survive,
plus redacted examples of the `Activity` / `Pubmed_ID` shapes so the rule can be fixed
without ever publishing a sequence.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import sys
import traceback
import urllib.request

OUT = "/kaggle/working"
UA = {"User-Agent": "Mozilla/5.0 (compatible; rea-v21-probe4/1.0)",
      "Referer": "https://dramp.cpu-bioinfor.org/downloads/"}
BASE = "https://dramp.cpu-bioinfor.org/downloads/download_data/DRAMP3.0_new/{}"
AA = set("ACDEFGHIKLMNPQRSTVWY")
NUM = re.compile(r"\d")
UNIT = re.compile(r"(?i)(µg|μg|ug|mg|nm|µm|um|micromol|nmol)\s*/\s*(ml|l)")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def main() -> int:
    report: dict = {"stage": "v2_1_filter_diag", "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "datasets": {}}
    for fname in ("Antibacterial_amps.txt", "Anti-Gram-positive_amps.txt", "Anti-Gram-_amps.txt"):
        blob = fetch(BASE.format(fname))
        text = blob.decode("utf-8", "replace")
        reader = csv.DictReader(text.splitlines(), delimiter="\t")
        rows = list(reader)
        fields = reader.fieldnames or []
        stats = {"fields": fields, "records": len(rows),
                 "seq_standard_5_50": 0, "activity_nonempty": 0, "activity_numeric": 0,
                 "activity_unit": 0, "pmid_numeric": 0, "all_keep": 0}
        act_samples: list[str] = []
        pmid_samples: list[str] = []
        for row in rows:
            seq = re.sub(r"[^A-Za-z]", "", row.get("Sequence", "") or "").upper()
            ok_seq = 5 <= len(seq) <= 50 and not (set(seq) - AA)
            act = (row.get("Activity") or "").strip()
            pmid = (row.get("Pubmed_ID") or "").strip()
            if ok_seq:
                stats["seq_standard_5_50"] += 1
            if act:
                stats["activity_nonempty"] += 1
                if len(act_samples) < 15:
                    act_samples.append(act[:160])
            if act and NUM.search(act):
                stats["activity_numeric"] += 1
            if act and UNIT.search(act):
                stats["activity_unit"] += 1
            if re.search(r"\d", pmid):
                stats["pmid_numeric"] += 1
            if len(pmid_samples) < 8 and pmid:
                pmid_samples.append(pmid[:60])
            if ok_seq and act and NUM.search(act) and re.search(r"\d", pmid):
                stats["all_keep"] += 1
        stats["activity_samples"] = act_samples
        stats["pmid_samples"] = pmid_samples
        report["datasets"][fname] = stats
        print(f"[diag] {fname}: {json.dumps({k: v for k, v in stats.items() if 'samples' not in k})[:400]}\n"
              f"        activity samples: {act_samples[:6]}\n        pmid samples: {pmid_samples[:4]}", flush=True)
    report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "v2_1_filter_diag.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
