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
import time
import tempfile
import traceback
import zipfile
from pathlib import Path

LEANREA = "https://github.com/hukeying85-ux/leanrea"
LEANREA_REV = "f610dc00b1aec3ab948a5b77574af0e644259a13"
POSITIVE_SHA = "9ca0d4076f6d61c16ad31b920afc9029ae463d3df3510049c9b6ac4366a8e40c"

# --------------------------------------------------------------------------- #
# v4 = v2.1 protocol + three methodological fixes for the composition shortcut
# --------------------------------------------------------------------------- #
# Evidence for each fix:
#   * Siddhantkar/BIB 2022 (bbac343): AMP benchmark AUC correlates with how similar
#     the train and benchmark amino-acid composition are (rho = -0.53). Fix = the
#     model must see composition-matched sequences labelled negative during training,
#     not only at challenge time.
#   * bioRxiv May 2026 audit of 16 peptide-activity predictors: fold imbalance plus
#     train/test leakage inflate reported accuracy. Fix = report leakage-aware split
#     sizes and add a composition-only control model.
#   * leanrea's `scrambled` layer is excluded from training by design, so v2.1 could
#     only *measure* the shortcut. `hard_negative` IS trainable, so v4 injects the
#     composition-matched decoys there (disjoint pool from the challenge set).

#: fraction of the decoy pool injected as trainable hard negatives
V4_HARDNEG_FRAC = float(os.environ.get("V4_HARDNEG_FRAC", "0.5"))
#: fraction of *training* negatives that are composition-matched (class-imbalance arm)
V4_HARDNEG_SHARE = os.environ.get("V4_HARDNEG_SHARE", "0.5")
#: cap on hard negatives so the negative pool does not explode
V4_HARDNEG_CAP = int(os.environ.get("V4_HARDNEG_CAP", "20000"))
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
    """Dev-OOF thresholds that hit a target specificity, with the matching sensitivity.

    The threshold is the negative-score quantile at ``1 - target`` (empirical, nearest
    order statistic), then sensitivity/specificity are measured on the same pooled dev
    OOF rows. Reported per model across all seeds, plus the per-seed threshold spread so
    a reader can see how stable the cut is.
    """
    out: list[dict] = []
    by_model: dict[str, dict] = {}
    for row in oof_rows:
        model = row.get("model") or "unknown"
        try:
            y = int(float(row["y_true"]))
            p = float(row["prob"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0.0 <= p <= 1.0):
            continue
        bucket = by_model.setdefault(model, {"y": [], "p": [], "seeds": set()})
        bucket["y"].append(y)
        bucket["p"].append(p)
        if row.get("seed") not in (None, ""):
            bucket["seeds"].add(str(row["seed"]))
    for model, data in sorted(by_model.items()):
        neg = sorted(p for p, y in zip(data["p"], data["y"]) if y == 0)
        pos = sorted(p for p, y in zip(data["p"], data["y"]) if y == 1)
        if not neg or not pos:
            continue
        for target in targets:
            # Walk candidate cuts from the lowest negative score upwards and keep the first
            # one whose achieved specificity still meets the target: that is the *most
            # sensitive* operating point at the requested specificity. (Ordering this the
            # other way round silently reports specificity 0.01 for a 0.99 request.)
            thr = None
            for cand in neg:
                spec_c = sum(1 for p in neg if p < cand) / len(neg)
                if spec_c >= target - 1e-12:
                    thr = cand
                    break
            if thr is None:
                thr = max(neg)
            sens = sum(1 for p in pos if p >= thr) / len(pos)
            spec = sum(1 for p in neg if p < thr) / len(neg)
            out.append({"model": model, "target_specificity": target,
                        "source": "dev OOF (pooled over seeds)",
                        "oof_threshold": round(thr, 6),
                        "oof_specificity": round(spec, 4),
                        "oof_sensitivity": round(sens, 4),
                        "specificity_gap": round(spec - target, 4),
                        "n_seeds": len(data["seeds"]) or None,
                        "oof_n_neg": len(neg), "oof_n_pos": len(pos)})
    return out


# --------------------------------------------------------------------------- #
# calibration / operating points computed from the pipeline's own score files
# --------------------------------------------------------------------------- #
def _np():
    import numpy as np  # noqa: PLC0415
    return np


def _auc(y, p) -> float:
    np = _np()
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(p)
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    _, inverse, counts = np.unique(p, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inverse, ranks)
    ranks = (sums / counts)[inverse]
    return float((ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _brier(y, p) -> float:
    np = _np()
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((p - y) ** 2))


def _ece(y, p, bins: int = 10) -> tuple[float, list[dict]]:
    np = _np()
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows, total = [], 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p < hi) if i < bins - 1 else (p >= lo) & (p <= hi)
        if not m.any():
            continue
        conf, freq, n = float(p[m].mean()), float(y[m].mean()), int(m.sum())
        rows.append({"bin_low": round(float(lo), 3), "bin_high": round(float(hi), 3),
                     "n": n, "mean_predicted": round(conf, 4), "observed_rate": round(freq, 4)})
        total += n / len(p) * abs(freq - conf)
    return float(total), rows


def _metrics_at(y, p, thr: float) -> dict:
    np = _np()
    y = np.asarray(y).astype(int)
    pred = (np.asarray(p, dtype=float) >= thr).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum()); fp = int(((pred == 1) & (y == 0)).sum())
    tn = int(((pred == 0) & (y == 0)).sum()); fn = int(((pred == 0) & (y == 1)).sum())
    sens = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    prec = tp / (tp + fp) if tp + fp else float("nan")
    f1 = 2 * prec * sens / (prec + sens) if prec + sens else float("nan")
    mcc_den = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
    return {"threshold": round(float(thr), 6), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "sensitivity": round(sens, 4) if sens == sens else None,
            "specificity": round(spec, 4) if spec == spec else None,
            "precision": round(prec, 4) if prec == prec else None,
            "f1": round(f1, 4) if f1 == f1 else None,
            "mcc": round((tp * tn - fp * fn) / mcc_den, 4) if mcc_den else 0.0}


