#!/usr/bin/env python3
"""Build SHA256SUMS.txt + export_manifest.json for a weights-release package.

Usage: pack_release_assets.py <pkg_dir> <release_tag>
Walks <pkg_dir>, writes SHA256SUMS.txt and export_manifest.json into it, then
prints a one-line JSON summary. Provenance constants below document the real5
archival; keep them generic by overriding with env vars when archiving other runs.
"""
import hashlib, json, os, sys, datetime

PKG = sys.argv[1]
TAG = sys.argv[2]

PROVENANCE = {
    "source_workflow_run": os.environ.get(
        "PROVENANCE_RUN",
        "gpu-relay 43842046392 (https://github.com/shaohuawen03-cyber/rea/actions/runs/43842046392)"),
    "kernel": os.environ.get("PROVENANCE_KERNEL", "shaohuawen/dualamp-real5-20261116"),
    "trained_from_commit": os.environ.get("PROVENANCE_COMMIT", "8a2d232"),
    "positives_fasta_sha256": os.environ.get(
        "PROVENANCE_POS_SHA256",
        "7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d"),
    "models": os.environ.get(
        "PROVENANCE_MODELS",
        "M1,M2,M3 x seeds 1-5 (60 epochs, 5 folds); M0_RF sklearn not exported as .pt"),
    "backend": os.environ.get(
        "PROVENANCE_BACKEND",
        "fair-esm esm2_t12_35M_UR50D embeddings (ESM backbone NOT saved in .pt)"),
}
LABEL_NOTE = os.environ.get(
    "LABEL_NOTE",
    "candidate_v1_pilot_weights - unreviewed candidate_v1, NOT positive_core_v2, "
    "NOT for publication / not the formal model. positive_core_v2 retrain required "
    "after Review_Result backfill.")

files = []
for root, _, names in os.walk(PKG):
    for n in sorted(names):
        p = os.path.join(root, n)
        rel = os.path.relpath(p, PKG)
        if rel in ("SHA256SUMS.txt", "export_manifest.json"):
            continue
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        files.append({"file": rel, "bytes": os.path.getsize(p), "sha256": h.hexdigest()})
files.sort(key=lambda d: d["file"])

with open(os.path.join(PKG, "SHA256SUMS.txt"), "w", encoding="utf-8") as fh:
    for d in files:
        fh.write(f"{d['sha256']}  {d['file']}\n")

manifest = {
    "release_tag": TAG,
    "archived_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "provenance": PROVENANCE,
    "label_note": LABEL_NOTE,
    "file_count": len(files),
    "total_bytes": sum(d["bytes"] for d in files),
    "files": files,
}
with open(os.path.join(PKG, "export_manifest.json"), "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2, ensure_ascii=False)

print(json.dumps({"packaged": len(files), "total_bytes": manifest["total_bytes"]}, ensure_ascii=False))
