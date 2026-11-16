#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 4：DualAMP-M1/M2 批量打分（>1 亿条流式、可分片、可续跑）。
M1=ESM-2 全局 masked-mean + MLP；M2=残基级多尺度 CNN（与训练时结构一致，n_physio=0）。
每个模型=5 个 seed 的 sigmoid(logit) 均值；阈值取各 seed 阈值均值（写入 per-model _thr，默认用训练阈值）。
输出 TSV：seq<TAB>length<TAB>m1_prob<TAB>m2_prob<TAB>m1_pred<TAB>m2_pred
用法：
  04_batch_score.py --input reps.faa --export-dir model_export --leanrea-dir .external/leanrea \
      --out pred.tsv [--shard 0 --num-shards 10] [--batch-size 128] [--device cuda] [--resume]
多进程：按 --shard k --num-shards N 切分（seq 序号取模），N 个进程各自跑，最后 cat 合并。
续跑：--resume 跳过已输出行数（同一输入文件重跑时有效）。
"""
import argparse, json, os, sys, time

import numpy as np

SCRIPT_VERSION = "04_batch_score.py v2 (resume keyed on input+checkpoints sha256)"

def sha256_file(path):
    h = __import__("hashlib").sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()

def checkpoints_sha256(export_dir):
    import hashlib, glob, os
    lines = []
    for f in sorted(glob.glob(os.path.join(export_dir, "model", "*.pt"))):
        lines.append(f"{os.path.basename(f)}:{os.path.getsize(f)}:{sha256_file(f)}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()

def iter_fasta(path):
    name, buf = None, []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(buf)
                name = line[1:].split()[0]
                buf = []
            else:
                buf.append(line.strip().upper())
        if name is not None:
            yield name, "".join(buf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--export-dir", required=True, help="03_train_export.sh 的导出目录")
    ap.add_argument("--leanrea-dir", default=None, help="leanrea 克隆路径（缺省自动浅克隆到 <export-dir>/leanrea）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=128)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    assert 0 <= args.shard < args.num_shards

    # leanrea_dir = leanrea 仓库根目录（默认 .external/leanrea）；包路径 = <leanrea>/dualamp-lite/dualamp_lite
    leanrea = args.leanrea_dir or os.path.join(".external", "leanrea")
    pkg_parent = os.path.join(leanrea, "dualamp-lite")
    if not os.path.isdir(os.path.join(pkg_parent, "dualamp_lite")):
        import subprocess
        os.makedirs(os.path.dirname(leanrea) or ".", exist_ok=True)
        print(f"cloning leanrea into {leanrea} (repo root)")
        subprocess.check_call(["git", "clone", "--depth", "1", "-b", "arena/4ecb70f1-leanrea",
                               "https://github.com/hukeying85-ux/leanrea.git", leanrea])
    sys.path.insert(0, pkg_parent)

    import torch
    from dualamp_lite import features, models  # noqa: E402
    from dualamp_lite.config import FeatureConfig  # noqa: E402

    device = args.device if torch.cuda.is_available() or args.device == "cpu" else "cpu"
    cfg = FeatureConfig(backend="fair-esm", model_name="esm2_t12_35M_UR50D",
                        repr_layer=12, embed_dim=480, batch_size=args.batch_size, max_residues=512)
    enc = features.FairESMEncoder(cfg, device)

    # 载入双模型 × 5 seeds；M1/M2 不用 biophys（n_physio=0），无需 scaler
    bundle = []  # (name, model)
    model_threshold = {}
    for name in ("M1", "M2"):
        thresholds = []
        for seed in (1, 2, 3, 4, 5):
            ckpt_path = os.path.join(args.export_dir, "model", f"{name}_seed{seed}.pt")
            if not os.path.exists(ckpt_path):
                print(f"WARNING: missing {ckpt_path}, skipped")
                continue
            blob = torch.load(ckpt_path, map_location="cpu", weights_only=False)
            model = models.build_model(name, embed_dim=blob["embed_dim"], n_physio=blob["n_physio"])
            model.load_state_dict(blob["state_dict"])
            model.eval().to(device)
            bundle.append((name, model))
            thresholds.append(float(blob["threshold"]))
        if thresholds:
            model_threshold[name] = float(np.mean(thresholds))
            print(f"{name}: {len(thresholds)} seeds, mean threshold {model_threshold[name]:.3f}")
        else:
            sys.exit(f"ERROR: no {name} checkpoints found in {args.export_dir}/model")

    physio0 = torch.zeros((args.batch_size, 0), dtype=torch.float32, device=device)

    # resume 保护：必须匹配 输入 sha256 + 权重集 sha256 + shard 配置
    meta_path = args.out + ".meta.json"
    meta = {"version": SCRIPT_VERSION,
            "input_sha256": sha256_file(args.input),
            "checkpoints_sha256": checkpoints_sha256(args.export_dir),
            "shard": args.shard, "num_shards": args.num_shards,
            "leanrea_dir": os.path.abspath(leanrea)}
    if args.resume:
        if not os.path.exists(meta_path):
            sys.exit(f"ERROR: --resume 要求存在 {meta_path}；已有输出来源不明，拒绝续跑。")
        old_meta = json.load(open(meta_path))
        for k in ("input_sha256", "checkpoints_sha256", "shard", "num_shards"):
            if old_meta.get(k) != meta[k]:
                sys.exit(f"ERROR: {k} 与 {meta_path} 不一致（{old_meta.get(k)} != {meta[k]}）；拒绝错位续跑。")
    with open(meta_path, "w") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)

    done_lines = 0
    if args.resume and os.path.exists(args.out):
        with open(args.out) as fh:
            done_lines = sum(1 for _ in fh) - 1  # minus header
    write_header = not (args.resume and done_lines > 0)
    mode = "a" if (args.resume and done_lines > 0) else "w"

    t0 = time.time()
    n_written = 0
    with open(args.out, mode, buffering=1 << 20) as out:
        if write_header:
            out.write("seq\tlength\tm1_prob\tm2_prob\tm1_pred\tm2_pred\n")
        pending = []  # (index, seq)
        seen = 0

        def flush(batch):
            nonlocal n_written
            seqs = [s for _, s in batch]
            residue, mask, mean = enc.encode(seqs, batch_size=len(seqs))
            residue_t = torch.from_numpy(residue).to(device)
            mask_t = torch.from_numpy(mask).to(device)
            mean_t = torch.from_numpy(mean).to(device)
            phys = physio0[: len(seqs)]
            with torch.no_grad():
                probs = {}
                logits = {"M1": [], "M2": []}
                for name, model in bundle:
                    if name == "M1":
                        lg = model(residue_t, mask_t, phys, mean_t)
                    else:
                        lg = model(residue_t, mask_t, phys)
                    logits[name].append(torch.sigmoid(lg))
                for name in ("M1", "M2"):
                    probs[name] = torch.stack(logits[name]).mean(0).cpu().numpy()
            thr = model_threshold
            for i, (_, seq) in enumerate(batch):
                p1, p2 = float(probs["M1"][i]), float(probs["M2"][i])
                out.write(f"{seq}\t{len(seq)}\t{p1:.4f}\t{p2:.4f}\t{int(p1>=thr['M1'])}\t{int(p2>=thr['M2'])}\n")
            n_written += len(seqs)

        for idx, (_sid, seq) in enumerate(iter_fasta(args.input)):
            if args.limit and idx >= args.limit:
                break
            if idx % args.num_shards != args.shard:
                continue
            if args.resume and seen < done_lines:
                seen += 1
                continue
            pending.append((idx, seq))
            if len(pending) >= args.batch_size:
                flush(pending)
                pending = []
        if pending:
            flush(pending)
    dt = time.time() - t0
    print(json.dumps({"shard": args.shard, "written": n_written, "seconds": round(dt, 1),
                      "seqs_per_sec": round(n_written / max(dt, 1e-6), 1)}))

if __name__ == "__main__":
    main()