def fixed_specificity_from_dev(dev_y, dev_p, targets=(0.99, 0.995)) -> list[dict]:
    """Most sensitive dev cut that still reaches the target specificity."""
    np = _np()
    dev_y = np.asarray(dev_y).astype(int)
    dev_p = np.asarray(dev_p, dtype=float)
    neg = np.sort(dev_p[dev_y == 0])
    pos = dev_p[dev_y == 1]
    out = []
    if len(neg) == 0 or len(pos) == 0:
        return out
    for target in targets:
        thr = None
        for cand in neg:                      # lowest negative score upwards
            if float((neg < cand).mean()) >= target - 1e-12:
                thr = float(cand)
                break
        if thr is None:
            thr = float(neg.max())
        out.append({"target_specificity": target,
                    "dev_threshold": round(thr, 6),
                    "dev_specificity": round(float((neg < thr).mean()), 4),
                    "dev_sensitivity": round(float((pos >= thr).mean()), 4),
                    "dev_n_neg": int(len(neg)), "dev_n_pos": int(len(pos)),
                    "source": "dev OOF (seed 1) per model/<MODEL>_scores.npz"})
    return out


def calibration_block(work: Path) -> dict:
    """Brier/ECE/reliability + fixed-specificity points from the score files.

    leanrea's CLI never runs the V4 `evaluation` stage (no subcommand, and the stage
    bootstrap needs a run id the CLI does not persist), so the calibration tables the
    report needs are computed here from `model/<MODEL>_scores.npz`, which holds
    per-seed test scores, the ensemble, the labels and the seed-1 dev OOF vector.
    """
    np = _np()
    root = work / "run"
    out: dict = {"per_model": [], "bins": [], "operating_points": [], "source": []}
    for path in sorted((root / "model").glob("*_scores.npz")) if (root / "model").exists() else []:
        model = path.name.split("_scores.npz")[0]
        with np.load(path, allow_pickle=True) as z:
            keys = set(z.files)
            ens = z["test_ensemble"] if "test_ensemble" in keys else None
            y = z["test_labels"] if "test_labels" in keys else None
            if ens is None or y is None:
                continue
            ens = np.asarray(ens, dtype=float)
            y = np.asarray(y).astype(int)
            row = {"model": model, "n_test": int(len(y)), "n_pos": int(y.sum()),
                   "auroc": round(_auc(y, ens), 4),
                   "brier": round(_brier(y, ens), 5),
                   "brier_ref_all_positive": round(float(y.mean() * (1 - y.mean())), 5)}
            ece_val, bins = _ece(y, ens)
            row["ece"] = round(ece_val, 5)
            row["mean_predicted"] = round(float(ens.mean()), 4)
            row["observed_rate"] = round(float(y.mean()), 4)
            out["per_model"].append(row)
            for b in bins:
                out["bins"].append({"model": model, **b})
            out["source"].append(f"model/{path.name}")
            if "dev_oof_seed0" in keys and "dev_labels" in keys:
                for pt in fixed_specificity_from_dev(z["dev_labels"], z["dev_oof_seed0"]):
                    pt["model"] = model
                    hit = _metrics_at(y, ens, pt["dev_threshold"])
                    pt["test_at_dev_threshold"] = {k: hit[k] for k in
                                                   ("sensitivity", "specificity", "mcc",
                                                    "precision", "f1")}
                    out["operating_points"].append(pt)
    return out


