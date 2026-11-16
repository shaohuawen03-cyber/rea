#!/usr/bin/env python3
"""DualAMP-Lite pilot — candidate_v1 positives + UniProt A fragment negatives.

跑在 Kaggle 2xT4（经 GitHub Actions gpu-relay 派发）。本地可加 --negatives-only 预演阴性构建。

模式：
  real   — 阳性来自一次性预授权 Drive 链接（FASTA_URL env，统计/载荷由一次性加密信封注入），
           下载后核对 sha256（FASTA_SHA256 env，默认 candidate_v1 锁定值）；不过则中止。
  dry    — MOCK 阳性（随机肽，可复现），仅验证 build→features→train→report 链路，
           指标不可引用、不得用于任何结论。

约束（来自 DualAMP_Lite 项目文档，不可越界）：
  * hard_negative / scrambled 一律不进核心、不参与训练；
  * UniProt B 快照 / 348k parquet 不得替代 A（本载荷只用 A）；
  * Ma2022 仅引用；CAMP/LAMP/ADAM 不下载；
  * candidate_v1 ≠ v2 定稿：本载荷产出全部标注 PILOT，未过 230 条人工抽核。
"""
from __future__ import annotations

import argparse, gzip, hashlib, json, os, random, re, subprocess, sys, time
from pathlib import Path

REPO = "https://github.com/shaohuawen03-cyber/rea"
LEANREA = "https://github.com/hukeying85-ux/leanrea"
LEANREA_BRANCH = "arena/4ecb70f1-leanrea"
EXPECTED_POS_SHA256 = "7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d"
AA = "ACDEFGHIKLMNPQRSTVWY"
EXCLUDE_RE = re.compile(r"antimicrobial|bacteriocin|defensin|antibiotic|microcin|lantibio|nisin|pediocin|thuricin|cytolysin|subtilin|enterocin|leucocin", re.I)

def log(msg): print(f"[dualamp_pilot {time.strftime('%H:%M:%S')}] {msg}", flush=True)

