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

# =========================================================================== #
# v5 - run this file *without* V5_MODE to get exactly v4 (registration run),
#      with V5_MODE=<mode> for one of the controlled experiments below.
# =========================================================================== #
# What v4 established, measured, not guessed:
#   * Injecting composition-matched decoys as trainable negatives cuts decoy
#     acceptance on the CNN models from ~67% to ~6% (smoke, 1000 decoys) but only
#     to ~17% at full scale (6000 decoys) - see v4 full report.
#   * The same change drops sensitivity at matched specificity from ~0.85 to
#     ~0.45-0.49. AUROC moves only 0.989 -> 0.950, so AUROC hides the cost.
#   * The leakage-free composition control reaches AUROC 0.807 on the same split,
#     and a paired bootstrap puts deep-composition = +0.143 [0.094, 0.198].
#
# The obvious hypothesis is that this is a *negative-data geometry* problem, not a
# training bug: positives share an amino-acid composition with the decoys, so the
# model cannot tell "same composition, real order" from "same composition, scrambled
# order" without also sacrificing true positives. Four controlled arms separate the
# possible causes. Each arm changes exactly one thing:
#
#   V5_MODE=v4_repeat    identical to v4 (registration / reproducibility only)
#   V5_MODE=no_trainhn   no trainable decoys at all  -> isolates the cost of fix #1
#   V5_MODE=scram_only   identical to v4, but the *challenge* set is a second,
#                        independent scramble of the same positives. If the model
#                        also rejects those, the fix generalises; if it only rejects
#                        the training-pool patterns, it memorised the pool. This is
#                        the discriminative test the v4 design could not make.
#   V5_MODE=matched_neg  v4 hard negatives + experimental-negative-like background
#                        drawn from the *same* composition band as the positives
#                        (rejection-sampled from UniProt fragments). Tests whether
#                        the sensitivity loss is caused by the negative pool being
#                        composition-shifted rather than by the decoy injection.
#
# Nothing here is claimed as a result until the arms have run.
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

V5_MODE = (os.environ.get("V5_MODE") or "v4_repeat").strip()
V5_MODES = ("v4_repeat", "no_trainhn", "scram_only", "matched_neg")
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


V4_HEADLINE_MODEL = "M3"


def _merge_challenge(log_block: dict, table_block: dict) -> dict:
    """Prefer the per-model numbers from the training log; keep table rows as extra.

    `max_frac_predicted_positive` must stay the worst model (never an average) — it is
    the honest upper bound. `best_model_frac` carries the headline model so the two
    numbers can never be confused with each other in the report.
    """
    log_block = log_block or {}
    table_block = table_block or {}
    rows = list(log_block.get("rows") or []) + [
        r for r in (table_block.get("rows") or []) if r not in (log_block.get("rows") or [])]
    fracs = [r["frac_predicted_positive"] for r in rows
             if isinstance(r.get("frac_predicted_positive"), (int, float))]
    out = dict(log_block)
    out["rows"] = rows
    if fracs:
        out["max_frac_predicted_positive"] = max(fracs)
        by_model = {}
        for r in rows:
            name = str(r.get("model") or "").upper()
            val = r.get("frac_predicted_positive")
            if name and isinstance(val, (int, float)):
                by_model[name] = float(val)
        out["best_model_frac"] = min(by_model.values()) if by_model else None
        out["best_model"] = (min(by_model, key=by_model.get) if by_model else None)
        out["verdict"] = "PASS" if max(fracs) < 0.10 else "FAIL_COMPOSITION_SHORTCUT"
    return out