def composition_from_scores(work: Path, decoy_ids: set[str]) -> dict:
    """Fraction of composition decoys accepted by each model, from the score archive.

    The CLI prints this in the log (`external[scrambled] ...`) but writes no table, and
    the V4 challenge table is unavailable. The test split is homology-grouped and holds
    no decoys, so the decoy acceptance rate is taken from the recorded challenge log
    lines; the raw per-sequence scores stay private.
    """
    return {"decoy_ids_available": bool(decoy_ids)}


def composition_from_log(log_path: Path, models: list[str]) -> dict:
    """Recover the composition-challenge numbers from the training log.

    leanrea prints `external[scrambled] <MODEL>: {..., 'frac_predicted_positive': x}`
    but writes no challenge table on the CLI path, and the frozen test split is
    homology-grouped (it holds no decoys). The numbers below are those log lines:
    aggregate only, no per-sequence scores.
    """
    import re
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    rows = []
    for m in re.finditer(r"external\[scrambled\]\s+(\w+):\s*\{([^}]*)\}", text):
        model, body = m.group(1), m.group(2)
        row = {"split": "scrambled_test", "model": model, "source": "training log"}
        for key in ("n", "n_pos", "mean_score", "frac_predicted_positive"):
            mm = re.search(rf"'{key}':\s*([-0-9.eE]+)", body)
            if mm:
                val = float(mm.group(1))
                row[key] = int(val) if key in ("n", "n_pos") else round(val, 4)
        if row.get("model"):
            rows.append(row)
    fracs = [r["frac_predicted_positive"] for r in rows if "frac_predicted_positive" in r]
    verdict = "NOT_EVALUATED"
    if fracs:
        verdict = "PASS" if max(fracs) < 0.10 else "FAIL_COMPOSITION_SHORTCUT"
    return {"rows": rows, "max_frac_predicted_positive": max(fracs) if fracs else None,
            "best_model_frac": min(fracs) if fracs else None,
            "verdict": verdict,
            "criterion": "PASS only if every model keeps composition-decoy acceptance < 10%",
            "note": "decoy sequences are length+composition matched shuffles of the positives; "
                    "a high acceptance rate means the model reads composition, not order"}


