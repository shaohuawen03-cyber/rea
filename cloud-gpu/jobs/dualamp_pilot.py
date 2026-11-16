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

def _ensure_deps():
    import importlib
    need = {"Bio": "biopython", "yaml": "pyyaml"}
    missing = [pkg for mod, pkg in need.items() if importlib.util.find_spec(mod) is None]
    if missing:
        log("installing missing deps: " + ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])

_CD_HIT_SHIM = r'''#!/usr/bin/env python3
"""Minimal cd-hit shim backed by the mmseqs2 engine.
Accepts -i/-o/-c/-aL (+ ignored extras); writes a CD-HIT .clstr file."""
import argparse, subprocess

def main():
    p = argparse.ArgumentParser()
    p.add_argument("-i", dest="inp", required=True)
    p.add_argument("-o", dest="out", required=True)
    p.add_argument("-c", type=float, required=True)
    p.add_argument("-aL", type=float, default=0.8)
    a, _ = p.parse_known_args()
    lengths, name, buf = {}, None, []
    with open(a.inp) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    lengths[name] = len("".join(buf))
                name = line[1:].split()[0]; buf = []
            else:
                buf.append(line.strip())
        if name is not None:
            lengths[name] = len("".join(buf))
    tmpdir = a.out + "_tmp"; prefix = a.out + "_mm"
    subprocess.check_call(["mmseqs", "easy-cluster", a.inp, prefix, tmpdir,
                           "--min-seq-id", f"{a.c:.2f}", "-c", f"{a.aL:.2f}",
                           "--cov-mode", "0", "-v", "0"])
    clusters, order = {}, []
    with open(prefix + "_cluster.tsv") as fh:
        for line in fh:
            rep, mem = line.rstrip("\n").split("\t")
            if rep not in clusters:
                clusters[rep] = len(order); order.append(rep)
    members = {}
    with open(prefix + "_cluster.tsv") as fh:
        for line in fh:
            rep, mem = line.rstrip("\n").split("\t")
            members.setdefault(clusters[rep], []).append((mem, mem == rep))
    with open(a.out + ".clstr", "w") as out:
        for idx in range(len(order)):
            out.write(f">Cluster {idx}\n")
            for rank, (mem, is_rep) in enumerate(members[idx]):
                ln = lengths.get(mem, 0)
                if is_rep:
                    out.write(f"{rank}\t{ln}aa, >{mem}... *\n")
                else:
                    out.write(f"{rank}\t{ln}aa, >{mem}... at +/99.00%\n")

main()
'''