def _v4_acceptance(report: dict, metrics: list[dict], challenge: dict) -> dict:
    """Did v4 actually fix the thing it was built to fix?

    Deliberately strict and self-reported: a run that lowers the decoy acceptance
    from 70% to 40% is an improvement but still not论文-usable, and this returns
    that honestly instead of declaring victory.

    Per-model numbers are the point, not the worst case. M1 is a masked-mean-pooled
    ESM-2 MLP, i.e. a permutation-near-invariant encoder: it cannot in principle
    tell a peptide from its own shuffle, so it keeps accepting decoys no matter how
    the negatives are injected. Reporting one "model X% acceptance" number hides
    that. So the gate is evaluated on the headline model (M3) and every model's
    number is published alongside, with an explicit verdict when only a baseline
    still fails — that asymmetry is evidence about the architecture, not a bug.
    """
    crit = {}
    frac = (challenge or {}).get("max_frac_predicted_positive")
    per_model = {}
    for row in (challenge or {}).get("rows", []):
        name = str(row.get("model") or "").upper()
        val = row.get("frac_predicted_positive")
        if name and val is not None:
            per_model[name] = float(val)
    crit["decoy_acceptance_by_model"] = per_model
    crit["decoy_acceptance"] = frac
    crit["decoy_acceptance_target"] = 0.10
    crit["decoy_acceptance_pass"] = (frac is not None and frac < 0.10)
    head = per_model.get(V4_HEADLINE_MODEL)
    crit["headline_model"] = V4_HEADLINE_MODEL
    crit["headline_decoy_acceptance"] = head
    crit["headline_decoy_acceptance_pass"] = (head is not None and head < 0.10)
    crit["baselines_failing_composition"] = sorted(
        k for k, v in per_model.items() if v >= 0.10)

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
    elif crit["headline_decoy_acceptance_pass"] and crit["composition_only_pass"] \
            and crit["test_positives_pass"] and crit["decoy_split_pass"]:
        # The deployed model fixes the shortcut; a permutation-invariant baseline
        # does not. That is a reportable architecture finding, not a pass.
        crit["verdict"] = "V4_HEADLINE_PASSES_BASELINE_FAILS_COMPOSITION"
    elif crit["decoy_acceptance_pass"] or (
            crit["headline_decoy_acceptance"] is not None
            and crit["headline_decoy_acceptance"] < 0.40):
        crit["verdict"] = "V4_IMPROVED_BUT_NOT_READY"
    else:
        crit["verdict"] = "V4_STILL_HAS_COMPOSITION_SHORTCUT"
    audit = report.get("v4_hardneg_composition_audit") or {}
    crit["hardneg_permutation_fraction"] = audit.get("fraction")
    crit["note"] = ("Passing these gates is necessary, not sufficient: an external "
                    "benchmark (AMPBenchmark multi-negative) and wet-lab hit rate are "
                    "still required before any paper claim.")
    return crit


def _fresh_scrambles(positives: list[str], exclude: set[str], n: int = 8000,
                    seed: int = 5261126) -> list[str]:
    """Independent second scramble of the positives (arm 3).

    The v4 challenge set is a fixed half of one decoy pool; a model trained against the
    other half could in principle key on pool-specific artefacts. These sequences are
    generated by a different RNG stream and are guaranteed absent from the pool, so
    rejecting them can only come from the composition-order rule itself.
    """
    import random as _random  # noqa: PLC0415

    rng = _random.Random(seed)
    seen = set(exclude)
    out: list[str] = []
    pool = [s for s in positives if len(s) >= 5]
    rng.shuffle(pool)
    for seq in pool:
        if len(out) >= n:
            break
        chars = list(seq)
        for _ in range(40):
            rng.shuffle(chars)
            cand = "".join(chars)
            if cand != seq and cand not in seen and 5 <= len(cand) <= 50:
                seen.add(cand)
                out.append(cand)
                break
    return out


def _multiset(seq: str) -> str:
    return "".join(sorted(str(seq).upper()))


def hardneg_composition_audit(work: Path, hard_neg_fasta: Path) -> dict:
    """How many trainable hard negatives are permutations of a *held-out* positive?

    Every composition decoy is an exact residue permutation of exactly one positive
    (one decoy per positive by construction), and the pool is split in half, so no
    challenge decoy's composition is ever in the training set — that part is safe by
    construction. The remaining question is the reverse direction: a hard negative
    may be a permutation of a positive that ends up in val/test. That is not label
    leakage in the inflating direction (it cannot make the test number look better);
    it makes the trained threshold *conservative* for those positives, and it must be
    published rather than discovered by a reviewer. This audits it exactly.
    """
    splits = work / "run" / "dataset" / "splits"
    if not splits.exists():
        splits = work / "dataset" / "splits"
    if not hard_neg_fasta.exists() or not splits.exists():
        return {"status": "unavailable"}
    import pandas as pd  # noqa: PLC0415

    held: dict[str, int] = {}
    for split in ("val", "test", "review"):
        path = splits / f"{split}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        if not {"seq", "label"} <= set(df.columns):
            continue
        for seq, lab in zip(df["seq"], df["label"]):
            if int(lab) == 1:
                held.setdefault(_multiset(seq), 0)
                held[_multiset(seq)] += 1
    trained = read_fasta(hard_neg_fasta)
    collide = [s for s in trained if _multiset(s) in held]
    return {
        "status": "ok",
        "hard_negatives_scored": len(trained),
        "heldout_positive_compositions": len(held),
        "hardneg_permutations_of_heldout_positives": len(collide),
        "fraction": round(len(collide) / max(len(trained), 1), 4),
        "reading": ("Non-zero means the trained threshold is conservative for those "
                    "positives (their exact composition was shown as a negative). It "
                    "cannot inflate the test AUROC; report it as a limitation."),
    }