def parse_fasta_gz(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        name, chunks = None, []
        for line in f:
            if line.startswith(">"):
                if name: yield name, "".join(chunks)
                name, chunks = line[1:].strip(), []
            else:
                chunks.append(line.strip())
        if name: yield name, "".join(chunks)

def load_tsv_meta(path):
    meta = {}
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split("\t")
        idx = {h.strip(): i for i, h in enumerate(header)}
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4: continue
            acc = cols[idx.get("Entry", 0)].strip()
            meta[acc] = (
                cols[idx.get("Protein names", 2)] if "Protein names" in idx else "",
                cols[idx.get("Organism", 3)] if "Organism" in idx else "",
            )
    return meta

def build_negatives(fasta_gz, tsv_gz, out_fasta, positive_seqs, cap=10000, seed=11):
    t0 = time.time()
    meta = load_tsv_meta(tsv_gz)
    pos_kmers = set()
    for s in positive_seqs:
        for i in range(len(s) - 3):
            pos_kmers.add(s[i:i+4])
    stats = {"proteins_total": 0, "keyword_excluded": 0, "fragments_raw": 0,
             "fragments_standard_aa": 0, "fragments_dedup": 0,
             "fragments_hit_positive_4mer_gt08": 0, "negatives_final": 0,
             "seed": seed, "cap": cap}
    rng = random.Random(seed)
    seen = set()
    kept = []
    for header, seq in parse_fasta_gz(fasta_gz):
        stats["proteins_total"] += 1
        acc = header.split("|")[1] if "|" in header else header.split()[0]
        name, org = meta.get(acc, ("", ""))
        if EXCLUDE_RE.search(name or "") or EXCLUDE_RE.search(org or "") or EXCLUDE_RE.search(header):
            stats["keyword_excluded"] += 1
            continue
        L = len(seq)
        if L < 100: continue
        for _ in range(3):
            flen = rng.randint(8, 50)
            if flen > L: flen = L
            start = rng.randint(0, L - flen)
            frag = seq[start:start+flen]
            stats["fragments_raw"] += 1
            if any(c not in AA for c in frag): continue
            stats["fragments_standard_aa"] += 1
            if frag in seen: continue
            seen.add(frag)
            stats["fragments_dedup"] += 1
            if positive_seqs:
                kmers = [frag[i:i+4] for i in range(len(frag) - 3)]
                if kmers:
                    hits = sum(1 for k in kmers if k in pos_kmers)
                    if hits / len(kmers) > 0.8:
                        stats["fragments_hit_positive_4mer_gt08"] += 1
                        continue
            kept.append((acc, frag))
    rng.shuffle(kept)
    kept = kept[:cap]
    stats["negatives_final"] = len(kept)
    with open(out_fasta, "w", encoding="utf-8") as f:
        for i, (acc, frag) in enumerate(kept):
            f.write(f">NEG_{i:06d}_{acc}\n{frag}\n")
    stats["seconds"] = round(time.time() - t0, 1)
    return stats

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

def shadow_positives(out_path, n=150, seed=20261116):
    rng = random.Random(seed)
    with open(out_path, "w", encoding="utf-8") as f:
        for i in range(n):
            L = rng.randint(12, 40)
            f.write(f">MOCK_P{i:04d}\n{''.join(rng.choice(AA) for _ in range(L))}\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["real", "dry", "negatives-only"], default="dry")
    ap.add_argument("--workdir", default="/kaggle/working/dualamp-pilot")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--seeds", default="1,2,3,4,5")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--models", default="M0_RF,M1,M2,M3")
    ap.add_argument("--neg-cap", type=int, default=10000)
    args = ap.parse_args()
    wd = Path(args.workdir); wd.mkdir(parents=True, exist_ok=True)
    data = wd / "data"; data.mkdir(exist_ok=True)

    repo_root = Path(os.environ.get("REA_REPO_DIR", str(wd / "rea")))
    if not (repo_root / "uniprot_bacteria_cytoplasm_reviewed.fasta.gz").exists():
        branch = os.environ.get("REA_BRANCH", "arena/cac2205a-rea")
        log(f"cloning repo branch {branch}")
        subprocess.check_call(["git", "clone", "--depth", "1", "-b", branch, REPO, str(repo_root)])
    fa = repo_root / "uniprot_bacteria_cytoplasm_reviewed.fasta.gz"
    tsv = repo_root / "uniprot_bacteria_cytoplasm_reviewed.tsv.gz"
    assert fa.exists() and tsv.exists(), "A snapshot not found"

    pos_fasta = data / "positives.fasta"
    mock = args.mode != "real"
    if args.mode == "real":
        url = os.environ.get("FASTA_URL", "").strip()
        want_sha = os.environ.get("FASTA_SHA256", EXPECTED_POS_SHA256).strip()
        assert url, "real mode requires FASTA_URL (one-time preauthorized Drive link)"
        log("downloading positives via one-time preauthorized URL")
        import urllib.request
        raw = urllib.request.urlopen(url, timeout=600).read()
        got = hashlib.sha256(raw).hexdigest()
        assert got == want_sha, f"sha256 mismatch: {got} != {want_sha}"
        pos_fasta.write_bytes(raw)
        log(f"positives sha256 OK ({len(raw)} bytes)")
    else:
        shadow_positives(pos_fasta)
        log("MOCK positives written (not citable)")

    pos_seqs = read_fasta(pos_fasta)
    log(f"positives: {len(pos_seqs)}")
    neg_fasta = data / "negatives.fasta"
    neg_stats = build_negatives(fa, tsv, neg_fasta, set(pos_seqs) if not mock else set(), cap=args.neg_cap, seed=11)
    log(f"negatives stats: {json.dumps(neg_stats, ensure_ascii=False)}")
    if args.mode == "negatives-only":
        (wd / "negatives_stats.json").write_text(json.dumps(neg_stats, indent=2, ensure_ascii=False))
        return

    dualamp = repo_root / "lee"
    if not dualamp.exists():
        log(f"cloning leanrea @ {LEANREA_BRANCH}")
        subprocess.check_call(["git", "clone", "--depth", "1", "-b", LEANREA_BRANCH, LEANREA, str(dualamp)])
    pkg = dualamp / "dualamp-lite"

    manifest = data / "sources.yaml"
    manifest.write_text(f"""sources:
  - id: {'mock_positives' if mock else 'candidate_v1'}
    path: {pos_fasta}
    format: fasta
    layer: general_amp
    label: 1
    min_len: 8
    max_len: 50
  - id: uniprot_a_fragments
    path: {neg_fasta}
    format: fasta
    layer: negative_background
    label: 0
    min_len: 8
    max_len: 50
""", encoding="utf-8")

    def run(cmd, **kw):
        log("$ " + " ".join(cmd))
        subprocess.check_call(cmd, cwd=str(pkg), **kw)

    run([sys.executable, "-m", "dualamp_lite", "build", "--manifest", str(manifest),
         "--workdir", str(wd / "run"), "--cluster-method", "auto"],
        env={**os.environ, "PYTHONPATH": str(pkg)})
    run([sys.executable, "-m", "dualamp_lite", "features", "--workdir", str(wd / "run"),
         "--backend", "fair-esm", "--batch-size", "64"],
        env={**os.environ, "PYTHONPATH": str(pkg)})
    run([sys.executable, "-m", "dualamp_lite", "train", "--workdir", str(wd / "run"),
         "--models", args.models, "--seeds", args.seeds, "--folds", str(args.folds),
         "--epochs", str(args.epochs), "--loss", "bce"],
        env={**os.environ, "PYTHONPATH": str(pkg)})
    run([sys.executable, "-m", "dualamp_lite", "report", "--workdir", str(wd / "run")],
        env={**os.environ, "PYTHONPATH": str(pkg)})

    outdir = wd / "artifacts"; outdir.mkdir(exist_ok=True)
    keep = ["dataset/dataset_summary.json", "dataset/label_audit.csv",
            "dataset/D1_sample_metadata.csv",
            "manuscript/D10_test_metrics.csv", "manuscript/D10_evaluation_report.md",
            "manuscript/D11_external_validation.csv", "manuscript/D15_input_sha256.tsv",
            "ablation/D9_ablation.csv", "model/train_summary.json",
            "features/feature_meta.json"]
    copied, missing = [], []
    for rel in keep:
        src = wd / "run" / rel
        if src.exists():
            dst = outdir / rel.replace("/", "_")
            dst.write_bytes(src.read_bytes())
            copied.append(dst.name)
        else:
            missing.append(rel)
    for p in (wd / "run" / "splits").glob("*.csv") if (wd / "run" / "splits").exists() else []:
        if p.stat().st_size <= 2 * 1024 * 1024:
            (outdir / f"splits_{p.name}").write_bytes(p.read_bytes()); copied.append(f"splits_{p.name}")
    (outdir / "_pilot_summary.json").write_text(json.dumps({
        "mode": args.mode, "mock_positives": mock,
        "positives": len(pos_seqs), "negatives_stats": neg_stats,
        "models": args.models, "seeds": args.seeds, "folds": args.folds, "epochs": args.epochs,
        "note": "PILOT on candidate_v1 (unreviewed, NOT v2 定稿). 不可入论文。" if not mock else "DRY RUN with MOCK positives; numbers are meaningless.",
        "artifacts_copied": copied, "artifacts_missing": missing,
    }, indent=2, ensure_ascii=False))
    log("artifacts: " + ", ".join(copied))

if __name__ == "__main__":
    main()