def _selftest_cdhit():
    import os, tempfile
    d = tempfile.mkdtemp()
    fa = os.path.join(d, "t.fasta")
    with open(fa, "w") as fh:
        fh.write(">id0\nACDEFGHIKLMNPQRSTVWY\n>id1\nACDEFGHIKLMNPQRSTVWF\n>id2\nWWWWWWWWWWWWWWWWWWWW\n")
    try:
        subprocess.check_call(["cd-hit", "-i", fa, "-o", os.path.join(d, "o"),
                               "-c", "0.4", "-aL", "0.8", "-n", "2", "-d", "0",
                               "-T", "2", "-M", "0"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cl = os.path.join(d, "o.clstr")
        return os.path.exists(cl) and os.path.getsize(cl) > 0
    except Exception:
        return False


def _ensure_clustering():
    """Guarantee dualamp_lite's first clustering tier (cd-hit) actually works:
    apt mmseqs2 engine (+ cd-hit if the distro ships it), plus a source self-test
    of whatever `cd-hit` is on PATH; if the self-test fails and an mmseqs engine
    exists, install a /usr/local/bin/cd-hit shim (shadows /usr/bin via PATH)."""
    import shutil
    if shutil.which("mmseqs") is None:
        try:
            subprocess.check_call(["apt-get", "update", "-qq"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["apt-get", "install", "-y", "-qq", "mmseqs2"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            log(f"warning: mmseqs2 apt install failed: {exc}")
    if shutil.which("cd-hit") is None:
        try:
            subprocess.check_call(["apt-get", "install", "-y", "-qq", "cd-hit"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log("cd-hit installed via apt: " + str(shutil.which("cd-hit")))
        except Exception as exc:
            log(f"warning: cd-hit apt install failed: {exc}; will rely on shim if an mmseqs engine exists")
    if shutil.which("cd-hit") is not None and _selftest_cdhit():
        log("cd-hit self-test OK: " + str(shutil.which("cd-hit")))
        return
    if shutil.which("mmseqs") is not None:
        try:
            Path("/usr/local/bin/cd-hit").write_text(_CD_HIT_SHIM)
            os.chmod("/usr/local/bin/cd-hit", 0o755)
            if _selftest_cdhit():
                log("native cd-hit missing/broken; installed cd-hit->mmseqs shim at /usr/local/bin/cd-hit (self-test OK)")
                return
            log("ERROR: cd-hit shim installed but self-test FAILED; k-mer fallback will be used")
        except Exception as exc:
            log(f"ERROR: failed to install cd-hit shim: {exc}; k-mer fallback will be used")
    else:
        log("WARNING: neither cd-hit nor mmseqs available; k-mer fallback will be used")

def main():
    os.environ.setdefault("TORCH_HOME", "/tmp/torch_home")
    _ensure_deps()
    _ensure_clustering()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["real", "dry", "negatives-only"], default="dry")
    ap.add_argument("--workdir", default="/tmp/dualamp-pilot")
    ap.add_argument("--artifact-dir", default=os.getcwd())
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
        want_sha = os.environ.get("FASTA_SHA256", EXPECTED_POS_SHA256).strip()
        raw = None
        url = os.environ.get("FASTA_URL", "").strip()
        fid = os.environ.get("FASTA_ID", "11vvQSlOJDjt8v6qHCB4LWg6mRK3DwNis").strip()  # gdown 需要 anyone-with-link 只读（派发窗内开放，跑完回收）
        if fid:
            log(f"downloading positives via gdown (Drive file id {fid[:6]}…)")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "gdown"])
            import gdown
            raw_path = str(data / "positive_core_candidate_v1.fasta")
            got_path = gdown.download(id=fid, output=raw_path, quiet=False)
            assert got_path, "gdown failed (file must be 'anyone with link can view')"
            raw = Path(got_path).read_bytes()
        elif url:
            log("downloading positives via preauthorized URL")
            import urllib.request
            raw = urllib.request.urlopen(url, timeout=600).read()
        else:
            sa = os.environ.get("DRIVE_SA_JSON", "").strip()   # not recommended; prefer private dataset
            if sa:
                log("downloading positives via service account (Drive API)")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-q",
                                       "google-api-python-client", "google-auth"])
                import json as _json, tempfile
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
                    tf.write(sa); sa_path = tf.name
                creds = service_account.Credentials.from_service_account_file(
                    sa_path, scopes=["https://www.googleapis.com/auth/drive.readonly"])
                svc = build("drive", "v3", credentials=creds)
                sa_fid = os.environ.get("FASTA_ID", "11vvQSlOJDjt8v6qHCB4LWg6mRK3DwNis")
                req = svc.files().get_media(fileId=sa_fid)
                with tempfile.NamedTemporaryFile(delete=False) as tf2:
                    from googleapiclient.http import MediaIoBaseDownload
                    import io
                    buf = io.BytesIO()
                    dl = MediaIoBaseDownload(buf, req)
                    done = False
                    while not done:
                        _, done = dl.next_chunk()
                    raw = buf.getvalue()
            else:
                sa_ds = os.environ.get("SA_DATASET", "").strip()  # e.g. shaohuawen/dualamp-drive-sa
                sa_path = "/kaggle/input/dualamp-drive-sa/service_account.json"
                alt_dir = "/kaggle/input/" + sa_ds.split("/", 1)[-1] if sa_ds else None
                for cand in [sa_path, (alt_dir + "/service_account.json") if alt_dir else None]:
                    if cand and os.path.exists(cand):
                        sa_path = cand; break
                if os.path.exists(sa_path):
                    log("downloading positives via private Kaggle dataset service account")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q",
                                           "google-api-python-client", "google-auth"])
                    from google.oauth2 import service_account
                    from googleapiclient.discovery import build
                    from googleapiclient.http import MediaIoBaseDownload
                    import io
                    creds = service_account.Credentials.from_service_account_file(
                        sa_path, scopes=["https://www.googleapis.com/auth/drive.readonly"])
                    svc = build("drive", "v3", credentials=creds)
                    sa_fid = os.environ.get("FASTA_ID", "11vvQSlOJDjt8v6qHCB4LWg6mRK3DwNis")
                    req = svc.files().get_media(fileId=sa_fid)
                    buf = io.BytesIO()
                    dl = MediaIoBaseDownload(buf, req)
                    done = False
                    while not done:
                        _, done = dl.next_chunk()
                    raw = buf.getvalue()
                else:
                    raise RuntimeError("real mode needs FASTA_ID (public-link) / FASTA_URL (preauthorized) / private SA dataset or DRIVE_SA_JSON")
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

    outdir = Path(args.artifact_dir); outdir.mkdir(parents=True, exist_ok=True)
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
