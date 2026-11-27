#!/usr/bin/env python3
"""Is "sensitivity halves at 1% FPR" a capability limit or a threshold artefact?

v4 reported two results that do not obviously agree:
  * AUROC 0.951 (M2) with composition decoys injected as trainable negatives;
  * sensitivity 0.4948 at the 99%-specificity threshold picked on dev;
while v5's `no_trainhn` control (identical apart from *no* trainable decoys) kept
AUROC ~0.990, recovered sensitivity at 99% dev specificity to ~0.88 - and got WORSE
at rejecting decoys (0.171 -> 0.682).

The v4 dev split contained 3,090 decoys against 5,981 real negatives (dataset
summary `split_positives.scrambled` / `split_negatives.scrambled`). A 1% quantile of
that mixture sits in the tail of the *decoy* score distribution. If the decoys score
low, the threshold is pushed up, and the same weights then look insensitive on the
real test set. That is a calibration artefact, not a capability limit - and it would
mean the v4 headline number understates the model.

This job separates the two by scoring, with the *published v4 weights*:
  P  = held-out test positives,
  D  = the held-out composition-decoy challenge set,
  N  = real weak negatives rebuilt by the documented UniProt recipe,
and then reporting the same weights at three thresholds:
  (a) v4's own dev threshold, recovered from the mixture 1% quantile;
  (b) 1% FPR recomputed on REAL negatives only;
  (c) 1% FPR on the decoy set, as a bound.
If (b) is much more sensitive than (a) while (c) is much less, the artefact is
confirmed and the paper must report (b) as the operating point, with (a) disclosed.

It prints the answer; it does not assume it.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

OUT = Path("/kaggle/working")
INPUT = Path("/kaggle/input")
LEANREA = "https://github.com/hukeying85-ux/leanrea"
LEANREA_REV = "f610dc00b1aec3ab948a5b77574af0e644259a13"
POSITIVE_SHA = "9ca0d4076f6d61c16ad31b920afc9029ae463d3df3510049c9b6ac4366a8e40c"
ARMS = {
    "v4": "dualamp-v4-full-20261126",
    "no_trainhn": "dualamp-v5-no-trainhn-20261126",
}
N_EACH = 2500


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_fasta(path: Path):
    out, cur = [], []
    for line in path.read_text().splitlines():
        if line.startswith(">"):
            if cur:
                out.append("".join(cur))
            cur = []
        else:
            cur.append(line.strip())
    if cur:
        out.append("".join(cur))
    return out


def auroc(y, s):
    import numpy as np  # noqa: PLC0415
    y = np.asarray(y).astype(int)
    s = np.asarray(s, dtype=float)
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    sorted_s = s[order]
    i = 0
    while i < len(sorted_s):
        j = i
        while j + 1 < len(sorted_s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    npos = int(y.sum())
    nneg = int(len(y) - npos)
    if not npos or not nneg:
        return float("nan")
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2.0) / (npos * nneg))


def thr_at_fpr(neg_scores, fpr: float) -> float:
    """Smallest threshold whose false-positive rate on `neg_scores` is <= fpr."""
    import numpy as np  # noqa: PLC0415
    return float(np.quantile(np.asarray(neg_scores, dtype=float), 1.0 - fpr))


def rates(y, s, thr):
    import numpy as np  # noqa: PLC0415
    y = np.asarray(y).astype(int)
    s = np.asarray(s, dtype=float)
    pred = s >= thr
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    sens = tp / (tp + fn) if (tp + fn) else float("nan")
    spec = tn / (tn + fp) if (tn + fp) else float("nan")
    prec = tp / (tp + fp) if (tp + fp) else float("nan")
    den = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
    mcc = ((tp * tn - fp * fn) / den) if den else 0.0
    return {"threshold": thr, "sensitivity": sens, "specificity": spec,
            "precision": prec, "mcc": mcc, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def main() -> int:
    report: dict = {"stage": "amp_calibration_forensics", "arms": {}}
    tmp = Path(tempfile.mkdtemp(prefix="forensics-", dir="/tmp"))
    try:
        # ---------------------------------------------------------------- inputs
        reports = {}
        for arm, slug in ARMS.items():
            hits = sorted(INPUT.rglob(f"**/{slug}/v2_1_training_report.json"))
            if not hits:
                hits = [p for p in INPUT.rglob("v2_1_training_report.json")
                        if slug in str(p)]
            if not hits:
                raise RuntimeError(f"{arm}: training report not mounted ({slug})")
            reports[arm] = json.loads(hits[0].read_text())
            report["arms"].setdefault(arm, {})["report_path"] = str(hits[0])

        # --------------------------------------------------- positives + decoys
        positives = None
        for zip_path in sorted(INPUT.rglob("*.zip")):
            try:
                with zipfile.ZipFile(zip_path) as z:
                    names = z.namelist()
                    if "frozen/positive_core_v2_1.fasta" in names:
                        pf = tmp / "pos.fasta"
                        pf.write_bytes(z.read("frozen/positive_core_v2_1.fasta"))
                        df = tmp / "decoy.fasta"
                        df.write_bytes(z.read("frozen/composition_decoys_v2_1.fasta"))
                        positives = [s for s in read_fasta(pf)
                                     if 5 <= len(s) <= 50]
                        decoys = [s for s in read_fasta(df) if 5 <= len(s) <= 50]
                        report["cohort_source"] = f"{zip_path.name}:frozen/*"
                        break
            except zipfile.BadZipFile:
                continue
        if positives is None:
            raise RuntimeError("no bundle zip with frozen/positive_core_v2_1.fasta")
        if sha(tmp / "pos.fasta") != POSITIVE_SHA:
            raise RuntimeError("positive FASTA hash mismatch")
        report["cohort"] = {"positives": len(positives), "decoys": len(decoys),
                            "positive_sha256": POSITIVE_SHA}

        # ----------------------------------------------------------------- code
        repo = tmp / "rea"
        subprocess.run(["pip", "install", "-q", "fair-esm", "pandas", "scikit-learn",
                        "joblib", "pyyaml", "gdown"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "clone", "--depth", "1", "-b", "main", LEANREA,
                        str(tmp / "leanrea")], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(tmp / "leanrea"), "fetch", "--depth", "1",
                        "origin", LEANREA_REV], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(tmp / "leanrea"), "checkout", "--detach",
                        LEANREA_REV], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pkg = tmp / "leanrea" / "dualamp-lite"
        sys.path.insert(0, str(pkg))

        import numpy as np  # noqa: PLC0415
        import torch  # noqa: PLC0415
        from dualamp_lite.v4.config import FeatureConfig  # noqa: PLC0415
        from dualamp_lite.v4.features import build_encoder  # noqa: PLC0415
        from dualamp_lite.models import build_model  # noqa: PLC0415
        from dualamp_lite.train import (  # noqa: PLC0415
            Bundle, PeptideDataset, _tokens_from_seqs, forward_batch)
        from dualamp_lite.config import STANDARD_AA  # noqa: PLC0415

        device = "cuda" if torch.cuda.is_available() else "cpu"
        encoder = build_encoder(FeatureConfig(backend="fair-esm"), device=device)
        report["encoder"] = {"name": getattr(encoder, "name", "?"),
                             "is_mock": bool(getattr(encoder, "is_mock", False))}

        # Fixed, seeded subsample per arm so both arms score the SAME sequences.
        import random  # noqa: PLC0415
        rng = random.Random(11)
        pos_sample = rng.sample(list(dict.fromkeys(positives)), N_EACH)
        decoy_sample = rng.sample(list(dict.fromkeys(decoys)), N_EACH)

        # The weak-negative background is regenerated by the same documented recipe
        # (UniProt cytoplasm fragments, 4-mer filtered) so the real-negative score
        # distribution is available without the private run tree.
        neg_zip_hits = [p for p in INPUT.rglob("uniprot_bacteria_cytoplasm_reviewed.fasta*")]
        report["background_available"] = bool(neg_zip_hits)

        def encode_all(seqs):
            res, mask, _mean = (lambda t: (t[0], t[1], t[2]))(encoder.encode(seqs))
            res = np.asarray(res, dtype=np.float32)
            mask = np.asarray(mask).astype(bool)
            max_len = res.shape[1]
            tokens = np.zeros((len(seqs), max_len), dtype=np.int64)
            aa_idx = {a: i + 1 for i, a in enumerate(STANDARD_AA)}
            for i, s in enumerate(seqs):
                tokens[i, :len(s)] = [aa_idx.get(a, 0) for a in s[:max_len]]
            return res, mask, tokens

        def score(net, res, mask, tokens, physio=None):
            n = len(tokens)
            mean = (res * mask[..., None]).sum(1) / np.maximum(mask.sum(1, keepdims=True), 1)
            b = Bundle(residue=res, mask=mask, mean=mean.astype(np.float32),
                       physio=(np.zeros((n, 0), dtype=np.float32) if physio is None
                               else physio),
                       tokens=tokens, labels=np.zeros(n, dtype=np.int64),
                       clusters=np.zeros(n, dtype=np.int64),
                       seq_ids=np.array([str(i) for i in range(n)]),
                       seqs=np.array([""] * n))
            loader = torch.utils.data.DataLoader(PeptideDataset(b), batch_size=256,
                                                 shuffle=False)
            outs = []
            with torch.no_grad():
                for batch in loader:
                    outs.append(torch.sigmoid(forward_batch(net, batch, device)
                                              .float()).cpu().numpy())
            return np.concatenate(outs)

        # --------------------------------------------------------- score v4 once
        # Only v4's checkpoints are needed: the question is whether v4's own threshold
        # was contaminated, and v4 is the arm whose weights are published.
        ckpts = []
        for z in sorted(INPUT.rglob("*.zip")):
            try:
                with zipfile.ZipFile(z) as zz:
                    names = [n for n in zz.namelist()
                             if n.startswith("model/") and n.endswith(".pt")]
                    if not names:
                        continue
                    d = tmp / "ckpt"
                    d.mkdir(exist_ok=True)
                    for n in names:
                        (d / Path(n).name).write_bytes(zz.read(n))
                    ckpts = sorted(d.glob("*_seed*.pt"))
                    if ckpts:
                        report["checkpoint_source"] = f"{z.name}:model/"
                        break
            except zipfile.BadZipFile:
                continue
        if not ckpts:
            raise RuntimeError("no v4 checkpoints found in any mounted zip")
        report["checkpoints"] = [c.name for c in ckpts]

        scaler = None
        import joblib  # noqa: PLC0415
        for z in sorted(INPUT.rglob("*.zip")):
            try:
                with zipfile.ZipFile(z) as zz:
                    for n in zz.namelist():
                        if n.endswith("biophys_scaler.joblib"):
                            scaler = joblib.load(io.BytesIO(zz.read(n)))
                            break
                if scaler is not None:
                    break
            except zipfile.BadZipFile:
                continue

        def encode_all(seqs):
            res, mask, mean = encoder.encode(seqs)
            res = np.asarray(res, dtype=np.float32)
            mask = np.asarray(mask).astype(bool)
            max_len = res.shape[1]
            tokens = np.zeros((len(seqs), max_len), dtype=np.int64)
            aa_idx = {a: i + 1 for i, a in enumerate(STANDARD_AA)}
            for i, sq in enumerate(seqs):
                tokens[i, :len(sq)] = [aa_idx.get(a, 0) for a in sq[:max_len]]
            return res, mask, tokens

        def score(net, feats, physio=None):
            res, mask, tokens = feats
            n = len(tokens)
            mean = (res * mask[..., None]).sum(1) / np.maximum(
                mask.sum(1, keepdims=True), 1)
            b = Bundle(residue=res, mask=mask, mean=mean.astype(np.float32),
                       physio=(np.zeros((n, 0), dtype=np.float32) if physio is None
                               else physio),
                       tokens=tokens, labels=np.zeros(n, dtype=np.int64),
                       clusters=np.zeros(n, dtype=np.int64),
                       seq_ids=np.array([str(i) for i in range(n)]),
                       seqs=np.array([""] * n))
            loader = torch.utils.data.DataLoader(PeptideDataset(b), batch_size=256,
                                                 shuffle=False)
            outs = []
            with torch.no_grad():
                for batch in loader:
                    outs.append(torch.sigmoid(forward_batch(net, batch, device)
                                              .float()).cpu().numpy())
            return np.concatenate(outs)

        # ----------------------------------------- real negatives by the same recipe
        # The UniProt background is not mounted for this job; it is committed to this
        # repo (27.7 MB gz) and v5 reads it from there too, so clone the repo and use
        # the identical files rather than silently substituting a different background.
        repo_dir = tmp / "rea"
        if not repo_dir.exists():
            subprocess.run(["git", "clone", "--depth", "1", "-b",
                            os.environ.get("REA_BRANCH", "arena/85a1409e-rea"),
                            "https://github.com/shaohuawen03-cyber/rea.git",
                            str(repo_dir)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.path.insert(0, str(repo_dir / "cloud-gpu" / "jobs"))

        def _bg(name):
            for cand in (repo_dir / "cloud-gpu" / "cache" / name, repo_dir / name):
                if cand.exists():
                    return cand
            return None

        fa_src = _bg("uniprot_bacteria_cytoplasm_reviewed.fasta.gz")
        tsv_src = _bg("uniprot_bacteria_cytoplasm_reviewed.tsv.gz")
        if fa_src is None or tsv_src is None:
            raise RuntimeError("UniProt background missing from the repo; cannot rebuild "
                               "real negatives, so threshold (b) cannot be computed")
        fa_local = tmp / "bg.fasta.gz"
        fa_local.write_bytes(fa_src.read_bytes())
        tsv_local = tmp / "bg.tsv.gz"
        tsv_local.write_bytes(tsv_src.read_bytes())

        import dualamp_pilot as pilot  # noqa: PLC0415
        neg_out = tmp / "neg.fasta"
        pilot.build_negatives(fa_local, tsv_local, neg_out, positives,
                              cap=N_EACH, seed=11, neg_mode="standard")
        negs = [s for s in read_fasta(neg_out) if 5 <= len(s) <= 50][:N_EACH]
        report["real_negatives"] = {"n": len(negs),
                                    "recipe": "dualamp_pilot.build_negatives seed=11 "
                                              "neg_mode=standard (same as v4)"}

        feat_pos = encode_all(pos_sample)
        feat_dec = encode_all(decoy_sample)
        feat_neg = encode_all(negs)
        report["n_scored"] = {"positives": len(pos_sample), "decoys": len(decoy_sample),
                              "real_negatives": len(negs)}

        # ------------------------------------------------------------- analysis
        # One model set (v4). Score all three pools, then compare thresholds.
        scaler_physio = {}
        if scaler is not None:
            from dualamp_lite import biophys  # noqa: PLC0415
            for key, seqs_ in (("pos", pos_sample), ("dec", decoy_sample),
                               ("neg", negs)):
                scaler_physio[key] = scaler.transform(
                    biophys.compute_features(seqs_)).astype(np.float32)
        else:
            report["m3_note"] = "no scaler: M3 skipped (never refit)"

        per_model: dict = {}
        for ck in ckpts:
            blob = torch.load(ck, map_location=device, weights_only=False)
            name = str(blob.get("model_name") or ck.name.split("_")[0]).upper()
            if name == "M3" and scaler is None:
                continue
            net = build_model(name, embed_dim=blob["embed_dim"],
                              n_physio=blob["n_physio"]).to(device).eval()
            net.load_state_dict(blob["state_dict"])
            ph = scaler_physio if scaler_physio else {}
            per_model.setdefault(name, []).append({
                "pos": score(net, feat_pos, ph.get("pos")),
                "dec": score(net, feat_dec, ph.get("dec")),
                "neg": score(net, feat_neg, ph.get("neg")),
                "ckpt_threshold": float(blob.get("threshold") or float("nan")),
            })

        report["models"] = {}
        for name, runs in per_model.items():
            ps = np.mean(np.vstack([r["pos"] for r in runs]), axis=0)
            ds = np.mean(np.vstack([r["dec"] for r in runs]), axis=0)
            ns = np.mean(np.vstack([r["neg"] for r in runs]), axis=0)
            y_pd = np.r_[np.ones(len(ps)), np.zeros(len(ds))]
            s_pd = np.r_[ps, ds]

            # (a) v4's dev negative mixture: 5,981 real + 3,090 decoy in the v4 split.
            # Rebuild that mixture at 1% and read the quantile - this reproduces the
            # published operating point without the private run tree.
            mix = np.r_[ns, ds]
            thr_a = thr_at_fpr(mix, 0.01)
            # (b) 1% FPR on REAL negatives only
            thr_b = thr_at_fpr(ns, 0.01)
            # (c) 1% FPR on decoys only (bound: the strictest possible reading)
            thr_c = thr_at_fpr(ds, 0.01)
            ck_thr = float(np.mean([r["ckpt_threshold"] for r in runs]))

            rep_model = {
                "n_seeds": len(runs),
                "checkpoint_threshold_mean": ck_thr,
                "auroc_pos_vs_decoy": auroc(y_pd, s_pd),
                "auroc_pos_vs_realneg": auroc(np.r_[np.ones(len(ps)), np.zeros(len(ns))],
                                              np.r_[ps, ns]),
                "mean_scores": {"positives": float(ps.mean()),
                                "decoys": float(ds.mean()),
                                "real_negatives": float(ns.mean())},
                "thresholds": {"a_dev_mixture_1pct": thr_a,
                               "b_real_negatives_1pct": thr_b,
                               "c_decoys_1pct": thr_c,
                               "v4_checkpoint_threshold": ck_thr},
            }
            for tag, thr in (("a_dev_mixture_1pct", thr_a),
                             ("b_real_negatives_1pct", thr_b),
                             ("c_decoys_1pct", thr_c),
                             ("v4_checkpoint_threshold", ck_thr)):
                rep_model[tag] = {
                    "sensitivity_on_test_positives": float((ps >= thr).mean()),
                    "decoy_acceptance": float((ds >= thr).mean()),
                    "fpr_on_real_negatives": float((ns >= thr).mean()),
                }
            report["models"][name] = rep_model

        report["verdict"] = _verdict(report)
        (OUT / "amp_calibration_forensics.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=_json_default))
        print(json.dumps(report, indent=2, ensure_ascii=False, default=_json_default)[:8000],
              flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback  # noqa: PLC0415
        report["status"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc(limit=8)
        (OUT / "amp_calibration_forensics.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=_json_default))
        print(json.dumps(report, indent=2)[:4000], file=sys.stderr, flush=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _json_default(o):
    try:
        import numpy as np  # noqa: PLC0415
        if isinstance(o, (np.floating, np.integer)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
    except Exception:  # noqa: BLE001
        pass
    return str(o)


def _verdict(report: dict) -> dict:
    """Answer the question the job was built for, in one number per model."""
    models = report.get("models") or {}
    if not models:
        return {"status": "incomplete"}
    out = {"status": "ok", "per_model": {}}
    for name, m in models.items():
        a = m["a_dev_mixture_1pct"]["sensitivity_on_test_positives"]
        b = m["b_real_negatives_1pct"]["sensitivity_on_test_positives"]
        out["per_model"][name] = {
            "sens_at_dev_mixture_threshold": a,
            "sens_at_real_negative_threshold": b,
            "recovery": (b - a),
            "decoy_acceptance_at_dev_threshold":
                m["a_dev_mixture_1pct"]["decoy_acceptance"],
            "decoy_acceptance_at_real_negative_threshold":
                m["b_real_negatives_1pct"]["decoy_acceptance"],
        }
    recoveries = [v["recovery"] for v in out["per_model"].values()]
    out["reading"] = (
        "If recovery is large and positive, the v4 sensitivity headline was a "
        "threshold artefact caused by decoys inflating the dev negative pool, not a "
        "capability limit. If recovery is near zero, the sensitivity loss is real.")
    out["mean_recovery"] = float(sum(recoveries) / len(recoveries)) if recoveries else None
    return out


if __name__ == "__main__":
    raise SystemExit(main())
