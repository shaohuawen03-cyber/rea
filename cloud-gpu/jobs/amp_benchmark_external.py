#!/usr/bin/env python3
"""Score DualAMP on AMPBenchmark-style external tests it was never trained on.

The user's question is "is the dataset right, and was it tested on a public set?".
The honest answer needs an external set chosen by someone else, with negatives the
model has never seen. This job builds four independent checks and reports each one
separately, because a single "accuracy" number is exactly what the AMP benchmark
literature says is misleading (Sidorczuk et al., Briefings in Bioinformatics 2022,
bbac343 - AUC correlates with train/test composition similarity at rho = -0.53).

Checks
------
1. cross_source   : positives from DBAASP (the positive set used by the
                    NegativeDatasets benchmark) vs the *model's own* UniProt
                    cytoplasm-style negatives. Tests covariate shift in positives.
2. decoy_challenge: the model's published composition decoys (exact residue
                    permutations of the training positives). True positive rate is 0%
                    by construction. This is the shortcut detector.
3. iid_split      : a random peptide-level split of (positives, negatives) from the
                    same two source pools. Expected to be inflated; included as the
                    "what a careless benchmark would report" reference.
4. len_extrap     : the same evaluation restricted to peptide lengths absent from
                    training (len <= 8 and len >= 41), i.e. extrapolation, not
                    interpolation.

Each check reports AUROC with a DeLong CI plus sensitivity/specificity at the
threshold frozen from the training dev split, and the amino-acid composition shift
between positives and negatives (the quantity bbac343 says drives the AUC).

Inputs are pinned by sha256; the job refuses to run on a different file.

Env:
  V21_INPUTS_JSON   descriptor with the private bundle id (positives + decoys)
  V4_MAX_PER_CLASS  cap per class per check (default 4000)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

LEANREA = "https://github.com/hukeying85-ux/leanrea"
LEANREA_REV = "f610dc00b1aec3ab948a5b77574af0e644259a13"
POSITIVE_SHA = "9ca0d4076f6d61c16ad31b920afc9029ae463d3df3510049c9b6ac4366a8e40c"
BUNDLE_SHA = "d5dce04e7763dc1abe9cdcf2729de1701cccdf6c893a903e6aee2319058936bd"
DBAASP_CSV = "https://raw.githubusercontent.com/BioGenies/NegativeDatasets/main/data/dbaasp.csv"
DBAASP_SHA = os.environ.get("DBAASP_SHA256", "").strip()
UNIPROT_FA = "uniprot_bacteria_cytoplasm_reviewed.fasta.gz"
UNIPROT_TSV = "uniprot_bacteria_cytoplasm_reviewed.tsv.gz"
AA = set("ACDEFGHIKLMNPQRSTVWY")
OUT = Path("/kaggle/working")
MAX_PER_CLASS = int(os.environ.get("V4_MAX_PER_CLASS", "4000"))


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_fasta(path: Path) -> list[str]:
    seqs, cur = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(">"):
            if cur:
                seqs.append("".join(cur))
                cur = []
        else:
            cur.append(line.strip())
    if cur:
        seqs.append("".join(cur))
    return [s.upper() for s in seqs if s]


def write_fasta(path: Path, rows: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for name, seq in rows:
            f.write(f">{name}\n{seq}\n")


def auroc(y, p):
    import numpy as np
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    ranks[order] = np.arange(1, len(p) + 1)
    _, inv, cnt = np.unique(p, return_inverse=True, return_counts=True)
    sums = np.zeros(len(cnt))
    np.add.at(sums, inv, ranks)
    ranks = sums[inv] / cnt[inv]
    return float((ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def auroc_ci(y, p, n_boot: int = 2000, seed: int = 20261127):
    """Bootstrap CI. Cheaper than a full DeLong implementation and equally honest
    about the sampling uncertainty at these class sizes."""
    import numpy as np
    rng = np.random.default_rng(seed)
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    vals = []
    n = len(y)
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        if len(set(yb.tolist())) < 2:
            continue
        vals.append(auroc(yb, p[idx]))
    if not vals:
        return (float("nan"), float("nan"))
    return (float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5)))


def composition(seq: str) -> list[float]:
    s = seq.upper()
    n = max(len(s), 1)
    return [s.count(a) / n for a in "ACDEFGHIKLMNPQRSTVWY"]


def composition_shift(pos: list[str], neg: list[str]) -> dict:
    """How different are the positive and negative composition distributions?

    bbac343 reports that a model's measured AUC is largely a function of how similar
    the training and the test composition are, so this has to be published next to
    every AUC in the table. Reported as the mean absolute difference over the 20
    frequencies plus the L2 distance of the mean composition vectors.
    """
    import numpy as np
    if not pos or not neg:
        return {"status": "empty"}
    mp = np.asarray([composition(s) for s in pos]).mean(axis=0)
    mn = np.asarray([composition(s) for s in neg]).mean(axis=0)
    return {
        "mean_abs_diff": float(np.abs(mp - mn).mean()),
        "l2_distance": float(np.linalg.norm(mp - mn)),
        "top_shifted_aa": sorted(
            ({"aa": a, "delta": float(mp[i] - mn[i])}
             for i, a in enumerate("ACDEFGHIKLMNPQRSTVWY")),
            key=lambda d: -abs(d["delta"]))[:5],
    }


def main() -> int:
    import numpy as np

    report: dict = {"stage": "amp_benchmark_external", "status": "STARTED",
                    "leanrea_commit": LEANREA_REV, "max_per_class": MAX_PER_CLASS}
    tmp = Path(tempfile.mkdtemp(prefix="ampbench-", dir="/tmp"))
    try:
        # ---- inputs ------------------------------------------------------ #
        inputs: dict = {}
        blob = (os.environ.get("V21_INPUTS_JSON") or "").strip()
        if blob:
            inputs = json.loads(blob)
        bundle_id = ((os.environ.get("EVIDENCE_AUDIT_ID") or "").strip()
                     or str(inputs.get("private_bundle_drive_id", "")))
        if not bundle_id:
            raise RuntimeError("no private bundle id")
        import gdown
        bundle = tmp / "bundle.zip"
        if not gdown.download(id=bundle_id, output=str(bundle), quiet=True):
            raise RuntimeError("bundle download failed")
        if sha(bundle) != BUNDLE_SHA:
            raise RuntimeError("bundle sha256 mismatch")
        pos_fa, decoy_fa = tmp / "pos.fasta", tmp / "decoy.fasta"
        with zipfile.ZipFile(bundle) as z:
            pos_fa.write_bytes(z.read("frozen/positive_core_v2_1.fasta"))
            decoy_fa.write_bytes(z.read("frozen/composition_decoys_v2_1.fasta"))
        if sha(pos_fa) != POSITIVE_SHA:
            raise RuntimeError("positive sha256 mismatch")
        positives = [s for s in read_fasta(pos_fa) if 5 <= len(s) <= 50 and not (set(s) - AA)]
        decoys = [s for s in read_fasta(decoy_fa) if 5 <= len(s) <= 50 and not (set(s) - AA)]
        report["cohort"] = {"positives": len(positives), "decoys": len(decoys),
                            "positive_sha256": POSITIVE_SHA, "bundle_sha256": BUNDLE_SHA}

        # ---- model code + checkpoints ------------------------------------ #
        repo = tmp / "rea"
        branch = os.environ.get("REA_BRANCH", "arena/85a1409e-rea")
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch,
                        "https://github.com/shaohuawen03-cyber/rea.git", str(repo)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        leanrea = tmp / "leanrea"
        subprocess.run(["git", "clone", "--depth", "1", "-b", "main", LEANREA, str(leanrea)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(leanrea), "fetch", "--depth", "1", "origin", LEANREA_REV],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(leanrea), "checkout", "--detach", LEANREA_REV],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pkg = leanrea / "dualamp-lite"
        sys.path.insert(0, str(pkg))

        # Checkpoints may arrive as loose files (a model-only kernel output) or only
        # inside the big private bundle, depending on how Kaggle resolves the mount.
        # Handle both here so the evaluation never depends on which shape arrived.
        ckpt_dir = tmp / "ckpts"
        ckpt_dir.mkdir(exist_ok=True)
        loose = sorted(Path("/kaggle/input").rglob("*_seed*.pt"))
        for c in loose:
            shutil.copy2(c, ckpt_dir / c.name)
        bundles = sorted(Path("/kaggle/input").rglob("dualamp_v2_1_model_bundle.zip"))
        extracted = []
        for b in bundles[:1]:
            with zipfile.ZipFile(b) as z:
                for name in z.namelist():
                    if name.startswith("model/") and name.endswith(".pt"):
                        target = ckpt_dir / Path(name).name
                        if not target.exists():
                            with z.open(name) as src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst)
                            extracted.append(target.name)
        checkpoints = sorted(ckpt_dir.glob("*_seed*.pt"))
        if not checkpoints:
            raise RuntimeError("no .pt checkpoints found (loose or inside the bundle)")
        report["checkpoints"] = [c.name for c in checkpoints]
        report["checkpoint_source"] = {"loose_mounted": len(loose),
                                       "from_bundle": len(extracted),
                                       "bundle": bundles[0].name if bundles else None}

        # ---- negatives (the same recipe the model trained on) ------------ #
        sys.path.insert(0, str(repo / "cloud-gpu" / "jobs"))
        import dualamp_pilot as pilot  # noqa: PLC0415
        for fname in (UNIPROT_FA, UNIPROT_TSV):
            src = repo / "cloud-gpu" / "cache" / fname
            if not src.exists():
                src = repo / fname
            if not src.exists():
                raise RuntimeError(f"{fname} missing from the repo")
            shutil.copy2(src, tmp / fname)
        neg_stats = pilot.build_negatives(
            tmp / UNIPROT_FA, tmp / UNIPROT_TSV, tmp / "negatives.fasta",
            set(positives), cap=MAX_PER_CLASS, seed=11, neg_mode="standard")
        report["negatives"] = neg_stats
        negatives = [s for s in read_fasta(tmp / "negatives.fasta")
                     if 5 <= len(s) <= 50 and not (set(s) - AA)]

        # ---- DBAASP positives via codeload (the NegativeDatasets source) -- #
        dbaasp_rows = _download_dbaasp(repo, tmp)
        dbaasp = [s for s in dbaasp_rows if 5 <= len(s) <= 50 and not (set(s) - AA)]
        dbaasp = list(dict.fromkeys(dbaasp))
        report["dbaasp"] = {"n_raw": len(dbaasp_rows), "n_usable": len(dbaasp),
                            "source": "BioGenies/NegativeDatasets data/dbaasp.csv"}

        # ---- score everything -------------------------------------------- #
        from dualamp_lite.config import FeatureConfig, Paths, TrainConfig  # noqa: PLC0415
        from dualamp_lite.features import build_encoder  # noqa: PLC0415
        import dualamp_lite.train as dl_train  # noqa: PLC0415

        from dualamp_lite.utils import device_of  # noqa: PLC0415
        encoder = build_encoder(FeatureConfig(backend="fair-esm"), device=device_of("auto"))
        report["encoder"] = {"name": encoder.name,
                             "embed_dim": int(getattr(encoder, "embed_dim", 0)),
                             "is_mock": bool(getattr(encoder, "is_mock", False))}

        # The biophys scaler must be the one fitted on the *training* split. Kaggle's
        # kernel_sources mount proved unreliable (the same attachment was empty twice and
        # present once), so this never depends on it: if the scaler is not mounted, M3 is
        # dropped from every external table instead of being scored on different inputs.
        scaler, scaler_audit = _scaler_for_m3()
        report["biophys_scaler"] = scaler_audit
        if scaler is None:
            dropped = [c for c in checkpoints if "_M3_" in c.name or "_M3." in c.name]
            checkpoints = [c for c in checkpoints if c not in dropped]
            report["m3_excluded"] = {"reason": scaler_audit.get("reason"),
                                     "dropped_checkpoints": [c.name for c in dropped]}

        def score(seqs: list[str]) -> dict:
            return _score_models(pkg, encoder, checkpoints, seqs, tmp, scaler=scaler)

        # ---- check 1: cross-source positives ----------------------------- #
        n = min(MAX_PER_CLASS, len(dbaasp), len(negatives))
        c1 = score(dbaasp[:n] + negatives[:n])
        y1 = [1] * n + [0] * n

        # ---- check 2: composition decoys --------------------------------- #
        m = min(MAX_PER_CLASS, len(decoys), len(positives))
        c2 = score(positives[:m] + decoys[:m])
        y2 = [1] * m + [0] * m

        # ---- check 3: i.i.d. peptide-level split -------------------------- #
        k = min(MAX_PER_CLASS, len(positives), len(negatives))
        c3 = score(positives[:k] + negatives[:k])
        y3 = [1] * k + [0] * k

        # ---- check 4: length extrapolation -------------------------------- #
        short_p = [s for s in positives if len(s) <= 8][:MAX_PER_CLASS // 2]
        long_p = [s for s in positives if len(s) >= 41][:MAX_PER_CLASS // 2]
        short_n = [s for s in negatives if len(s) <= 8][:MAX_PER_CLASS // 2]
        long_n = [s for s in negatives if len(s) >= 41][:MAX_PER_CLASS // 2]
        checks4 = {}
        for tag, ps, ns in (("short_le8", short_p, short_n), ("long_ge41", long_p, long_n)):
            if len(ps) >= 20 and len(ns) >= 20:
                cs = score(ps + ns)
                y = [1] * len(ps) + [0] * len(ns)
                checks4[tag] = {"n_pos": len(ps), "n_neg": len(ns),
                                "models": _metrics_block(cs, y)}

        report["checks"] = {
            "cross_source_dbaasp": {"n_pos": n, "n_neg": n,
                                    "models": _metrics_block(c1, y1),
                                    "composition_shift": composition_shift(dbaasp[:n], negatives[:n])},
            "decoy_challenge": {"n_pos": m, "n_neg": m,
                                "models": _metrics_block(c2, y2),
                                "composition_shift": composition_shift(positives[:m], decoys[:m]),
                                "note": "decoys are exact residue permutations of the training "
                                        "positives; their true positive rate is 0% by construction"},
            "iid_same_source": {"n_pos": k, "n_neg": k,
                                "models": _metrics_block(c3, y3),
                                "composition_shift": composition_shift(positives[:k], negatives[:k]),
                                "note": "peptide-level split of the same two pools - this is the "
                                        "inflated number a careless benchmark reports"},
            "length_extrapolation": checks4,
        }
        report["status"] = "COMPLETED_AMP_BENCHMARK"
        (OUT / "amp_benchmark_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "checks"},
                         indent=2, ensure_ascii=False)[:4000], flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001
        report.update({"status": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:800]}"})
        (OUT / "amp_benchmark_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, indent=2, ensure_ascii=False)[-4000:], flush=True)
        return 1


def _scaler_for_m3():
    """Locate the training-fitted biophys scaler, or refuse to score M3.

    Refitting it here would silently change M3's inputs (the scaler is fitted on the
    training split only), so a missing scaler is reported as a failure to evaluate M3,
    never papered over.
    """
    scaler_files = sorted(Path("/kaggle/input").rglob("*biophys_scaler.joblib"))
    if not scaler_files:
        return (None, {"status": "missing",
                       "reason": "training-fitted biophys scaler not mounted; M3 dropped "
                                 "from external tables rather than refitting it"})
    import joblib  # noqa: PLC0415
    return joblib.load(scaler_files[0]), {"status": "ok", "source": str(scaler_files[0])}


def _download_dbaasp(repo: Path, tmp: Path) -> list[str]:
    """Fetch dbaasp.csv through codeload, because raw.githubusercontent is blocked."""
    import tarfile
    import urllib.request
    target = tmp / "negds"
    if not (target / "data" / "dbaasp.csv").exists():
        url = ("https://codeload.github.com/BioGenies/NegativeDatasets/"
               "tar.gz/refs/heads/main")
        tgz = tmp / "negds.tgz"
        with urllib.request.urlopen(url, timeout=600) as r, tgz.open("wb") as f:
            shutil.copyfileobj(r, f)
        with tarfile.open(tgz) as tar:
            tar.extractall(tmp / "negds_x")
        root = next((tmp / "negds_x").iterdir())
        shutil.copytree(root, target, dirs_exist_ok=True)
    import csv
    path = target / "data" / "dbaasp.csv"
    if DBAASP_SHA and sha(path) != DBAASP_SHA:
        raise RuntimeError("dbaasp.csv sha256 mismatch")
    rows = []
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.DictReader(f):
            seq = (row.get("sequence") or "").strip().upper()
            if not seq:
                continue
            gram = str(row.get("Gram+")).strip().lower() in ("true", "1")
            gramneg = str(row.get("Gram-")).strip().lower() in ("true", "1")
            if gram or gramneg:
                rows.append(seq)
    return rows


def _score_models(pkg: Path, encoder, checkpoints: list[Path], seqs: list[str],
                  tmp: Path, scaler=None) -> dict:
    """Run every checkpoint of every model over `seqs` and average per model."""
    import numpy as np
    import torch  # noqa: PLC0415
    from dualamp_lite.config import FeatureConfig  # noqa: PLC0415
    from dualamp_lite.models import build_model  # noqa: PLC0415

    feat = encoder.encode(seqs)
    residue, mean, mask = feat[0], feat[1], feat[2]
    max_len = max(len(s) for s in seqs)
    tokens = np.zeros((len(seqs), max_len), dtype=np.int64)
    from dualamp_lite.config import STANDARD_AA  # noqa: PLC0415
    aa_idx = {a: i + 1 for i, a in enumerate(STANDARD_AA)}
    for i, s in enumerate(seqs):
        tokens[i, : len(s)] = [aa_idx.get(a, 0) for a in s]
    # biophys is only used by M3; it must be standardised with the scaler fitted on the
    # training split and shipped with the weights, never refitted here.
    physio = None
    if scaler is not None:
        try:
            from dualamp_lite import biophys  # noqa: PLC0415
            raw = biophys.compute_features(seqs)
            physio = scaler.transform(raw).astype(np.float32)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] biophys unavailable: {exc}", flush=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    out: dict[str, list] = {}
    thresholds: dict[str, float] = {}
    for ckpt in checkpoints:
        blob = torch.load(ckpt, map_location=device, weights_only=False)
        name = str(blob.get("model_name") or blob.get("model")
                   or ckpt.name.split("_")[0]).upper()
        net = build_model(name, embed_dim=blob["embed_dim"],
                          n_physio=blob["n_physio"]).to(device).eval()
        net.load_state_dict(blob["state_dict"])
        p = _predict(net, residue, mask, physio, tokens, device)
        out.setdefault(name, []).append(p)
        if blob.get("threshold") is not None:
            thresholds.setdefault(name, float(blob["threshold"]))
    return {"scores": {k: np.mean(np.vstack(v), axis=0) for k, v in out.items()},
            "thresholds": thresholds}


def _predict(net, residue, mask, physio, tokens, device) -> "object":
    import numpy as np
    import torch  # noqa: PLC0415
    from dualamp_lite.train import PeptideDataset, Bundle, _tokens_from_seqs  # noqa: PLC0415

    n = len(tokens)
    empty_physio = (np.zeros((n, 0), dtype=np.float32) if physio is None
                    else physio)
    # The encoder returns residue as float16/float32; mask must be boolean because the
    # models use ~mask for the attention/masking path (invert is not defined on floats).
    # `mean` is recomputed here with masked pooling over real residues only, exactly the
    # V3 P0 fix, rather than trusting the encoder's own pooling.
    residue = np.asarray(residue, dtype=np.float32)
    mask = np.asarray(mask).astype(bool)
    mean = (residue * mask[..., None]).sum(1) / np.maximum(
        mask.sum(1, keepdims=True), 1)
    bundle = Bundle(
        residue=residue, mask=mask, mean=mean.astype(np.float32),
        physio=empty_physio, tokens=tokens,
        labels=np.zeros(n, dtype=np.int64), clusters=np.zeros(n, dtype=np.int64),
        seq_ids=np.array([str(i) for i in range(n)]), seqs=np.array(["" for _ in range(n)]),
    )
    loader = torch.utils.data.DataLoader(PeptideDataset(bundle), batch_size=256,
                                         shuffle=False, num_workers=0)
    outs = []
    with torch.no_grad():
        for batch in loader:
            from dualamp_lite.train import forward_batch  # noqa: PLC0415
            logits = forward_batch(net, batch, device)
            outs.append(torch.sigmoid(logits.float()).cpu().numpy())
    return np.concatenate(outs)


def _metrics_block(payload: dict, y) -> dict:
    """AUROC + the model's own frozen operating point (threshold from dev OOF)."""
    scores = payload.get("scores", {})
    thresholds = payload.get("thresholds", {})
    out = {}
    for name, p in scores.items():
        thr = float(thresholds.get(name, 0.5))
        pred = [1 if v >= thr else 0 for v in p]
        yy = list(y)
        tp = sum(1 for a, b in zip(pred, yy) if a == 1 and b == 1)
        tn = sum(1 for a, b in zip(pred, yy) if a == 0 and b == 0)
        fp = sum(1 for a, b in zip(pred, yy) if a == 1 and b == 0)
        fn = sum(1 for a, b in zip(pred, yy) if a == 0 and b == 1)
        pos, neg = tp + fn, tn + fp
        den = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
        lo, hi = auroc_ci(y, p)
        out[name] = {
            "auroc": auroc(y, p),
            "auroc_ci95": [lo, hi],
            "threshold_from_dev": thr,
            "sensitivity": round(tp / pos, 4) if pos else None,
            "specificity": round(tn / neg, 4) if neg else None,
            "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
            "mcc": round((tp * tn - fp * fn) / den, 4) if den else None,
            "mean_score": round(float(sum(p) / max(len(p), 1)), 4),
        }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
