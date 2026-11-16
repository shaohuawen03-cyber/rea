#!/usr/bin/env python3
"""Package a gpu-relay run artifact into a release-ready directory.

Usage: pack_release_assets.py <src_dir> <pkg_dir> <release_tag> [expected_pt]

- Finds every *.pt under <src_dir> (the artifact holds both the private run dir
  and the cloud-gpu/results copy of the same files) and DEDUPES by basename.
  Duplicates must be byte-identical or the script aborts (conflict = suspicious).
- Copies features_biophys_scaler.joblib (first match) and a curated set of meta
  files (first match each) into the package.
- Writes SHA256SUMS.txt + export_manifest.json (provenance overridable via env).
- Creates <pkg_dir>.tar.gz next to <pkg_dir>? No - writes tarball to
  /tmp/<release_tag>.tar.gz and prints the path.
"""
import hashlib, json, os, sys, tarfile, datetime

if len(sys.argv) < 4:
    sys.exit(__doc__)
SRC = sys.argv[1]
PKG = sys.argv[2]
TAG = sys.argv[3]
EXPECT = int(sys.argv[4]) if len(sys.argv) > 4 else None

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

META_PATTERNS = [
    "_pilot_summary.json", "model_train_summary.json",
    "dataset_dataset_summary.json", "dataset_label_audit.csv",
    "manuscript_D10_test_metrics.csv", "manuscript_D10_evaluation_report.md",
    "manuscript_D11_external_validation.csv", "manuscript_D15_input_sha256.tsv",
    "ablation_D9_ablation.csv", "dualamp-real5-20261116.log", "result.json",
]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    os.makedirs(os.path.join(PKG, "model"), exist_ok=True)
    os.makedirs(os.path.join(PKG, "features"), exist_ok=True)
    os.makedirs(os.path.join(PKG, "meta"), exist_ok=True)

    # --- dedupe *.pt by basename -------------------------------------------
    by_base = {}
    for root, _, names in os.walk(SRC):
        for n in names:
            if n.lower().endswith(".pt"):
                full = os.path.join(root, n)
                base = n[len("model_"):] if n.startswith("model_") else n
                by_base.setdefault(base, []).append(full)
    conflicts = []
    for base, paths in by_base.items():
        digests = {sha256_file(p) for p in paths}
        if len(digests) != 1:
            conflicts.append((base, paths))
    if conflicts:
        for base, paths in conflicts:
            print(f"::error::conflicting copies of {base}: {paths}", file=sys.stderr)
        sys.exit(1)
    for base, paths in sorted(by_base.items()):
        with open(paths[0], "rb") as a, open(os.path.join(PKG, "model", base), "wb") as b:
            b.write(a.read())
    found = len(by_base)
    print(f"pt files (deduped): {found} (expected {EXPECT})")
    if EXPECT is not None and found != EXPECT:
        print(f"::error::expected {EXPECT} unique .pt files, found {found} — aborting", file=sys.stderr)
        sys.exit(1)

    # --- scaler + curated meta ----------------------------------------------
    scaler = None
    for root, _, names in os.walk(SRC):
        if "features_biophys_scaler.joblib" in names:
            scaler = os.path.join(root, "features_biophys_scaler.joblib")
            break
    if scaler:
        with open(scaler, "rb") as a, open(os.path.join(PKG, "features", "biophys_scaler.joblib"), "wb") as b:
            b.write(a.read())
    else:
        print("::warning::features_biophys_scaler.joblib not found in artifact", file=sys.stderr)
    for pat in META_PATTERNS:
        hit = None
        for root, _, names in os.walk(SRC):
            if pat in names:
                hit = os.path.join(root, pat)
                break
        if hit:
            with open(hit, "rb") as a, open(os.path.join(PKG, "meta", pat), "wb") as b:
                b.write(a.read())
        else:
            print(f"::warning::missing meta file: {pat}", file=sys.stderr)

    # --- manifest -------------------------------------------------------------
    files = []
    for root, _, names in os.walk(PKG):
        for n in sorted(names):
            p = os.path.join(root, n)
            files.append({"file": os.path.relpath(p, PKG), "bytes": os.path.getsize(p)})
    files = [f for f in files if f["file"] not in ("SHA256SUMS.txt", "export_manifest.json")]
    for f in files:
        f["sha256"] = sha256_file(os.path.join(PKG, f["file"]))
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

    tarball = f"/tmp/{TAG}.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        for root, dirs, names in os.walk(PKG):
            for n in names:
                full = os.path.join(root, n)
                tar.add(full, arcname=os.path.relpath(full, PKG))
    print(json.dumps({"packaged": len(files), "total_bytes": manifest["total_bytes"],
                      "tarball": tarball, "tarball_bytes": os.path.getsize(tarball)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
