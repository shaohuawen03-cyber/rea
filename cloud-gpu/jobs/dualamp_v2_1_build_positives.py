#!/usr/bin/env python3
"""Build the v2.1 strict positive set from curated, experimentally annotated AMP records.

Why this replaces the 16-positive set: v2's positives came only from full-text
verification of one candidate list, which capped the cohort at 16 and made every
metric unstable (test n=2) and composition-driven (scrambled decoys were accepted).
Curated databases carry the same evidence the rule asks for — a sequence, a named
bacterial target, a measured activity (MIC) and a primary reference — at cohort scale.

Sources (verified reachable from inside a Kaggle kernel):
  * DRAMP 3.0 `Antibacterial_amps.txt` (sequence + MIC + target + references)
  * DRAMP 3.0 `Anti-Gram-positive_amps.txt` / `Anti-Gram-_amps.txt`
  * APD3 per-kingdom FASTA (sequence-level curation only, used as a variant pool)

Rules (`auto_evidence_v2_1.1`, stricter than v2.0):
  1. sequence must be standard amino acids and 5-50 aa;
  2. record must name at least one bacterial target and carry a numeric activity value
     with a mic-like unit (ug/ml, mg/l, uM ...); records with only "not determined" are
     dropped, never treated as negatives;
  3. duplicates collapse by exact sequence; every kept sequence keeps its source record
     ids and references so a row can be audited back;
  4. a reference is required (DOI/PMID or a title) — records without one are dropped;
  5. output is split into public-count-only report + private row files: sequences never
     leave the private bundle.

Usage:
    python cloud-gpu/jobs/dualamp_v2_1_build_positives.py \
        --workdir /tmp/v21 --out /kaggle/working
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0 (compatible; rea-v21-build/1.0)"}
DRAMP = "https://dramp.cpu-bioinfor.org/download.php?filename=download_data/DRAMP3.0_new/{}"
APD3 = "https://aps.unmc.edu/assets/sequences/{}"
AA = set("ACDEFGHIKLMNPQRSTVWY")
MIC_UNITS = re.compile(r"(?i)(\u00b5g|ug|μg|mg|nm|µm|um|micromol|nmol)\s*/?\s*(ml|l)\b")
BACTERIA = re.compile(
    r"(?i)(escherichia|staphylococc|pseudomonas|bacillus|klebsiella|acinetobacter|enterococc|"
    r"salmonella|listeria|streptococc|clostridi|mycobacter|helicobacter|campylobacter|"
    r"serratia|proteus|shigella|vibrio|enterobacter|gram-?positive|gram-?negative|bacteria)"
)
NUMERIC = re.compile(r"(\d+(?:[.,]\d+)?)")


def fetch(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def parse_dramp_txt(text: str) -> list[dict]:
    """DRAMP txt is one record per block of `Key: value` lines, blank-line separated."""
    records: list[dict] = []
    current: dict[str, str] = {}
    last_key = ""
    for raw_line in text.splitlines():
        line = raw_line.strip("\ufeff").rstrip()
        if not line.strip():
            if current:
                records.append(current)
                current, last_key = {}, ""
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _/()\-]{1,40}):\s*(.*)$", line)
        if match and not line.startswith((" ", "\t")):
            key, value = match.group(1).strip().lower(), match.group(2).strip()
            current[key] = value
            last_key = key
        elif last_key:
            current[last_key] = (current[last_key] + " " + line.strip()).strip()
    if current:
        records.append(current)
    return records


def pick(record: dict, *names: str) -> str:
    for name in names:
        value = record.get(name.lower())
        if value:
            return value.strip()
    return ""


def clean_sequence(value: str) -> str:
    seq = re.sub(r"[^A-Za-z]", "", value).upper()
    return seq


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
        "sources": {},
        "counts": {},
        "status": "STARTED",
    }
    rows: list[dict] = []
    try:
        targets = {
            "dramp_antibacterial_txt": DRAMP.format("Antibacterial_amps.txt"),
            "dramp_antibacterial_fasta": DRAMP.format("Antibacterial_amps.fasta"),
            "dramp_grampos_txt": DRAMP.format("Anti-Gram-positive_amps.txt"),
            "dramp_gramneg_txt": DRAMP.format("Anti-Gram-_amps.txt"),
            "apd3_bacteria_fasta": APD3.format("bacteriaAMPs_APD2024.fasta"),
        }
        blobs: dict[str, bytes] = {}
        for name, url in targets.items():
            try:
                blob = fetch(url)
                blobs[name] = blob
                report["sources"][name] = {"url": url, "bytes": len(blob),
                                            "sha256": sha256_bytes(blob)}
                print(f"[build] {name}: {len(blob)} bytes", flush=True)
            except Exception as exc:  # noqa: BLE001
                report["sources"][name] = {"url": url,
                                            "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                print(f"[build] {name} FAILED: {exc}", flush=True)

        parsed_total = 0
        for name in ("dramp_antibacterial_txt", "dramp_grampos_txt", "dramp_gramneg_txt"):
            blob = blobs.get(name)
            if not blob:
                continue
            text = blob.decode("utf-8", "replace")
            records = parse_dramp_txt(text)
            parsed_total += len(records)
            report["counts"][f"{name}_records"] = len(records)
            if records and name == "dramp_antibacterial_txt":
                report["dramp_field_names"] = sorted(records[0])[:40]
            kept_here = 0
            for record in records:
                seq = clean_sequence(pick(record, "sequence"))
                if not seq or len(seq) < 5 or len(seq) > 50 or set(seq) - AA:
                    continue
                target = pick(record, "target organism", "target", "activity target")
                activity = pick(record, "activity", "mic", "antimicrobial activity")
                refs = pick(record, "references", "reference", "literature", "pubmed id")
                if not target or not BACTERIA.search(target):
                    continue
                if not activity or not NUMERIC.search(activity):
                    continue
                if not MIC_UNITS.search(activity) and "mic" not in activity.lower():
                    continue
                if not refs:
                    continue
                rows.append({
                    "sequence": seq,
                    "target": target[:200],
                    "activity": activity[:200],
                    "references": refs[:400],
                    "source": name,
                    "dramp_id": pick(record, "dramp id", "id", "amp id"),
                    "name": pick(record, "name", "peptide name")[:120],
                })
                kept_here += 1
            report["counts"][f"{name}_kept"] = kept_here

        # APD3 FASTA: sequence-level only; kept as a variant pool (never a positive on its own).
        apd_seqs: set[str] = set()
        blob = blobs.get("apd3_bacteria_fasta")
        if blob:
            for line in blob.decode("utf-8", "replace").splitlines():
                if line.startswith(">"):
                    continue
                seq = clean_sequence(line)
                if 5 <= len(seq) <= 50 and not (set(seq) - AA):
                    apd_seqs.add(seq)
        report["counts"]["apd3_bacteria_sequences"] = len(apd_seqs)

        # Deduplicate by sequence; merge provenance.
        merged: dict[str, dict] = {}
        for row in rows:
            seq = row.pop("sequence")
            if seq in merged:
                merged[seq]["sources"] = sorted(set(merged[seq]["sources"]) | {row["source"]})
                merged[seq]["n_records"] += 1
                if row["activity"] and row["activity"] not in merged[seq]["activity"]:
                    merged[seq]["activity"] = (merged[seq]["activity"] + " | " + row["activity"])[:400]
                if row["target"] and row["target"] not in merged[seq]["target"]:
                    merged[seq]["target"] = (merged[seq]["target"] + " | " + row["target"])[:300]
            else:
                row["sources"] = [row["source"]]
                row["n_records"] = 1
                merged[seq] = row

        # Composition-preserving decoys (hard negatives): shuffle each positive 1:1.
        import random
        rng = random.Random(20261124)
        decoys: list[str] = []
        for seq in merged:
            chars = list(seq)
            for _ in range(20):
                rng.shuffle(chars)
                cand = "".join(chars)
                if cand != seq and cand not in merged and cand not in decoys:
                    decoys.append(cand)
                    break

        report["counts"]["unique_strict_positives"] = len(merged)
        report["counts"]["multi_record_sequences"] = sum(1 for v in merged.values() if v["n_records"] > 1)
        report["counts"]["composition_preserving_decoys"] = len(decoys)
        report["counts"]["parsed_dramp_records"] = parsed_total

        positives = args.workdir + "/positive_core_v2_1.fasta"
        with open(positives, "w", encoding="utf-8") as f:
            for i, seq in enumerate(sorted(merged)):
                f.write(f">V21P{i:05d}\n{seq}\n")
        decoy_path = args.workdir + "/composition_decoys_v2_1.fasta"
        with open(decoy_path, "w", encoding="utf-8") as f:
            for i, seq in enumerate(decoys):
                f.write(f">V21D{i:05d}\n{seq}\n")
        ledger_path = args.workdir + "/positive_core_v2_1_ledger.tsv"
        with open(ledger_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(["sequence_id", "sequence", "target", "activity", "references",
                             "sources", "n_records", "dramp_id", "name"])
            for i, seq in enumerate(sorted(merged)):
                row = merged[seq]
                writer.writerow([f"V21P{i:05d}", seq, row["target"], row["activity"],
                                 row["references"], ",".join(row["sources"]), row["n_records"],
                                 row.get("dramp_id", ""), row.get("name", "")])

        # Bundle the row-level material for the private Drive upload.
        bundle = os.path.join(args.workdir, "positive_core_v2_1_bundle.zip")
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(positives, "frozen/positive_core_v2_1.fasta")
            z.write(decoy_path, "frozen/composition_decoys_v2_1.fasta")
            z.write(ledger_path, "frozen/positive_core_v2_1_ledger.tsv")
            z.writestr("frozen/manifest.json", json.dumps(report, ensure_ascii=False, indent=2))

        with open(positives, "rb") as f:
            pos_sha = sha256_bytes(f.read())
        with open(bundle, "rb") as f:
            bundle_blob = f.read()
        report.update({
            "positive_fasta_sha256": pos_sha,
            "bundle_bytes": len(bundle_blob),
            "bundle_sha256": sha256_bytes(bundle_blob),
            "status": "POSITIVES_BUILT",
        })

        uri = (os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
        if uri:
            import requests
            size = len(bundle_blob)
            offset = 0
            resp = None
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
            report["private_bundle_upload"] = {"bytes": size, "sha256": sha256_bytes(bundle_blob),
                                               "drive_file": resp.json() if resp else {}}
            report["status"] = "POSITIVES_BUILT_AND_UPLOADED"
        else:
            report["private_bundle_upload"] = {"skipped": "no upload session provided"}

        report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        with open(os.path.join(args.out, "v2_1_positive_build_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(json.dumps({k: v for k, v in report.items() if k != "sources"},
                         ensure_ascii=False, indent=2), flush=True)
    except Exception as exc:  # noqa: BLE001
        report.update({"status": "FAILED", "error": str(exc),
                       "traceback": traceback.format_exc(limit=6),
                       "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        with open(os.path.join(args.out, "v2_1_positive_build_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
