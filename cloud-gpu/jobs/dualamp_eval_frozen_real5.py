#!/usr/bin/env python3
"""Frozen-weight cross-distribution evaluation of the real5 candidate_v1 pilot weights.

目的：P1/P2（组成偏好）的直接检验——不重训，用 real5 的冻结 .pt（GitHub prerelease
`dualamp-real5-candidate_v1-pilot-weights-20261116`）分别打分：

  * standard negatives          (real5 训练口径；期望 FP 率 ≈ 0.007，sanity)
  * composition-matched negatives (real7 口径；P1/P2 的跨分布探测点)
  * scrambled probes            (评估期生成的 shuffle，seed 20261118；非 D-split challenge 集)
  * candidate_v1 positives      (in-distribution sanity + AUC 配对)

同时作为发布渠道外的独立复核：下载 prerelease 资产并对 SHA256SUMS.txt 逐文件校验，
并对 export_manifest.json 的 file_count/total_bytes 自洽性打印结论。

约束：PILOT 口径；real5 权重 ≠ positive_core_v2；产物不可入论文正文结论（附录可引）。
环境：Kaggle T4（经 gpu-relay 派发）；Positives FASTA 经一次性 anyone-with-link 窗口下载，
窗口由派发方开启/关闭并负责复核仅 owner。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
import traceback
from pathlib import Path

REPO = "https://github.com/shaohuawen03-cyber/rea"
LEANREA = "https://github.com/hukeying85-ux/leanrea"
LEANREA_BRANCH = "arena/4ecb70f1-leanrea"
LEANREA_REV = os.environ.get("LEANREA_REV", "f610dc00b1aec3ab948a5b77574af0e644259a13")
RELEASE_TAG = "dualamp-real5-candidate_v1-pilot-weights-20261116"
RELEASE_BASE = f"https://github.com/shaohuawen03-cyber/rea/releases/download/{RELEASE_TAG}"
TARBALL = f"{RELEASE_TAG}.tar.gz"
EXPECTED_POS_SHA256 = "7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d"
AA = "ACDEFGHIKLMNPQRSTVWY"
# real7 构建日志中的对照值（/验证 reconstruct）
RECON_EXPECT = {
    "standard": {"proteins_total": 117172, "keyword_excluded": 18,
                 "fragments_raw": 351462, "fragments_dedup": 351119,
                 "fragments_hit_positive_4mer_gt08": 1096, "negatives_final": 10000},
    "composition-matched": {"proteins_total": 117172, "keyword_excluded": 18,
                            "fragments_raw": 351462, "fragments_dedup": 351119,
                            "fragments_hit_positive_4mer_gt08": 1096, "negatives_final": 10000,
                            "compmatch_eligible": 350023},
}
CUTOFF_WANT = 0.04177

def log(msg): print(f"[eval_frozen {time.strftime('%H:%M:%S')}] {msg}", flush=True)

def sha256_file(p, buf=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()

def read_fasta(path):
    seqs, name, chunks = [], None, []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith(">"):
                if name: seqs.append("".join(chunks))
                name, chunks = line[1:].strip(), []
            else:
                chunks.append(line.strip())
        if name: seqs.append("".join(chunks))
    return seqs

def dl(url, dst, retries=3, chunk=1 << 20):
    dst = Path(dst)
    ua = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    for attempt in range(1, retries + 1):
        # try curl first (Kaggle images ship it)
        if subprocess.run(["bash", "-lc", "command -v curl >/dev/null"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            rc = subprocess.run(["bash", "-lc",
                                 f"curl -fL --retry 2 --retry-all-errors --max-time 240 "
                                 f"-A '{ua}' -o '{dst}' '{url}'"]).returncode
            if rc == 0 and dst.exists() and dst.stat().st_size > 0:
                log(f"curl ok: {url.rsplit('/',1)[-1]} -> {dst} ({dst.stat().st_size} B)")
                return dst
            log(f"curl attempt {attempt} failed (rc={rc})")
        # fallback: urllib with browser UA
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=180) as r, open(dst, "wb") as f:
                while True:
                    b = r.read(chunk)
                    if not b: break
                    f.write(b)
            log(f"urllib ok: {url.rsplit('/',1)[-1]} -> {dst} ({dst.stat().st_size} B)")
            return dst
        except Exception as exc:  # noqa: BLE001
            log(f"urllib attempt {attempt} failed: {exc}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"download failed after {retries} rounds: {url}")

def verify_release_assets(workdir):
    """独立于发布 runner 的资产复核（本 kernel 通过 GitHub 直链下载）。"""
    lines = []
    wd = Path(workdir); wd.mkdir(parents=True, exist_ok=True)
    sums_p = dl(f"{RELEASE_BASE}/SHA256SUMS.txt", wd / "SHA256SUMS.txt")
    man_p = dl(f"{RELEASE_BASE}/export_manifest.json", wd / "export_manifest.json")
    tar_p = dl(f"{RELEASE_BASE}/{TARBALL}", wd / TARBALL)
    import tarfile
    ext = wd / "extracted"; ext.mkdir(exist_ok=True)
    with tarfile.open(tar_p) as tf:
        tf.extractall(ext, filter="data")
    ok_all = True
    total = 0
    n = 0
    for ln in sums_p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln: continue
        want, rel = ln.split(None, 1)
        rel = rel.strip()
        p = ext / rel
        if not p.exists():
            lines.append(f"MISSING  {rel}"); ok_all = False; continue
        got = sha256_file(p)
        good = got == want.lower()
        ok_all = ok_all and good
        total += p.stat().st_size; n += 1
        lines.append(f"{'OK' if good else 'MISMATCH'}  {rel}  bytes={p.stat().st_size}")
    man = json.loads(man_p.read_text(encoding="utf-8"))
    lines.append("")
    lines.append(f"files in SHA256SUMS: {n}; bytes hashed: {total}")
    lines.append(f"manifest: file_count={man.get('file_count')} total_bytes={man.get('total_bytes')} "
                 f"release_tag={man.get('release_tag')}")
    lines.append(f"manifest_provenance_run={man.get('provenance', {}).get('source_workflow_run')}")
    lines.append(f"VERDICT: {'PASS' if ok_all else 'FAIL'} (独立复核于 kernel 内完成；资产经 GitHub release 直链)")
    (wd / "weights_verify.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ext, ok_all

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["real", "negatives-only"], default="real")
    ap.add_argument("--workdir", default="/tmp/frozen-eval")
    ap.add_argument("--artifact-dir", default=os.getcwd())
    ap.add_argument("--fasta-id", default="11vvQSlOJDjt8v6qHCB4LWg6mRK3DwNis")
    ap.add_argument("--fasta-sha", default=EXPECTED_POS_SHA256)
    ap.add_argument("--neg-cap", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    os.environ.setdefault("TORCH_HOME", "/tmp/torch_home")
    os.environ.setdefault("PYTHONHASHSEED", "0")

    wd = Path(args.workdir); wd.mkdir(parents=True, exist_ok=True)
    data = wd / "data"; data.mkdir(exist_ok=True)
    out = Path(args.artifact_dir); out.mkdir(parents=True, exist_ok=True)

    # ---- 1) repo snapshot -------------------------------------------------
    repo_root = Path(os.environ.get("REA_REPO_DIR", str(wd / "rea")))
    if not (repo_root / "uniprot_bacteria_cytoplasm_reviewed.fasta.gz").exists():
        branch = os.environ.get("REA_BRANCH", "arena/cac2205a-rea")
        log(f"cloning repo branch {branch}")
        subprocess.check_call(["git", "clone", "--depth", "1", "-b", branch, REPO, str(repo_root)])
    fa = repo_root / "uniprot_bacteria_cytoplasm_reviewed.fasta.gz"
    tsv = repo_root / "uniprot_bacteria_cytoplasm_reviewed.tsv.gz"
    sys.path.insert(0, str(repo_root / "cloud-gpu" / "jobs"))
    import dualamp_pilot as dp

    # ---- 2) positives via one-time Drive window ---------------------------
    pos_fasta = data / "positives.fasta"
    fid = (os.environ.get("FASTA_ID", "").strip() or args.fasta_id).strip()
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "gdown", "biopython"])
    import gdown
    log(f"downloading positives via gdown (Drive id {fid[:6]}…)")
    got = gdown.download(id=fid, output=str(pos_fasta), quiet=False)
    assert got, "gdown failed (positives file must be anyone-with-link during the burn window)"
    want = (os.environ.get("FASTA_SHA256", "").strip() or args.fasta_sha).strip()
    got_sha = sha256_file(pos_fasta)
    log(f"positives sha256: {got_sha} (want {want})")
    assert got_sha.lower() == want.lower(), "positives sha256 mismatch — abort"
    pos_seqs = read_fasta(pos_fasta)
    log(f"positives: {len(pos_seqs)}")
    pos_set = set(pos_seqs)

    # ---- 4) rebuild negatives (both modes, same recipe/seed) ---------------
    neg_std = data / "negatives_standard.fasta"
    neg_mat = data / "negatives_matched.fasta"
    std_stats = dp.build_negatives(fa, tsv, neg_std, pos_set, cap=args.neg_cap, seed=args.seed, neg_mode="standard")
    mat_stats = dp.build_negatives(fa, tsv, neg_mat, pos_set, cap=args.neg_cap, seed=args.seed, neg_mode="composition-matched")
    log(f"negatives standard: {json.dumps(std_stats, ensure_ascii=False)}")
    log(f"negatives matched : {json.dumps(mat_stats, ensure_ascii=False)}")

    recon_lines = []
    for mode, stats in (("standard", std_stats), ("composition-matched", mat_stats)):
        for k, v in RECON_EXPECT[mode].items():
            got_v = stats.get(k)
            ok = int(got_v) == int(v) if got_v is not None else False
            recon_lines.append(f"{mode:18s} {k:38s} want={v} got={got_v} {'PASS' if ok else 'DIFF'}")
    cut = mat_stats.get("compmatch_cutoff_sqdist")
    recon_lines.append(f"{'composition-matched':18s} {'compmatch_cutoff_sqdist':38s} want~={CUTOFF_WANT} got={cut} "
                       f"{'PASS' if cut is not None and abs(cut - CUTOFF_WANT) < 1e-4 else 'DIFF'}")
    recon_lines.append(f"{'composition-matched':18s} {'compmatch_kept':38s} want~=105007 got={mat_stats.get('compmatch_kept')}")
    (out / "recon_verification.txt").write_text("\n".join(recon_lines) + "\n", encoding="utf-8")
    recon_sha_lines = [f"sha256 standard  {sha256_file(neg_std)}", f"sha256 matched   {sha256_file(neg_mat)}"]
    with open(out / "recon_verification.txt", "a", encoding="utf-8") as fh:
        fh.write("\n".join(recon_sha_lines) + "\n")

    if args.mode == "negatives-only":
        (out / "negatives_stats.json").write_text(json.dumps({"standard": std_stats, "matched": mat_stats},
                                                             indent=2, ensure_ascii=False), encoding="utf-8")
        return

    # ---- 3b) prerelease download + independent sha256 verify (non-fatal) ------
    rel_dir = wd / "release"
    try:
        ext, verify_ok = verify_release_assets(rel_dir)
        log(f"weights verify: {'PASS' if verify_ok else 'FAIL'}")
    except Exception as exc:  # noqa: BLE001 - publish diagnostics instead of crashing silently
        traceback.print_exc()
        (out / "_eval_failed.json").write_text(json.dumps({
            "status": "failed", "stage": "release_assets", "error": repr(exc)}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        log("FATAL at release-assets stage; diagnostics written to _eval_failed.json; scoring skipped")
        return

    if not verify_ok:
        (out / "_eval_failed.json").write_text(json.dumps({
            "status": "failed", "stage": "weights_verify", "error": "SHA256SUMS mismatch"}, indent=2), encoding="utf-8")
        log("FATAL: prerelease sha256 verification FAILED; refusing to score mismatched weights")
        return

    # ---- 5) leanrea @ f610dc00 + fair-esm encode ---------------------------
    dualamp = repo_root / "lee"
    if not dualamp.exists():
        log(f"cloning leanrea @ {LEANREA_BRANCH} then pinning {LEANREA_REV[:12]}")
        subprocess.check_call(["git", "clone", "--depth", "1", "-b", LEANREA_BRANCH, LEANREA, str(dualamp)])
        subprocess.check_call(["git", "-C", str(dualamp), "fetch", "--depth", "1", "origin", LEANREA_REV])
        subprocess.check_call(["git", "-C", str(dualamp), "checkout", "--detach", LEANREA_REV])
    head = subprocess.check_output(["git", "-C", str(dualamp), "rev-parse", "HEAD"], text=True).strip()
    assert head == LEANREA_REV, f"leanrea HEAD mismatch: {head}"
    log(f"leanrea pinned at {head[:12]}")
    pkg = dualamp / "dualamp-lite"
    sys.path.insert(0, str(pkg))

    import numpy as np
    import torch
    import joblib
    from dualamp_lite import biophys, models, features, config as dlconfig

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    fcfg = dlconfig.FeatureConfig(backend="fair-esm", model_name="esm2_t12_35M_UR50D", repr_layer=12,
                                  max_residues=50, storage_dtype="float32", device="cuda")
    encoder = features.FairESMEncoder(fcfg, device)
    scaler_path = None
    for p in ext.rglob("biophys_scaler.joblib"):
        scaler_path = p; break
    assert scaler_path is not None, "biophys_scaler.joblib not found in prerelease package"
    scaler = joblib.load(scaler_path)

    def encode_set(seqs, tag):
        log(f"encoding {tag}: {len(seqs)} seqs")
        t0 = time.time()
        residue, mask, mean = encoder.encode(seqs, batch_size=64)
        raw = biophys.compute_features(seqs)
        physio = scaler.transform(raw).astype(np.float32)
        log(f"  {tag} encoded in {time.time()-t0:.1f}s; physio {physio.shape}")
        return np.asarray(residue, dtype=np.float32), np.asarray(mask, dtype=bool), physio

    rng = random.Random(20261118)
    scrambled = []
    for s in pos_seqs[:500]:
        t = list(s); rng.shuffle(t); scrambled.append("".join(t))

    sets = {
        "positives": pos_seqs,
        "standard": read_fasta(neg_std),
        "matched": read_fasta(neg_mat),
        "scrambled": scrambled,
    }
    feats = {tag: encode_set(seqs, tag) for tag, seqs in sets.items()}

    def predict_scores(model, residue, mask, physio, batch=512):
        model.eval()
        out_probs = []
        with torch.no_grad():
            for i in range(0, residue.shape[0], batch):
                r = torch.from_numpy(residue[i:i+batch]).to(device)
                m = torch.from_numpy(mask[i:i+batch]).to(device)
                p = torch.from_numpy(physio[i:i+batch]).to(device)
                logit = model(r, m, p)
                out_probs.append(torch.sigmoid(logit).float().cpu().numpy())
        return np.concatenate(out_probs)

    from sklearn.metrics import roc_auc_score

    model_dir = ext / "model"
    ckpts = sorted(model_dir.glob("*.pt"))
    log(f"checkpoints: {len(ckpts)} -> {[p.name for p in ckpts]}")
    assert len(ckpts) == 15, f"expected 15 checkpoints, found {len(ckpts)}"

    rows = []
    for ck in ckpts:
        blob = torch.load(ck, map_location=device, weights_only=False)
        name = blob.get("model_name") or ck.stem.split("_")[0]
        seed = int(blob.get("seed", 0))
        threshold = float(blob.get("threshold", 0.5))
        mdl = models.build_model(name, embed_dim=int(blob.get("embed_dim", 480)),
                                 n_physio=int(blob.get("n_physio", 35)),
                                 dropout=float(blob.get("config", {}).get("dropout", 0.3))).to(device)
        mdl.load_state_dict(blob["state_dict"])
        scores = {tag: predict_scores(mdl, *feats[tag]) for tag in ("positives", "standard", "matched", "scrambled")}
        pos_scores = scores["positives"]
        for neg_tag in ("standard", "matched", "scrambled"):
            ns = scores[neg_tag]
            y = np.concatenate([np.ones(len(pos_scores)), np.zeros(len(ns))])
            s = np.concatenate([pos_scores, ns])
            rows.append({
                "model": name, "seed": seed, "negative_set": neg_tag,
                "n_pos": len(pos_scores), "n_neg": len(ns), "threshold": threshold,
                "pos_mean_score": float(pos_scores.mean()), "neg_mean_score": float(ns.mean()),
                "pos_frac_pred_positive": float((pos_scores >= threshold).mean()),
                "neg_frac_pred_positive": float((ns >= threshold).mean()),
                "auroc": float(roc_auc_score(y, s)),
            })
        log(f"scored {ck.name} (threshold={threshold:.4f})")
    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv(out / "eval_frozen_real5_metrics.csv", index=False)

    agg = (df.groupby(["model", "negative_set"])
             .agg(neg_frac_pred_positive_mean=("neg_frac_pred_positive", "mean"),
                  neg_frac_pred_positive_sd=("neg_frac_pred_positive", "std"),
                  neg_mean_score=("neg_mean_score", "mean"),
                  pos_frac_pred_positive=("pos_frac_pred_positive", "mean"),
                  auroc_mean=("auroc", "mean"), auroc_sd=("auroc", "std"), n_seeds=("seed", "count"))
             .reset_index())
    summary = {
        "job": "dualamp-eval-frozen-real5-20261118",
        "positives_sha256": want,
        "weights_release": RELEASE_TAG,
        "weights_verify": "PASS" if verify_ok else "FAIL",
        "negatives": {"standard": std_stats, "matched": mat_stats},
        "aggregate": agg.to_dict(orient="records"),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    md = ["# Frozen real5 weights × cross-distribution negatives (dualamp-eval-frozen-real5-20261118)\n",
          f"- positives sha256: `{want}`; leaneRA pin `{LEANREA_REV[:12]}`; fair-esm esm2_t12_35M_UR50D layer 12\n",
          f"- weights release: {RELEASE_TAG}; independent sha256 verify: **{'PASS' if verify_ok else 'FAIL'}**\n",
          f"- standard negatives: n={std_stats['negatives_final']}; matched: n={mat_stats['negatives_final']} "
          f"(cutoff {mat_stats.get('compmatch_cutoff_sqdist'):.5f})\n\n",
          "| model | negative set | FP rate @ embedded threshold (mean±sd over 5 seeds) | mean score | AUC (pos vs set) | TPR (pos ≥ thr) |\n",
          "|---|---|---|---|---|---|\n"]
    for _, r in agg.iterrows():
        md.append(f"| {r.model} | {r.negative_set} | {r.neg_frac_pred_positive_mean:.4f} ± {r.neg_frac_pred_positive_sd:.4f} | "
                  f"{r.neg_mean_score:.4f} | {r.auroc_mean:.4f} ± {r.auroc_sd:.4f} | {r.pos_frac_pred_positive:.4f} |\n")
    md.append("\n*PILOT 口径；scrambled = 评估期 shuffle（seed 20261118），非 D-split challenge 集。\n")
    (out / "summary.md").write_text("".join(md), encoding="utf-8")
    (out / "_eval_frozen_real5_done.json").write_text(json.dumps({"status": "completed", "verify_ok": verify_ok}, indent=2), encoding="utf-8")
    log("artifacts written: eval_frozen_real5_metrics.csv, summary.json, summary.md, weights_verify.txt, recon_verification.txt")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        (Path(os.environ.get("EVAL_ARTIFACT_DIR", os.getcwd())) / "_eval_failed.json").write_text(
            json.dumps({"status": "failed", "stage": "unhandled", "error": "see traceback in kernel log"}, indent=2),
            encoding="utf-8")
        raise