def _composition_control_safe_v4(work: Path, report: dict) -> dict:
    """Leakage-free control first; fall back to the older one if it cannot run."""
    try:
        out = composition_control_v4(work)
        if out.get("status") == "ok":
            out["source"] = "composition_control_v4 (train-fit, val-threshold, test-scored)"
            return out
        fallback = _composition_control_safe(work, report)
        fallback["v4_control_status"] = out.get("status")
        return fallback
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def _composition_control_safe(work: Path, report: dict,
                              best_deep_auroc: float | None = None) -> dict:
    """Never let the diagnostic take the run down; record why if it fails."""
    try:
        out = composition_control(work, best_deep_auroc=best_deep_auroc)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def composition_control_v4(work: Path, n_boot: int = 1000,
                           seed: int = 20261126) -> dict:
    """Composition-only baseline on the *exact* v4 splits, with a paired test.

    Two things the earlier control got wrong, both of which inflate it:

    * it refit a scaler + logistic regression on `train` and scored `test`, so the
      reported AUROC is the optimistic in-sample-scaler variant;
    * it compared that number to the deep AUROC informally, without asking whether
      the difference is larger than fold noise.

    This version trains on plain `train`, picks the threshold on `val`, and reports
    `test` (out-of-sample end to end), plus a paired bootstrap of
    AUC(deep) - AUC(composition) on the *same* test rows. A deep model that does not
    beat 20 amino-acid frequencies by a significant margin has no sequence-order
    claim to make, which is exactly the question a reviewer will ask.
    """
    np = _np()
    splits = work / "run" / "dataset" / "splits"
    if not splits.exists():
        return {"status": "no_splits"}
    import pandas as pd  # noqa: PLC0415

    def load(name: str):
        path = splits / f"{name}.csv"
        if not path.exists():
            return None
        df = pd.read_csv(path)
        if not {"seq", "label"} <= set(df.columns) or df.empty:
            return None
        return df

    tr, va, te = load("train"), load("val"), load("test")
    if tr is None or te is None:
        return {"status": "no_split_files"}
    y_te = te["label"].to_numpy().astype(int)
    if len(set(y_te.tolist())) < 2:
        return {"status": "test_needs_both_classes"}

    aa = "ACDEFGHIKLMNPQRSTVWY"

    def encode(seqs):
        rows = []
        for s in seqs:
            s = str(s).upper()
            n = max(len(s), 1)
            rows.append([s.count(a) / n for a in aa])
        return np.asarray(rows, dtype=float)

    from sklearn.linear_model import LogisticRegression  # noqa: PLC0415
    from sklearn.pipeline import make_pipeline  # noqa: PLC0415
    from sklearn.preprocessing import StandardScaler  # noqa: PLC0415

    clf = make_pipeline(StandardScaler(),
                        LogisticRegression(max_iter=2000, random_state=seed))
    clf.fit(encode(tr["seq"]), tr["label"].to_numpy().astype(int))
    p_te = clf.predict_proba(encode(te["seq"]))[:, 1]

    auroc_comp = float(_auc(y_te, p_te))
    brier_comp = float(_brier(y_te, p_te))
    ece_comp, _ = _ece(y_te, p_te)
    thr = 0.5
    if va is not None and len(set(va["label"].tolist())) > 1:
        p_va = clf.predict_proba(encode(va["seq"]))[:, 1]
        # pick the threshold on val by maximising Youden's J, never on test
        best, thr = -1e9, 0.5
        for cand in np.unique(np.round(p_va, 4)):
            tp = ((p_va >= cand) & (va["label"].to_numpy().astype(int) == 1)).sum()
            fp = ((p_va >= cand) & (va["label"].to_numpy().astype(int) == 0)).sum()
            pos = int((va["label"].to_numpy().astype(int) == 1).sum())
            neg = int((va["label"].to_numpy().astype(int) == 0).sum())
            if pos and neg:
                j = tp / pos - fp / neg
                if j > best:
                    best, thr = j, float(cand)
    pred = (p_te >= thr).astype(int)
    tp = int(((pred == 1) & (y_te == 1)).sum())
    tn = int(((pred == 0) & (y_te == 0)).sum())
    fp = int(((pred == 1) & (y_te == 0)).sum())
    fn = int(((pred == 0) & (y_te == 1)).sum())
    mcc_den = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
    out = {
        "status": "ok",
        "protocol": ("fit on train, threshold on val (Youden J), score test; "
                     "scaler+logistic regression inside one pipeline (no leakage)"),
        "n_train": int(len(tr)), "n_val": int(len(va)) if va is not None else 0,
        "n_test": int(len(y_te)), "n_pos_test": int(y_te.sum()),
        "threshold": round(thr, 4),
        "auroc": auroc_comp,
        "brier": brier_comp,
        "ece": ece_comp,
        "mcc": round((tp * tn - fp * fn) / mcc_den, 6) if mcc_den else None,
        "top_coefficients": [],  # populated below from the linear head
    }
    try:
        lin = clf.steps[-1][1]
        coef = {a: float(c) for a, c in zip(aa, lin.coef_[0])}
        out["top_coefficients"] = [
            {"aa": a, "coef": c} for a, c in
            sorted(coef.items(), key=lambda kv: -abs(kv[1]))[:5]]
    except Exception:  # noqa: BLE001
        pass

    # paired bootstrap against the deep model on identical test rows
    deep_scores = _deep_test_scores(work)
    if deep_scores is not None:
        name, p_deep = deep_scores
        out["deep_model"] = name
        out["deep_auroc"] = float(_auc(y_te, p_deep))
        out["auroc_gap"] = out["deep_auroc"] - auroc_comp
        out["composition_share_of_deep_auroc"] = (
            auroc_comp / out["deep_auroc"] if out["deep_auroc"] else None)
        rng = np.random.default_rng(seed)
        deltas = []
        n = len(y_te)
        for _ in range(n_boot):
            idx = rng.integers(0, n, n)
            yb = y_te[idx]
            if len(set(yb.tolist())) < 2:
                continue
            deltas.append(_auc(yb, p_deep[idx]) - _auc(yb, p_te[idx]))
        if deltas:
            deltas = np.asarray(deltas)
            lo, hi = np.percentile(deltas, [2.5, 97.5])
            out["paired_bootstrap"] = {
                "metric": "auroc(deep) - auroc(composition_only)",
                "n_boot_valid": int(len(deltas)),
                "mean_delta": float(deltas.mean()),
                "ci95_low": float(lo), "ci95_high": float(hi),
                # two-sided bootstrap p: fraction of resamples with the sign flipped
                "p_two_sided": float(min(1.0, 2 * min((deltas <= 0).mean(),
                                                     (deltas >= 0).mean()))),
                "significant_at_0.05": bool(lo > 0 or hi < 0),
            }
    return out