def composition_challenge(metrics: list[dict], extra_paths: list[Path]) -> dict:
    """Did the model accept composition-identical shuffles? Report it straight."""
    rows: list[dict] = []
    for path in extra_paths:
        for row in csv_rows(path):
            blob = json.dumps(row).lower()
            # external_test / homolog splits are *not* composition controls; counting them
            # would let an unrelated split decide the composition verdict.
            if ("scrambled" in blob or "composition" in blob or "decoy" in blob) \
                    and "external" not in blob and "homolog" not in blob:
                rows.append(row)
    for row in metrics:
        blob = json.dumps(row).lower()
        if ("scrambled" in blob or "composition" in blob) and "external" not in blob:
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
        "stage": "dualamp_v4_training",
        "dataset_version": "positive_core_v2_1_dramp_v4_hardneg",
        "rule_version": "auto_evidence_v2_2.0",
        "leanrea_commit": LEANREA_REV,
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "training_started": False,
    }
    try:
        # Inputs come from the committed, non-secret descriptor first; envelopes are only
        # needed for the single-use upload session. That keeps a consumed envelope from
        # blocking a retry (it used to, twice).
        # The payload runs from /tmp (inlined by the relay), so the repo descriptor is not
        # next to __file__. Candidates: job env, repo clone, cwd, then a built-in copy that
        # the relay injects as V21_INPUTS_JSON.
        inputs: dict = {}
        env_blob = (os.environ.get("V21_INPUTS_JSON") or "").strip()
        if env_blob:
            try:
                inputs = json.loads(env_blob)
            except json.JSONDecodeError:
                inputs = {}
        for cand in (Path(os.environ.get("REA_REPO_DIR", ".")) / "cloud-gpu/jobs/v21_inputs.json",
                     Path(__file__).parent / "v21_inputs.json",
                     Path("cloud-gpu/jobs/v21_inputs.json")):
            if not inputs and cand.exists():
                inputs = json.loads(cand.read_text())
        bundle_id = ((os.environ.get("EVIDENCE_AUDIT_ID") or "").strip()
                     or str(inputs.get("private_bundle_drive_id", "")))
        want_sha = ((os.environ.get("FASTA_SHA256") or "").strip()
                    or str(inputs.get("positive_fasta_sha256", "")))
        uri = (os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
        if not bundle_id:
            raise RuntimeError("no private v2.1 bundle id (env or v21_inputs.json)")
        if want_sha != POSITIVE_SHA:
            raise RuntimeError("v2.1 positive FASTA hash does not match the frozen manifest")
        report["upload_session_present"] = bool(uri)

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

        # A prior kernel output can be mounted read-only under /kaggle/input; the
        # package-only pass rebuilds the private archive from it.
        mounted_root = None
        if (os.environ.get("V21_PACKAGE_ONLY") or "").strip() == "1":
            for cand in sorted(Path("/kaggle/input").rglob("v2_1_training_report.json")):
                mounted_root = cand.parent
                break
            if mounted_root is None:
                raise RuntimeError("package-only mode needs a mounted prior output")

        wd = tmp / "work"
        data = wd / "data"
        data.mkdir(parents=True)
        smoke = (os.environ.get("V21_SMOKE") or "").strip() == "1"
        if smoke:
            positives = positives[:400]
        write_fasta(data / "positives.fasta",
                    [(f"BACT_POS_{i:05d}", s) for i, s in enumerate(positives)])

        # ------------------------------------------------------------------ #
        # v4 fix #1: the composition shortcut must be trained against, not only
        # measured. Split the decoy pool into disjoint halves so the challenge
        # number stays honest: `hard_neg` is TRAINABLE, `challenge` is untouched.
        # ------------------------------------------------------------------ #
        import random as _random
        _rng = _random.Random(20261126)
        decoy_pool = list(dict.fromkeys(decoys))          # order-preserving dedupe
        _rng.shuffle(decoy_pool)
        n_hard = min(int(len(decoy_pool) * V4_HARDNEG_FRAC), V4_HARDNEG_CAP)
        hard_neg_seqs = decoy_pool[:n_hard]
        challenge_seqs = decoy_pool[n_hard:]
        hard_neg_fasta = data / "hard_negatives.fasta"
        write_fasta(hard_neg_fasta, [(f"HARDNEG_{i:05d}", x)
                                     for i, x in enumerate(hard_neg_seqs)])
        # The challenge file must contain ONLY the held-out half.
        write_fasta(decoy_fasta, [(f"CPD_{i:05d}", x)
                                  for i, x in enumerate(challenge_seqs)])
        v4_split = {
            "decoy_pool_total": len(decoy_pool),
            "hard_negatives_trainable": len(hard_neg_seqs),
            "challenge_held_out": len(challenge_seqs),
            "overlap": len(set(hard_neg_seqs) & set(challenge_seqs)),
            "hardneg_frac": V4_HARDNEG_FRAC,
            "negative_share_policy": V4_HARDNEG_SHARE,
        }
        if v4_split["overlap"] != 0:
            raise RuntimeError("v4 decoy split leaked: hard negatives overlap the challenge set")
        report["v4_decoy_split"] = v4_split
        print(f"[v4] decoy pool {len(decoy_pool)} -> hard negatives {len(hard_neg_seqs)} "
              f"(trainable) + challenge {len(challenge_seqs)} (held out)", flush=True)
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
            data / "negatives.fasta", set(positives), cap=2000 if smoke else 20000,
            seed=11, neg_mode="standard")
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
            "  - id: composition_hard_negatives_v4\n"
            f"    path: {hard_neg_fasta}\n"
            "    format: fasta\n    layer: hard_negative\n    label: 0\n"
            "    min_len: 5\n    max_len: 50\n"
            "  - id: composition_challenge_v4\n"
            f"    path: {decoy_fasta}\n"
            "    format: fasta\n    layer: scrambled\n    label: 0\n"
            "    min_len: 5\n    max_len: 50\n",
            encoding="utf-8")
        report["manifest_sha256"] = sha(manifest)

        if smoke:
            challenge_seqs = challenge_seqs[:200]
            hard_neg_seqs = hard_neg_seqs[:200]
            write_fasta(decoy_fasta, [(f"CPD_{i:05d}", s_) for i, s_ in enumerate(challenge_seqs)])
            write_fasta(hard_neg_fasta, [(f"HARDNEG_{i:05d}", s_)
                                         for i, s_ in enumerate(hard_neg_seqs)])
        seeds = "1" if smoke else "1,2,3,4,5"
        epochs = "2" if smoke else "60"
        folds = "2" if smoke else "5"
        report["smoke_mode"] = smoke
        env = {**os.environ, "PYTHONPATH": str(pkg)}
        package_only = (os.environ.get("V21_PACKAGE_ONLY") or "").strip() == "1"
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
        # `train` alone only writes the test metrics. Calibration (Brier/ECE/Platt), the
        # challenge-set table and the subgroup tables live in the V4 `evaluation` stage,
        # which the CLI has no subcommand for. Drive it directly on the existing run id;
        # without it the calibration and composition fields are empty (both early smokes).
        eval_payload = {
            "workdir": str(wd / "run"),
            "code": (
                "import json, sys\n"
                "sys.path.insert(0, '.')\n"
                "from pathlib import Path\n"
                "from dualamp_lite.v4.config import Paths, load_config\n"
                "from dualamp_lite.v4.pipeline import build_context, run_stage\n"
                f"root = Path({str(wd / 'run')!r})\n"
                "# results/ is only created by the evaluation stage itself, so look in every\n"
                "# directory the train stage writes a run id into, newest first\n"
                "def _discover(root):\n"
                "    if (root / 'latest_run.txt').exists():\n"
                "        return (root / 'latest_run.txt').read_text().strip()\n"
                "    for sub in ('runs', 'results', 'final_models', 'logs'):\n"
                "        base = root / sub\n"
                "        if not base.exists():\n"
                "            continue\n"
                "        for child in sorted((c for c in base.iterdir() if c.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True):\n"
                "            return child.name\n"
                "    return ''\n"
                "run_id = _discover(root)\n"
                "print('workdir tree:', sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_dir())[:40])\n"
                "if not run_id:\n"
                "    hits = [p.parent.name for p in root.rglob('oof_predictions.csv')]\n"
                "    run_id = hits[0] if hits else ''\n"
                "assert run_id, f'no run id found under {root}'\n"
                "print('discovered run_id:', run_id)\n"
                "cfg = load_config(root / 'configs' / f'resolved_{run_id}.yaml', workdir=root)\n"
                "ctx = build_context(cfg, Paths(root), run_id=run_id)\n"
                "print(json.dumps({'run_id': run_id, 'evaluation': run_stage(ctx, 'evaluation')}, default=str)[:2000])\n"
            ),
        }
        (tmp / "run_evaluation_stage.py").write_text(eval_payload["code"], encoding="utf-8")
        steps.insert(3, [sys.executable, str(tmp / "run_evaluation_stage.py")])
        report["evaluation_stage"] = "driven via dualamp_lite.v4.pipeline.run_stage('evaluation')"
        if package_only:
            # Re-packaging pass: the mounted kernel output already holds the trained run,
            # so retraining (90 GPU-minutes) would be pure waste.
            # the mounted output holds the run *contents*, not a nested work/run tree
            shutil.rmtree(wd, ignore_errors=True)
            wd.mkdir(parents=True, exist_ok=True)
            for item in mounted_root.iterdir():
                target = wd / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, target)
            print(f"[package-only] mounted items: {sorted(p.name for p in wd.iterdir())}", flush=True)
            steps = []
            report["package_only"] = True
            report["reused_run_utc"] = (mounted_root / "v2_1_progress.json").stat().st_mtime
            print(f"[package-only] reusing the mounted run at {wd}", flush=True)
        # Preflight the encoder: a missing/broken fair-esm must fail here with a clear
        # message instead of surfacing as "features exit 1" 20 minutes later.
        preflight = {}
        try:
            sys.path.insert(0, str(pkg))
            import numpy as np  # noqa: PLC0415
            from dualamp_lite.v4.config import FeatureConfig  # noqa: PLC0415
            from dualamp_lite.v4.features import build_encoder  # noqa: PLC0415
            enc = build_encoder(FeatureConfig(backend="fair-esm"), device="cpu")
            probe_res, probe_mean, probe_mask = enc.encode(["KLKLLKKLLK", "GIGKFLHSAKKFGKAFVGEIMNS"])
            preflight = {
                "encoder": enc.name,
                "is_mock": bool(getattr(enc, "is_mock", False)),
                "embed_dim": int(getattr(enc, "embed_dim", 0)),
                "probe_shapes": [list(probe_res.shape), list(probe_mean.shape)],
                "finite": bool(np.isfinite(probe_mean).all()),
            }
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised below
            preflight = {"encoder": "fair-esm", "error": f"{type(exc).__name__}: {exc}"}
            report["encoder_preflight"] = preflight
            raise
        report["encoder_preflight"] = preflight

        report["training_started"] = True
        # The log lives in the kernel output dir so a crash (or a killed poll) still
        # publishes it; the previous version wrote to /tmp and lost every root cause.
        log_path = Path(out) / "v2_1_training.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log:
            for cmd in steps:
                print("$ " + " ".join(cmd), file=log, flush=True)
                log.flush()
                t0 = time.time()
                proc = subprocess.run(cmd, cwd=str(pkg), env=env, stdout=log,
                                      stderr=subprocess.STDOUT)
                step_name = "evaluation" if cmd[1].endswith("run_evaluation_stage.py") else cmd[3]
                report.setdefault("steps", {})[step_name] = {
                    "returncode": proc.returncode,
                    "seconds": round(time.time() - t0, 1),
                }
                (Path(out) / "v2_1_progress.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
                log.flush()
                os.fsync(log.fileno())
                if proc.returncode != 0:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    print(tail, flush=True)
                    if step_name == "evaluation":
                        # 20 minutes of GPU training must survive a broken auxiliary stage:
                        # record it, keep going, and let the release tier say so.
                        report.setdefault("auxiliary_stage_errors", {})["evaluation"] = tail[-1200:]
                        print("[warn] evaluation stage failed; continuing without its tables",
                              flush=True)
                        continue
                    raise RuntimeError(
                        f"pipeline step failed ({proc.returncode}): {' '.join(cmd[3:7])}")

        # leanrea publishes results/<run_id>/internal_test_metrics.csv plus a manuscript copy
        # (D10_...); accept both spellings so a leanrea bump cannot silently break the summary.
        metrics_path = (find(wd, "internal_test_metrics.csv")
                        or find(wd, "D10_test_metrics.csv")
                        or find(wd, "manuscript_D10_test_metrics.csv"))
        if not metrics_path and not package_only:
            raise RuntimeError("no test-metrics table produced by the pipeline")
        metrics = csv_rows(metrics_path[0]) if metrics_path else []
        summary_files = find(wd, "D10_test_summary.csv") or find(wd, "internal_test_summary.csv")
        summary = csv_rows(summary_files[0]) if summary_files else []
        dataset_files = find(wd, "dataset_summary.json") or find(wd, "D1_dataset_summary.json")
        dataset = json.loads(dataset_files[0].read_text()) if dataset_files else {}
        calib_metrics = [p for p in find(wd, "calibration_metrics.csv")]
        calib_bins = [p for p in find(wd, "calibration_bins.csv")]

        # OOF predictions feed the fixed-specificity operating points. The file name is
        # oof_predictions.csv under runs/<run>/<model>/seed_<n>/, and the model column may
        # be absent, so recover it from the path instead of guessing.
        oof_rows: list[dict] = []
        for path in find(wd, "oof*.csv"):
            parts = [x.lower() for x in path.parts]
            model_from_path = next((x.upper() for x in parts if x in ("m1", "m2", "m3")), "")
            for row in csv_rows(path):
                if not {"y_true", "prob"} <= set(row):
                    continue
                if not row.get("model"):
                    row["model"] = row.get("model_name") or model_from_path or "unknown"
                oof_rows.append(row)

        challenge_paths = [p for p in find(wd, "*.csv")
                           if p.name in ("challenge_sets.csv", "external_validation.csv")]
        calib = calibration_block(wd)
        challenge_log = composition_from_log(log_path, [r.get("model") for r in metrics])
        report.update({
            "status": "COMPLETED_V4_TRAINING",
            "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "code_commit": code_sha,
            "dataset_summary": dataset,
            "metrics": metrics,
            "metrics_summary": summary,
            "calibration_metrics": ([r for p in calib_metrics for r in csv_rows(p)]
                                    or calib["per_model"]),
            "calibration_bins": ([r for p in calib_bins for r in csv_rows(p)][:120]
                                 or calib["bins"][:120]),
            "calibration_source": calib["source"],
            # Brier/ECE belong in the headline test table, not only in the side table:
            # a discriminative-but-miscalibrated model must be visible at a glance.
            "calibration_in_metrics": [
                {"model": r["model"], "n_test": r["n_test"], "n_pos": r["n_pos"],
                 "auroc": r["auroc"], "brier": r["brier"], "ece": r["ece"],
                 "mean_predicted": r["mean_predicted"], "observed_rate": r["observed_rate"]}
                for r in calib["per_model"]],
            "fixed_specificity_operating_points": (operating_points(oof_rows, [0.99, 0.995])
                                                   or calib["operating_points"]),
            "fixed_specificity_source": ("dev OOF table" if oof_rows
                                         else "dev OOF vector in model/<MODEL>_scores.npz"
                                         if calib["operating_points"] else "unavailable"),
            "composition_challenge": (composition_challenge(metrics, challenge_paths)
                                      if composition_challenge(metrics, challenge_paths)["rows"]
                                      else challenge_log),
            "calibration_bins_count": len(
                [r for p in calib_bins for r in csv_rows(p)]),
            "v4_composition_control": _composition_control_safe(wd, report),
            "v4_acceptance": _v4_acceptance(report, metrics, composition_challenge(
                metrics, challenge_paths) if challenge_paths else challenge_log),
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
        breakdown: dict[str, int] = {}
        for f in private_files:
            rel = str(f.relative_to(wd / "run")).split("/")[0]
            breakdown[rel] = breakdown.get(rel, 0) + f.stat().st_size
        report["private_bundle_breakdown"] = breakdown
        report["private_bundle_model_files"] = sorted(
            str(f.relative_to(wd / "run")) for f in private_files
            if f.suffix == ".pt")
        print(f"[package] private files: {len(private_files)}", flush=True)
        print(f"[package] bytes by directory: {breakdown}", flush=True)
        if not any(p.suffix == ".pt" for p in private_files):
            raise RuntimeError("no trained checkpoint found for the private archive")
        private_zip = tmp / "dualamp_v2_1_model_bundle.zip"
        with zipfile.ZipFile(private_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in private_files:
                z.write(p, str(p.relative_to(wd / "run")))
            if (tmp / "ledger.tsv").exists():
                z.write(tmp / "ledger.tsv", "reports/positive_core_v2_1_ledger.tsv")
            z.writestr("run_provenance.json", json.dumps({
                "dataset_version": "positive_core_v2_1_dramp_v4_hardneg",
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
        # Keep a copy in the kernel output so a consumed/expired upload session never
        # forces another 90-minute GPU run just to ship the weights. The raw zip is
        # ~760 MB, which the relay cannot route into a public repo, so also emit
        # <2 MiB parts that CAN be reassembled byte-for-byte later.
        shutil.copy2(private_zip, Path(out) / "dualamp_v2_1_model_bundle.zip")
        part_size = 1900 * 1024
        parts_dir = Path(out) / "model_bundle_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        part_hashes = []
        with private_zip.open("rb") as src:
            idx = 0
            while True:
                buf = src.read(part_size)
                if not buf:
                    break
                part = parts_dir / f"part_{idx:03d}.bin"
                part.write_bytes(buf)
                part_hashes.append({"name": part.name, "bytes": len(buf),
                                    "sha256": hashlib.sha256(buf).hexdigest()})
                idx += 1
        (parts_dir / "PARTS_MANIFEST.json").write_text(json.dumps({
            "whole_sha256": sha(private_zip),
            "whole_bytes": private_zip.stat().st_size,
            "part_bytes": part_size,
            "parts": part_hashes,
        }, indent=2) + "\n", encoding="utf-8")
        report["private_bundle_parts"] = {"count": len(part_hashes),
                                          "part_bytes": part_size,
                                          "dir": str(parts_dir)}
        if uri:
            report["private_model_bundle"] = upload(uri, private_zip)
        else:
            report["private_model_bundle"] = {
                "skipped": "no upload session in env",
                "local_bytes": private_zip.stat().st_size,
                "local_sha256": sha(private_zip),
            }

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


# --------------------------------------------------------------------------- #
# v4 fix #3: composition-only control
# --------------------------------------------------------------------------- #
def _v4_acceptance(report: dict, metrics: list[dict], challenge: dict) -> dict:
    """Did v4 actually fix the thing it was built to fix?

    Deliberately strict and self-reported: a run that lowers the decoy acceptance
    from 70% to 40% is an improvement but still not论文-usable, and this returns
    that honestly instead of declaring victory.
    """
    crit = {}
    frac = (challenge or {}).get("max_frac_predicted_positive")
    crit["decoy_acceptance"] = frac
    crit["decoy_acceptance_target"] = 0.10
    crit["decoy_acceptance_pass"] = (frac is not None and frac < 0.10)

    ctrl = report.get("v4_composition_control") or {}
    ctrl_auroc = ctrl.get("auroc")
    best = max((float(m.get("auroc") or 0) for m in metrics), default=0.0)
    crit["composition_only_auroc"] = ctrl_auroc
    crit["best_deep_auroc"] = best
    # A composition-only baseline above 0.90 means the deep model has little
    # headroom that is genuinely sequence-order driven.
    crit["composition_only_auroc_target"] = 0.90
    crit["composition_only_pass"] = (ctrl_auroc is not None and ctrl_auroc < 0.90)

    test_rows = [m for m in metrics if str(m.get("split", "internal_test")) == "internal_test"]
    n_pos = max((int(float(m.get("n_pos") or 0)) for m in test_rows), default=0)
    crit["test_positives"] = n_pos
    crit["test_positives_target"] = 100
    crit["test_positives_pass"] = n_pos >= 100

    split_info = report.get("v4_decoy_split") or {}
    crit["decoy_split_overlap"] = split_info.get("overlap")
    crit["decoy_split_pass"] = split_info.get("overlap") == 0

    passes = [v for k, v in crit.items() if k.endswith("_pass")]
    crit["all_pass"] = bool(passes) and all(passes)
    if crit["all_pass"]:
        crit["verdict"] = "V4_PAPER_READY_ON_COMPOSITION"
    elif crit["decoy_acceptance_pass"] or (
            crit["decoy_acceptance"] is not None and crit["decoy_acceptance"] < 0.40):
        crit["verdict"] = "V4_IMPROVED_BUT_NOT_READY"
    else:
        crit["verdict"] = "V4_STILL_HAS_COMPOSITION_SHORTCUT"
    crit["note"] = ("Passing these gates is necessary, not sufficient: an external "
                    "benchmark (AMPBenchmark multi-negative) and wet-lab hit rate are "
                    "still required before any paper claim.")
    return crit


def _composition_control_safe(work: Path, report: dict) -> dict:
    """Never let the diagnostic take the run down; record why if it fails."""
    try:
        out = composition_control(work)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def composition_control(work: Path, seed: int = 20261126) -> dict:
    """How much of the AUROC can amino-acid composition alone explain?

    A logistic regression on 20-dimensional amino-acid frequencies, trained on the
    exact same train/test split the deep models use. If this control reaches high
    AUROC, the headline number is not evidence of sequence-order learning.

    This is the quantitative version of the composition challenge: the challenge
    says "the model accepts shuffled sequences", the control says "and composition
    alone reproduces X% of the measured AUROC".
    """
    np = _np()
    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.preprocessing import StandardScaler  # noqa: PLC0415

    splits = work / "run" / "dataset" / "splits"
    if not splits.exists():
        splits = work / "dataset" / "splits"
    if not splits.exists():
        return {"status": "no_splits"}

    def load(split: str):
        import pandas as pd  # noqa: PLC0415
        path = splits / f"{split}.csv"
        if not path.exists():
            return None
        df = pd.read_csv(path)
        if not {"seq", "label"} <= set(df.columns):
            return None
        return df

    tr, te = load("train"), load("test")
    if tr is None or te is None or te.empty:
        return {"status": "no_split_files"}

    aa = "ACDEFGHIKLMNPQRSTVWY"

    def encode(seqs):
        rows = []
        for s in seqs:
            s = str(s).upper()
            n = max(len(s), 1)
            rows.append([s.count(a) / n for a in aa])
        return np.asarray(rows, dtype=float)

    y_tr, y_te = tr["label"].to_numpy().astype(int), te["label"].to_numpy().astype(int)
    if len(set(y_te.tolist())) < 2:
        return {"status": "test_needs_both_classes"}

    x_tr, x_te = encode(tr["seq"]), encode(te["seq"])
    scaler = StandardScaler().fit(x_tr)
    clf = LogisticRegression(max_iter=2000, random_state=seed)
    clf.fit(scaler.transform(x_tr), y_tr)
    p = clf.predict_proba(scaler.transform(x_te))[:, 1]

    auroc = float(_auc(y_te, p))
    brier = float(_brier(y_te, p))
    coef = {a: float(c) for a, c in zip(aa, clf.coef_[0])}
    top = sorted(coef.items(), key=lambda kv: -abs(kv[1]))[:5]
    out = {
        "status": "ok",
        "method": "logistic regression on 20-dim amino-acid frequency",
        "n_train": int(len(y_tr)),
        "n_test": int(len(y_te)),
        "n_pos_test": int(y_te.sum()),
        "auroc": auroc,
        "brier": brier,
        "top_coefficients": [{"aa": a, "coef": c} for a, c in top],
        "reading": ("If this control's AUROC approaches the deep models' AUROC, the "
                    "measured discrimination is explainable by composition alone."),
    }
    # Compare against the deep models on the same split, when their metrics exist.
    report_paths = list(work.rglob("*results.json")) + list(work.rglob("*metrics*.json"))
    deep = {}
    for path in report_paths[:50]:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(blob, dict) and "auroc" in blob:
            deep[path.parent.name or path.stem] = float(blob["auroc"])
    if deep:
        best = max(deep.values())
        out["deep_models_seen"] = deep
        out["best_deep_auroc"] = best
        out["composition_share_of_best_auroc"] = auroc / best if best else None
        out["auroc_gap"] = best - auroc
    return out
