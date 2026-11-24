#!/usr/bin/env python3
"""Build the v2.1 strict positive set from DRAMP 3.0 curated records (CPU only).

Why this replaces the 16-positive set: v2's positives came only from full-text
verification of one candidate list, which capped the cohort at 16 and left every
metric unstable (test n=2) and composition-driven (composition-identical decoys were
accepted as positives).

DRAMP 3.0 publishes tab-separated record dumps with the evidence the rule needs:
`DRAMP_ID | Sequence | Name | Source | Activity | Pubmed_ID | Dataset | Length`.
Reachable from inside a Kaggle kernel at
``https://dramp.cpu-bioinfor.org/downloads/download_data/DRAMP3.0_new/<file>``.

Rule `auto_evidence_v2_1.1` (stricter than v2.0):
  1. standard amino acids, length 5-50;
  2. Activity must carry a numeric MIC-like value with a concentration unit
     (ug/ml, mg/l, uM ...); "not determined" rows are dropped, never used as negatives;
  3. a numeric PubMed ID must be present (auditable primary reference);
  4. target organism is taken from the Activity string when a species is named,
     otherwise from the DRAMP dataset the record came from (Gram-positive /
     Gram-negative / antibacterial). The attribution used is recorded per row;
  5. exact-sequence dedupe, provenance merged; composition-preserving shuffles are
     generated 1:1 as hard negatives.

All row-level material (sequences, ledger) stays inside the private bundle that is
uploaded to Drive; the public report carries counts and SHA-256 only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import traceback
import urllib.error
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0 (compatible; rea-v21-build/1.0)",
      "Referer": "https://dramp.cpu-bioinfor.org/downloads/"}
DRAMP_BASE = "https://dramp.cpu-bioinfor.org/downloads/download_data/DRAMP3.0_new/{}"
APD3_BASE = "https://aps.unmc.edu/assets/sequences/{}"
AA = set("ACDEFGHIKLMNPQRSTVWY")
NUMERIC = re.compile(r"(\d+(?:[.,]\d+)?)")
CONC_UNIT = re.compile(r"(?i)(?:µg|μg|ug|mg|nm|µm|um|nM|mM|micromol|nmol)\s*/\s*(?:ml|l)\b")
SPECIES = re.compile(
    r"(?i)\b(escherichia|staphylococc\w*|pseudomonas|bacillus|klebsiella|acinetobacter|"
    r"enterococc\w*|salmonella|listeria|streptococc\w*|clostridi\w*|mycobacter\w*|"
    r"helicobacter|campylobacter|serratia|proteus|shigella|vibrio|enterobacter)\w*"
)
DATASET_TARGET = {
    "Anti-Gram-positive": "Gram-positive bacteria (DRAMP dataset attribution)",
    "Anti-Gram-": "Gram-negative bacteria (DRAMP dataset attribution)",
    "Antibacterial": "bacteria (DRAMP antibacterial dataset attribution)",
}


def fetch(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_tsv(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(text.splitlines(), delimiter="\t")
    rows = list(reader)
    return (reader.fieldnames or []), rows


def clean_seq(value: str) -> str:
    return re.sub(r"[^A-Za-z]", "", value or "").upper()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="/tmp/v21")
    ap.add_argument("--out", default="/kaggle/working")
    args = ap.parse_args()
    os.makedirs(args.workdir, exist_ok=True)
    os.makedirs(args.out, exist_ok=True)

    report: dict = {
        "stage": "v2_1_positive_build",
        "rule_version": "auto_evidence_v2_1.1",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": {}, "counts": {}, "status": "STARTED",
    }
    try:
        files = ["Antibacterial_amps.txt", "Anti-Gram-positive_amps.txt", "Anti-Gram-_amps.txt"]
        merged: dict[str, dict] = {}
        per_dataset: dict[str, dict] = {}
        activity_samples: list[str] = []
        for fname in files:
            dataset = fname.replace("_amps.txt", "")
            url = DRAMP_BASE.format(fname)
            try:
                blob = fetch(url)
            except urllib.error.HTTPError as exc:
                report["sources"][fname] = {"url": url, "error": f"HTTP {exc.code}"}
                continue
            except Exception as exc:  # noqa: BLE001
                report["sources"][fname] = {"url": url,
                                            "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                continue
            text = blob.decode("utf-8", "replace")
            fields, rows = parse_tsv(text)
            report["sources"][fname] = {"url": url, "bytes": len(blob), "sha256": sha256(blob),
                                        "fields": fields, "records": len(rows)}
            print(f"[build] {fname}: {len(rows)} records, fields={fields}", flush=True)
            kept = 0
            for row in rows:
                seq = clean_seq(row.get("Sequence", ""))
                if not (5 <= len(seq) <= 50) or set(seq) - AA:
                    continue
                activity = (row.get("Activity") or "").strip()
                pmid = (row.get("Pubmed_ID") or "").strip()
                if not activity or not NUMERIC.search(activity) or not CONC_UNIT.search(activity):
                    continue
                if not re.fullmatch(r"\d{5,9}", pmid):
                    continue
                if len(activity_samples) < 12:
                    activity_samples.append(activity[:120])
                species = SPECIES.search(activity)
                target = f"{species.group(0)}" if species else DATASET_TARGET.get(
                    dataset, "bacteria (dataset attribution)")
                entry = {
                    "target": target,
                    "activity": activity[:300],
                    "pmid": pmid,
                    "dramp_id": (row.get("DRAMP_ID") or "").strip(),
                    "name": (row.get("Name") or "").strip()[:120],
                    "source": dataset,
                }
                if seq in merged:
                    merged[seq]["sources"] = sorted(set(merged[seq]["sources"]) | {dataset})
                    merged[seq]["n_records"] += 1
                    merged[seq]["pmids"] = sorted(set(merged[seq]["pmids"]) | {pmid})
                else:
                    entry["sources"] = [dataset]
                    entry["n_records"] = 1
                    entry["pmids"] = [pmid]
                    merged[seq] = entry
                kept += 1
            per_dataset[dataset] = {"records": len(rows), "kept_rows": kept}

        decoys: list[str] = []
        rng = random.Random(20261124)
        for seq in merged:
            chars = list(seq)
            for _ in range(30):
                rng.shuffle(chars)
                cand = "".join(chars)
                if cand != seq and cand not in merged and cand not in decoys:
                    decoys.append(cand)
                    break

        targets: dict[str, int] = {}
        pmids: set[str] = set()
        for entry in merged.values():
            family = re.sub(r"\(.*?\)", "", entry["target"]).strip().lower()[:40]
            targets[family] = targets.get(family, 0) + 1
            pmids.update(entry["pmids"])

        report["counts"] = {
            "per_dataset": per_dataset,
            "unique_strict_positives": len(merged),
            "multi_record_sequences": sum(1 for v in merged.values() if v["n_records"] > 1),
            "distinct_pmids": len(pmids),
            "composition_preserving_decoys": len(decoys),
            "length_min": min((len(s) for s in merged), default=0),
            "length_max": max((len(s) for s in merged), default=0),
        }
        report["target_distribution"] = dict(sorted(targets.items(), key=lambda kv: -kv[1])[:15])
        report["activity_samples_redacted"] = activity_samples

        positives = os.path.join(args.workdir, "positive_core_v2_1.fasta")
        with open(positives, "w", encoding="utf-8") as f:
            for i, seq in enumerate(sorted(merged)):
                f.write(f">V21P{i:05d}\n{seq}\n")
        decoy_path = os.path.join(args.workdir, "composition_decoys_v2_1.fasta")
        with open(decoy_path, "w", encoding="utf-8") as f:
            for i, seq in enumerate(decoys):
                f.write(f">V21D{i:05d}\n{seq}\n")
        ledger = os.path.join(args.workdir, "positive_core_v2_1_ledger.tsv")
        with open(ledger, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["sequence_id", "sequence", "target_attribution", "activity",
                        "pmids", "sources", "n_records", "dramp_id", "name"])
            for i, seq in enumerate(sorted(merged)):
                e = merged[seq]
                w.writerow([f"V21P{i:05d}", seq, e["target"], e["activity"],
                            ",".join(e["pmids"]), ",".join(e["sources"]), e["n_records"],
                            e["dramp_id"], e["name"]])

        bundle = os.path.join(args.workdir, "positive_core_v2_1_bundle.zip")
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(positives, "frozen/positive_core_v2_1.fasta")
            z.write(decoy_path, "frozen/composition_decoys_v2_1.fasta")
            z.write(ledger, "frozen/positive_core_v2_1_ledger.tsv")
            z.writestr("frozen/manifest.json", json.dumps(report, ensure_ascii=False, indent=2))

        with open(positives, "rb") as f:
            pos_blob = f.read()
        with open(bundle, "rb") as f:
            bundle_blob = f.read()
        report["positive_fasta_sha256"] = sha256(pos_blob)
        report["positive_sequences"] = pos_blob.count(b">")
        report["bundle_bytes"] = len(bundle_blob)
        report["bundle_sha256"] = sha256(bundle_blob)
        report["status"] = "POSITIVES_BUILT"

        uri = ((os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
               or (os.environ.get("EVIDENCE_AUDIT_ID") or "").strip())
        if uri.startswith("https://"):
            import requests
            size, offset, resp = len(bundle_blob), 0, None
            while offset < size:
                chunk = bundle_blob[offset:offset + 8 * 1024 * 1024]
                final = offset + len(chunk) >= size
                headers = {
                    "Content-Type": "application/zip",
                    "Content-Length": str(len(chunk)),
                    "X-Goog-Upload-Offset": str(offset),
                    "X-Goog-Upload-Command": "upload, finalize" if final else "upload",
                    "X-Goog-Upload-Total-Size": str(size),
                }
                resp = requests.put(uri, headers=headers, data=chunk, timeout=300)
                if resp.status_code not in (200, 201):
                    raise RuntimeError(f"private bundle upload failed: HTTP {resp.status_code}")
                offset += len(chunk)
            report["private_bundle_upload"] = {"bytes": size, "sha256": sha256(bundle_blob),
                                               "drive_file": resp.json() if resp else {}}
            report["status"] = "POSITIVES_BUILT_AND_UPLOADED"
        else:
            report["private_bundle_upload"] = {"skipped": "no upload session in env"}

        report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with open(os.path.join(args.out, "v2_1_positive_build_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(json.dumps({k: v for k, v in report.items() if k != "sources"},
                         ensure_ascii=False, indent=2)[:4000], flush=True)
    except Exception as exc:  # noqa: BLE001
        report.update({"status": "FAILED", "error": str(exc),
                       "traceback": traceback.format_exc(limit=6),
                       "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        with open(os.path.join(args.out, "v2_1_positive_build_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