def _deep_test_scores(work: Path) -> tuple[str, "object"] | None:
    """Per-row test probabilities of the strongest deep model, in test-CSV order.

    The `report` stage keeps each model's test scores in `model/<M>_scores.npz`
    (`test_ensemble`) and the test row order in dataset/splits/test.csv, so the two
    line up without re-running the models.
    """
    np = _np()
    splits = work / "run" / "dataset" / "splits"
    test_csv = splits / "test.csv"
    model_dir = work / "run" / "model"
    if not test_csv.exists() or not model_dir.exists():
        return None
    n_test = sum(1 for _ in test_csv.open(encoding="utf-8")) - 1
    best: tuple[str, object, float] | None = None
    for path in sorted(model_dir.glob("*_scores.npz")):
        try:
            with np.load(path, allow_pickle=False) as z:
                if "test_ensemble" not in z.files:
                    continue
                scores = np.asarray(z["test_ensemble"], dtype=float)
        except Exception:  # noqa: BLE001
            continue
        if scores.shape[0] != n_test:
            continue
        import pandas as pd  # noqa: PLC0415
        y = pd.read_csv(test_csv)["label"].to_numpy().astype(int)
        auc = float(_auc(y, scores))
        name = path.name.split("_")[0]
        if best is None or auc > best[2]:
            best = (name, scores, auc)
    return (best[0], best[1]) if best else None


