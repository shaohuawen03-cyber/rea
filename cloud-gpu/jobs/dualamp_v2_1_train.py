#!/usr/bin/env python3
"""v2.1 training + honest reporting on the rebuilt (DRAMP-curated) positive cohort.

Why not `dualamp_pilot.py`: the pilot writes its own two-source manifest, so the
composition-matched decoys could never enter as a challenge layer and the positive
layer stayed `general_amp`. Here we drive the pinned leanrea V4 pipeline directly with
a three-source manifest (`bacterial_amp` positives + UniProt weak negatives +
`scrambled` composition decoys), then report calibration and fixed-specificity
operating points instead of a single threshold.

Private-only material: sequences, ledger, weights, scalers, splits. The kernel output
carries aggregate metrics only.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

LEANREA = "https://github.com/hukeying85-ux/leanrea"
LEANREA_REV = "f610dc00b1aec3ab948a5b77574af0e644259a13"
POSITIVE_SHA = "9ca0d4076f6d61c16ad31b920afc9029ae463d3df3510049c9b6ac4366a8e40c"
BUNDLE_SHA = "d5dce04e7763dc1abe9cdcf2729de1701cccdf6c893a903e6aee2319058936bd"
AA = set("ACDEFGHIKLMNPQRSTVWY")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_fasta(path: Path) -> list[str]:
    seqs, buf, name = [], [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(">"):
            if name is not None:
                seqs.append("".join(buf))
            name, buf = line, []
        else:
            buf.append(line.strip())
    if name is not None:
        seqs.append("".join(buf))
    return [s for s in seqs if s]


def write_fasta(path: Path, rows: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for header, seq in rows:
            f.write(f">{header}\n{seq}\n")


def upload(uri: str, path: Path) -> dict:
    import requests
    size = path.stat().st_size
    offset, resp = 0, None
    with path.open("rb") as f:
        while offset < size:
            chunk = f.read(8 * 1024 * 1024)
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
                raise RuntimeError(f"private upload failed: HTTP {resp.status_code}")
            offset += len(chunk)
    return {"bytes": size, "sha256": sha(path), "drive_file": resp.json() if resp else {}}


def csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def find(work: Path, pattern: str) -> list[Path]:
    root = work / "run"
    return sorted(root.rglob(pattern)) if root.exists() else []


def operating_points(oof_rows: list[dict], targets: list[float]) -> list[dict]:
    """Dev-OOF thresholds that hit fixed specificities, with the matching sensitivity."""
    out: list[dict] = []
    by_model: dict[str, dict] = {}
    for row in oof_rows:
        model = row.get("model") or "unknown"
        try:
            y = int(float(row["y_true"]))
            p = float(row["prob"])
        except (KeyError, TypeError, ValueError):
            continue
        by_model.setdefault(model, {"y": [], "p": []})
        by_model[model]["y"].append(y)
        by_model[model]["p"].append(p)
    for model, data in sorted(by_model.items()):
        neg = sorted(p for p, y in zip(data["p"], data["y"]) if y == 0)
        pos = sorted(p for p, y in zip(data["p"], data["y"]) if y == 1)
        if not neg or not pos:
            continue
        for target in targets:
            idx = min(len(neg) - 1, max(0, int(round((1.0 - target) * (len(neg) - 1)))))
            thr = neg[idx]
            sens = sum(1 for p in pos if p >= thr) / len(pos)
            spec = sum(1 for p in neg if p < thr) / len(neg)
            out.append({"model": model, "target_specificity": target,
                        "oof_threshold": round(thr, 6),
                        "oof_specificity": round(spec, 4),
                        "oof_sensitivity": round(sens, 4),
                        "oof_n_neg": len(neg), "oof_n_pos": len(pos)})
    return out


def composition_challenge(metrics: list[dict], extra_paths: list[Path]) -> dict:
    """Did the model accept composition-identical shuffles? Report it straight."""
    rows: list[dict] = []
    for path in extra_paths:
        for row in csv_rows(path):
            if any(k in json.dumps(row).lower() for k in ("scrambled", "decoy")):
                rows.append(row)
    for row in metrics:
        if "scrambled" in json.dumps(row).lower():
            rows.append(row)
    fracs = []
    for row in rows:
        for key in ("frac_predicted_positive", "fpr", "false_positive_rate"):
            if key in row and str(row[key]).strip():
                try:
                    fracs.append(float(row[key]))
                except ValueError:
                    pass
    verdict = "NOT_EVALUATED"
    if fracs:
        worst = max(fracs)
        verdict = "PASS" if worst < 0.10 else "FAIL_COMPOSITION_SHORTCUT"
    return {"rows": rows, "max_frac_predicted_positive": max(fracs) if fracs else None,
            "verdict": verdict}


def main() -> int:
    out = Path("/kaggle/working")
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="v21-", dir="/tmp"))
    report: dict = {
        "stage": "dualamp_v2_1_training",
        "dataset_version": "positive_core_v2_1_dramp",
        "rule_version": "auto_evidence_v2_2.0",
        "leanrea_commit": LEANREA_REV,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "training_started": False,
    }
    try:
        bundle_id = (os.environ.get("EVIDENCE_AUDIT_ID") or "").strip()
        uri = (os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
        want_sha = (os.environ.get("FASTA_SHA256") or "").strip()
        if not bundle_id or not uri:
            raise RuntimeError("private v2.1 bundle id and model-upload session are required")
        if want_sha != POSITIVE_SHA:
            raise RuntimeError("v2.1 positive FASTA hash does not match the frozen manifest")

        import gdown
        bundle = tmp / "v21_bundle.zip"
        got = gdown.download(id=bundle_id, output=str(bundle), quiet=True)
        if not got or sha(bundle) != BUNDLE_SHA:
            raise RuntimeError("private v2.1 bundle download/hash verification failed")
        pos_fasta, decoy_fasta = tmp / "pos.fasta", tmp / "decoy.fasta"
        with zipfile.ZipFile(bundle) as z:
            names = z.namelist()
            for member in ("frozen/positive_core_v2_1.fasta",
                           "frozen/composition_decoys_v2_1.fasta"):
                if member not in names:
                    raise RuntimeError(f"{member} missing from the private bundle")
            pos_fasta.write_bytes(z.read("frozen/positive_core_v2_1.fasta"))
            decoy_fasta.write_bytes(z.read("frozen/composition_decoys_v2_1.fasta"))
            if "frozen/positive_core_v2_1_ledger.tsv" in names:
                (tmp / "ledger.tsv").write_bytes(
                    z.read("frozen/positive_core_v2_1_ledger.tsv"))
        if sha(pos_fasta) != POSITIVE_SHA:
            raise RuntimeError("positive FASTA hash mismatch after extraction")
        positives = [s for s in read_fasta(pos_fasta) if 5 <= len(s) <= 50]
        decoys = [s for s in read_fasta(decoy_fasta) if 5 <= len(s) <= 50 and not (set(s) - AA)]
        report["cohort"] = {"positives": len(positives), "composition_decoys": len(decoys),
                            "positive_fasta_sha256": POSITIVE_SHA}

        branch = os.environ.get("REA_BRANCH", "arena/85a1409e-rea")
        repo = tmp / "rea"
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch,
                        "https://github.com/shaohuawen03-cyber/rea.git", str(repo)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        code_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                           text=True).strip()

        leanrea = tmp / "leanrea"
        subprocess.run(["git", "clone", "--depth", "1", "-b", "main", LEANREA, str(leanrea)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(leanrea), "fetch", "--depth", "1", "origin", LEANREA_REV],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(leanrea), "checkout", "--detach", LEANREA_REV],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        head = subprocess.check_output(["git", "-C", str(leanrea), "rev-parse", "HEAD"],
                                       text=True).strip()
        if head != LEANREA_REV:
            raise RuntimeError(f"leanrea pin mismatch: {head}")
        pkg = leanrea / "dualamp-lite"

        wd = tmp / "work"
        data = wd / "data"
        data.mkdir(parents=True)
        write_fasta(data / "positives.fasta",
                    [(f"BACT_POS_{i:05d}", s) for i, s in enumerate(positives)])
        neg_src_dir = repo / "cloud-gpu" / "cache"
        for fname in ("uniprot_bacteria_cytoplasm_reviewed.fasta.gz",
                      "uniprot_bacteria_cytoplasm_reviewed.tsv.gz"):
            cand = neg_src_dir / fname
            if not cand.exists():
                cand = repo / fname
            if not cand.exists():
                raise RuntimeError(f"negative background {fname} not found in the repo")
            shutil.copy2(cand, data / fname)

        # UniProt fragments as the weak negative background (same recipe as v2).
        sys.path.insert(0, str(repo / "cloud-gpu" / "jobs"))
        import dualamp_pilot as pilot  # noqa: PLC0415

        neg_stats = pilot.build_negatives(
            data / "uniprot_bacteria_cytoplasm_reviewed.fasta.gz",
            data / "uniprot_bacteria_cytoplasm_reviewed.tsv.gz",
            data / "negatives.fasta", set(positives), cap=20000, seed=11, neg_mode="standard")
        report["negatives"] = neg_stats

        manifest = data / "sources.yaml"
        manifest.write_text(
            "sources:\n"
            "  - id: dramp_v2_1_positives\n"
            f"    path: {data / 'positives.fasta'}\n"
            "    format: fasta\n    layer: bacterial_amp\n    label: 1\n"
            "    min_len: 5\n    max_len: 50\n"
            "  - id: uniprot_a_fragments\n"
            f"    path: {data / 'negatives.fasta'}\n"
            "    format: fasta\n    layer: negative_background\n    label: 0\n"
            "    min_len: 5\n    max_len: 50\n"
            "  - id: composition_decoys_v2_1\n"
            f"    path: {decoy_fasta}\n"
            "    format: fasta\n    layer: scrambled\n    label: 0\n"
            "    min_len: 5\n    max_len: 50\n",
            encoding="utf-8")
        report["manifest_sha256"] = sha(manifest)

        smoke = (os.environ.get("V21_SMOKE") or "").strip() == "1"
        if smoke:
            positives = positives[:400]
            decoys = decoys[:400]
            write_fasta(data / "positives.fasta",
                        [(f"BACT_POS_{i:05d}", s_) for i, s_ in enumerate(positives)])
        seeds = "1" if smoke else "1,2,3,4,5"
        epochs = "2" if smoke else "60"
        folds = "2" if smoke else "5"
        report["smoke_mode"] = smoke
        env = {**os.environ, "PYTHONPATH": str(pkg)}
        steps = [
            [sys.executable, "-m", "dualamp_lite", "build", "--manifest", str(manifest),
             "--workdir", str(wd / "run"), "--cluster-method", "auto"],
            [sys.executable, "-m", "dualamp_lite", "features", "--workdir", str(wd / "run"),
             "--backend", "fair-esm", "--batch-size", "64"],
            [sys.executable, "-m", "dualamp_lite", "train", "--workdir", str(wd / "run"),
             "--models", "M1,M2,M3", "--seeds", seeds, "--folds", folds,
             "--epochs", epochs, "--loss", "bce"],
            [sys.executable, "-m", "dualamp_lite", "report", "--workdir", str(wd / "run")],
        ]
        report["training_started"] = True
        log_path = tmp / "training.log"
        with log_path.open("w", encoding="utf-8") as log:
            for cmd in steps:
                print("$ " + " ".join(cmd), file=log, flush=True)
                subprocess.run(cmd, cwd=str(pkg), env=env, stdout=log,
                               stderr=subprocess.STDOUT, check=True)
        shutil.copy2(log_path, out / "v2_1_training.log")

        metrics_path = (find(wd, "internal_test_metrics.csv")
                        or find(wd, "manuscript_D10_test_metrics.csv"))
        if not metrics_path:
            raise RuntimeError("no test-metrics table produced by the pipeline")
        metrics = csv_rows(metrics_path[0])
        summary_files = find(wd, "internal_test_summary.csv")
        summary = csv_rows(summary_files[0]) if summary_files else []
        dataset_files = find(wd, "dataset_summary.json")
        dataset = json.loads(dataset_files[0].read_text()) if dataset_files else {}
        calib_metrics = [p for p in find(wd, "calibration_metrics.csv")]
        calib_bins = [p for p in find(wd, "calibration_bins.csv")]

        oof_rows: list[dict] = []
        for path in find(wd, "oof*.csv"):
            rows = csv_rows(path)
            for row in rows:
                if {"model", "y_true", "prob"} <= set(row):
                    oof_rows.append(row)
                elif {"y_true", "prob"} <= set(row):
                    row.setdefault("model", path.parent.parent.name)
                    oof_rows.append(row)

        report.update({
            "status": "COMPLETED_V2_1_TRAINING",
            "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "code_commit": code_sha,
            "dataset_summary": dataset,
            "metrics": metrics,
            "metrics_summary": summary,
            "calibration_metrics": [r for p in calib_metrics for r in csv_rows(p)],
            "calibration_bins": [r for p in calib_bins for r in csv_rows(p)][:120],
            "fixed_specificity_operating_points": operating_points(oof_rows, [0.99, 0.995]),
            "composition_challenge": composition_challenge(
                metrics, [p for p in find(wd, "*.csv") if "challenge" in p.name.lower()]
                + [p for p in find(wd, "external_validation.csv")]),
            "caveats": [
                "Positives are DRAMP-curated bacterial actives (TIER_B_LABEL: bacterial "
                "activity label + named target + PubMed reference). The bulk dumps do not "
                "carry per-record MIC values, so this is not a MIC-quantitative cohort.",
                "Negatives are UniProt bacterial-cytoplasm fragments: a weak negative label, "
                "not experimentally verified inactivity.",
                "The composition-matched challenge is reported as a challenge; a high flag "
                "rate is published as a failure mode, not hidden.",
            ],
        })

        private_files = []
        for sub in ("model", "features", "splits", "results", "dataset"):
            root = wd / "run" / sub
            if root.exists():
                private_files.extend(p for p in sorted(root.rglob("*")) if p.is_file())
        if not any(p.suffix == ".pt" for p in private_files):
            raise RuntimeError("no trained checkpoint found for the private archive")
        private_zip = tmp / "dualamp_v2_1_model_bundle.zip"
        with zipfile.ZipFile(private_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in private_files:
                z.write(p, str(p.relative_to(wd / "run")))
            if (tmp / "ledger.tsv").exists():
                z.write(tmp / "ledger.tsv", "reports/positive_core_v2_1_ledger.tsv")
            z.writestr("run_provenance.json", json.dumps({
                "dataset_version": "positive_core_v2_1_dramp",
                "rule_version": "auto_evidence_v2_2.0",
                "positive_fasta_sha256": POSITIVE_SHA,
                "private_bundle_sha256": BUNDLE_SHA,
                "repo_commit": code_sha,
                "leanrea_commit": LEANREA_REV,
                "seeds": [1, 2, 3, 4, 5], "folds": 5, "epochs": 60,
                "models": ["M1", "M2", "M3"],
                "negative_semantics": "weak UniProt background, not experimental inactivity",
                "evidence_tier": "TIER_B_LABEL",
            }, indent=2))
        report["private_model_bundle"] = upload(uri, private_zip)

        (out / "v2_1_training_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items()
                          if k not in {"metrics", "metrics_summary", "calibration_bins"}},
                         ensure_ascii=False, indent=2)[:6000], flush=True)
    except Exception as exc:  # noqa: BLE001
        report.update({"status": "FAILED_NOT_COMPLETED", "error": str(exc),
                       "traceback": traceback.format_exc(limit=6),
                       "finished_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
        (out / "v2_1_training_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