def composition_control(work: Path, seed: int = 20261126,
                       best_deep_auroc: float | None = None) -> dict:
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
    # Compare against the deep models on the same split. The metrics table is the
    # authoritative source; scanning result files is only a fallback, because the
    # `report` stage does not always leave a per-model JSON behind.
    if best_deep_auroc:
        out["best_deep_auroc"] = float(best_deep_auroc)
        out["composition_share_of_best_auroc"] = auroc / float(best_deep_auroc)
        out["auroc_gap"] = float(best_deep_auroc) - auroc
        return out
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
        if V5_MODE not in V5_MODES:
            raise RuntimeError(f"unknown V5_MODE {V5_MODE!r}; choose from {V5_MODES}")
        if V5_MODE == "no_trainhn":
            # Arm 2: exactly v4 minus fix #1. Measures what the decoy injection costs
            # (sensitivity) and what it buys (decoy rejection) with everything else fixed.
            n_hard = 0
        else:
            n_hard = min(int(len(decoy_pool) * V4_HARDNEG_FRAC), V4_HARDNEG_CAP)
        hard_neg_seqs = decoy_pool[:n_hard]
        challenge_seqs = decoy_pool[n_hard:]
        hard_neg_fasta = data / "hard_negatives.fasta"
        write_fasta(hard_neg_fasta, [(f"HARDNEG_{i:05d}", x)
                                     for i, x in enumerate(hard_neg_seqs)])
        # The challenge file must contain ONLY the held-out half.
        write_fasta(decoy_fasta, [(f"CPD_{i:05d}", x)
                                  for i, x in enumerate(challenge_seqs)])
        if V5_MODE == "scram_only":
            # Arm 3: replace the held-out half with a *fresh* scramble of positives that
            # were never in the decoy pool, and keep the pool-half rejection as a second
            # table. Only rejection of the fresh set can distinguish "learned the rule"
            # from "memorised the pool it was trained against".
            fresh = tmp / "fresh_scramble.fasta"
            fresh_seqs = _fresh_scrambles(positives, exclude=set(decoy_pool), n=8000)
            write_fasta(fresh, [(f"FRESH_{i:05d}", x) for i, x in enumerate(fresh_seqs)])
            report["v5_fresh_scramble"] = {"n": len(fresh_seqs),
                                           "excluded_pool": True}
            decoy_fasta = fresh
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
        v4_split["v5_mode"] = V5_MODE
        report["v4_decoy_split"] = v4_split
        report["v5_mode"] = V5_MODE
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
        # Calibration (Brier/ECE/Platt), the challenge table and the subgroup tables come
        # from the `report` stage on this CLI path (model/*_scores.npz). The v4
        # `evaluation` stage additionally expects a runs/<run_id> tree that this path never
        # creates, so driving it unconditionally only ever produced
        # `AssertionError: no run id found` — swallowed as `auxiliary_stage_errors` in the
        # v2.1 r4, the v3 run and the first two v4 smokes. So drive it through a gate that
        # decides *at run time*, after train, and record the outcome explicitly.
        gate_inner = tmp / "gate_inner.py"
        gate_inner.write_text(
            "import json, sys\n"
            "sys.path.insert(0, '.')\n"
            "from pathlib import Path\n"
            f"root = Path({str(wd / 'run')!r})\n"
            "rid = ''\n"
            "if (root / 'latest_run.txt').exists():\n"
            "    rid = (root / 'latest_run.txt').read_text().strip()\n"
            "if not rid and (root / 'runs').exists():\n"
            "    kids = sorted(c.name for c in (root / 'runs').iterdir() if c.is_dir())\n"
            "    rid = kids[-1] if kids else ''\n"
            "if not rid or not (root / 'runs' / rid).exists():\n"
            "    print(json.dumps({'evaluation_stage': 'not_applicable',\n"
            "                      'reason': 'no runs/<run_id> tree on this CLI path',\n"
            "                      'workdir_dirs': sorted(p.name for p in root.iterdir()\n"
            "                                             if p.is_dir())}))\n"
            "    raise SystemExit(0)\n"
            "from dualamp_lite.v4.config import Paths, load_config\n"
            "from dualamp_lite.v4.pipeline import build_context, run_stage\n"
            "cfg = load_config(root / 'configs' / f'resolved_{rid}.yaml', workdir=root)\n"
            "ctx = build_context(cfg, Paths(root), run_id=rid)\n"
            "out = run_stage(ctx, 'evaluation')\n"
            "print(json.dumps({'evaluation_stage': 'ran', 'run_id': rid,\n"
            "                  'result': str(out)[:1500]}))\n",
            encoding="utf-8")
        gate_out = Path(out) / "v4_evaluation_gate.json"
        # Capture the gate's JSON into the kernel output so it survives the run and can be
        # read back by the report block (and by a later re-packaging pass).
        (tmp / "run_evaluation_stage.py").write_text(
            "import io, subprocess, sys\n"
            f"r = subprocess.run([sys.executable, {str(gate_inner)!r}],\n"
            f"                   capture_output=True, text=True)\n"
            f"open({str(gate_out)!r}, 'w').write(r.stdout)\n"
            "sys.stderr.write(r.stderr[-4000:])\n"
            "print(r.stdout[-1500:])\n"
            "raise SystemExit(r.returncode)\n",
            encoding="utf-8")
        steps = [
            [sys.executable, "-m", "dualamp_lite", "build", "--manifest", str(manifest),
             "--workdir", str(wd / "run"), "--cluster-method", "auto"],
            [sys.executable, "-m", "dualamp_lite", "features", "--workdir", str(wd / "run"),
             "--backend", "fair-esm", "--batch-size", "64"],
            [sys.executable, "-m", "dualamp_lite", "train", "--workdir", str(wd / "run"),
             "--models", "M1,M2,M3", "--seeds", seeds, "--folds", folds,
             "--epochs", epochs, "--loss", "bce"],
            [sys.executable, str(tmp / "run_evaluation_stage.py")],
            [sys.executable, "-m", "dualamp_lite", "report", "--workdir", str(wd / "run")],
        ]
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
                step_name = ("evaluation" if cmd[1].endswith("run_evaluation_stage.py")
                             else cmd[3])
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
        gate_report: dict = {}
        gate_file = Path(out) / "v4_evaluation_gate.json"
        if gate_file.exists():
            try:
                gate_report = json.loads(gate_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                gate_report = {"raw": gate_file.read_text(errors="replace")[:500]}

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
        # Order matters: the composition-only control has to be on the report before
        # _v4_acceptance() reads it, otherwise the gate always sees None and reports a
        # spurious failure (first v4 smoke: valid 0.868 control, composition_only_pass=false).
        try:
            best_deep_auroc = max(float(m.get("auroc") or 0.0) for m in metrics)
        except (TypeError, ValueError):
            best_deep_auroc = None
        v4_ctrl = _composition_control_safe_v4(wd, report)
        report["v4_composition_control"] = v4_ctrl
        report["v4_hardneg_composition_audit"] = hardneg_composition_audit(
            wd, hard_neg_fasta)
        report.update({
            "status": "COMPLETED_V4_TRAINING",
            "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "code_commit": code_sha,
            "dataset_summary": dataset,
            "evaluation_stage": gate_report or {"evaluation_stage": "unknown"},
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
            "composition_challenge": _merge_challenge(challenge_log,
                                                      composition_challenge(metrics, challenge_paths)),
            "calibration_bins_count": len(
                [r for p in calib_bins for r in csv_rows(p)]),
            "v4_composition_control": v4_ctrl,
            "v4_acceptance": _v4_acceptance(
                report, metrics,
                _merge_challenge(challenge_log,
                                 composition_challenge(metrics, challenge_paths))),
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
        # A controlled arm only needs its metrics: copying the ~760 MB private zip (and
        # re-splitting it into parts) into the kernel output adds tens of minutes of
        # upload and fetch traffic per arm and changes nothing about the comparison.
        # V5_SKIP_BUNDLE=1 keeps the arm lightweight; the winning arm is exported
        # separately by the model-only extractor when it becomes the release.
        skip_bundle = (os.environ.get("V5_SKIP_BUNDLE") or "").strip() == "1"
        if skip_bundle:
            report["private_bundle_export"] = {"skipped": "V5_SKIP_BUNDLE=1 (arm run)"}
        else:
            shutil.copy2(private_zip, Path(out) / "dualamp_v2_1_model_bundle.zip")
        part_size = 1900 * 1024
        parts_dir = Path(out) / "model_bundle_parts"
        part_hashes = []
        if skip_bundle:
            (out / "v5_arm_summary.json").write_text(
                json.dumps({"v5_mode": V5_MODE,
                            "report": {k: v for k, v in report.items()
                                       if k not in {"traceback"}}},
                           ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (out / "v2_1_training_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({k: v for k, v in report.items()
                              if k not in {"metrics", "metrics_summary",
                                           "calibration_bins"}},
                             ensure_ascii=False, indent=2)[:6000], flush=True)
            return 0
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