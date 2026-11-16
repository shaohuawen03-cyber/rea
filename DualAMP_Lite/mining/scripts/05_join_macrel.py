#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 5：合并 DualAMP 打分 + Macrel 判定 → AMP_candidate_priority.tsv（ Tier1/2/3）。
输入：04_batch_score.py 的 pred TSV(s) + macrel 输出目录。
Macrel 输出：默认 auto 探测 amp_prediction*、*_prediction*.tsv 等；用 --macrel-tsv 显式指定。
Tier 规则（用户已采纳）：
  Tier1 = (M1 或 M2 判阳) 且 Macrel 判阳 → 或 (M1∧M2) 双阳且同源簇内折半零；取“双证据”最高优先级
  Tier2 = 单模型高分（M1>=0.90 或 M2>=0.95）且 Macrel 阴、或 (M1∧M2) 双阳但 Macrel 缺席/边界
  Tier3 = 其余（单模型阳、或组成驱动型：scrambled 判阳模型偏好）
附加列：net_charge / GRAVY / pI（纯 Python，无依赖）、长度、代表序列所属家族大小（若有 cluster tsv）。
输出：AMP_candidate_priority.tsv + qc_priority.json（Tier 计数漏斗）。
用法：05_join_macrel.py --pred shard0.tsv,shard1.tsv --macrel-out macrel_out --out AMP_candidate_priority.tsv \
        [--macrel-tsv macrel_out/amp_prediction.tsv] [--cluster-tsv work/mm_cluster.tsv] [--cluster-to-seq work/mm_rep_seq.fasta]
"""
import argparse, csv, glob, json, math, os, re

# ---------- 纯 Python 理化 ----------
PKA_POS = {"K": 10.5, "R": 12.5, "H": 6.0}
PKA_NEG = {"D": 3.9, "E": 4.2, "C": 8.3, "Y": 10.1}
MASS = {"A": 71.08, "R": 156.19, "N": 114.10, "D": 115.09, "C": 103.14, "E": 129.12,
        "Q": 128.13, "G": 57.05, "H": 137.14, "I": 113.16, "L": 113.16, "K": 128.17,
        "M": 131.19, "F": 147.18, "P": 97.12, "S": 87.08, "T": 101.11, "W": 186.21,
        "Y": 163.18, "V": 99.13}
GRAVY_VAL = {"A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "E": -3.5, "Q": -3.5,
             "G": -0.4, "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8,
             "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2}

def net_charge(seq):
    pos = sum(1 for a in seq if a in PKA_POS)
    # C/Y 在中性 pH 通常接近中性，仅 D/E 计负；末端忽略（短肽近似）
    neg = sum(1 for a in seq if a in ("D", "E"))
    return pos - neg

def gravy(seq):
    return sum(GRAVY_VAL.get(a, 0.0) for a in seq) / max(len(seq), 1)

def pI(seq):
    n_term = 9.69
    c_term = 2.34
    counts = {a: seq.count(a) for a in set(seq)}
    def net_at_ph(ph):
        n = 10 ** (n_term - ph) / (1 + 10 ** (n_term - ph))
        c = 1 / (1 + 10 ** (ph - c_term))
        q = n - c
        for a, pka in PKA_POS.items():
            q += counts.get(a, 0) * (10 ** (pka - ph) / (1 + 10 ** (pka - ph)))
        for a, pka in PKA_NEG.items():
            q -= counts.get(a, 0) / (1 + 10 ** (pka - ph))
        return q
    lo, hi = 2.0, 13.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if net_at_ph(mid) > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)

# ---------- Macrel 解析 ----------
def find_macrel_tsv(outdir):
    cands = glob.glob(os.path.join(outdir, "**", "*.prediction.gz"), recursive=True)
    cands += glob.glob(os.path.join(outdir, "**", "*.prediction.tsv"), recursive=True)
    cands += glob.glob(os.path.join(outdir, "**", "*prediction*.tsv"), recursive=True)
    cands += glob.glob(os.path.join(outdir, "**", "*amp*.tsv"), recursive=True)
    return cands[0] if cands else None


def _open_maybe_gzip(path):
    if path.endswith(".gz"):
        import gzip
        return gzip.open(path, "rt")
    return open(path)

def read_macrel(tsv):
    """解析 Macrel v1.6.1 产物（macrel.out.prediction.gz；支持 TSV 回退）。

    列：Access Sequence AMP_family AMP_probability Hemolytic Hemolytic_probability
    文件开头有一行 `# Prediction from macrel v1.6.1` 注释，需跳过。
    语义：列出的肽 = Macrel 判阳（macrel 默认只输出 AMP）；未列出 = 判非 AMP。
    """
    result = {}
    with _open_maybe_gzip(tsv) as fh:
        lines = [l for l in fh if not l.startswith("#")]
    rows = list(csv.DictReader(lines, delimiter="\t"))
    def col(cands):
        keys = rows[0].keys() if rows else []
        for c in cands:
            for orig in keys:
                if orig.lower() == c:
                    return orig
        return None
    ccol = col(["sequence", "peptide", "seq"])
    pcol = col(["amp_probability", "probability", "prediction", "amp", "is_amp", "label", "pred"])
    lcol = col(["hemolytic", "lytic", "lysis"])
    if ccol is None or pcol is None:
        return None
    for r in rows:
        seq = (r[ccol] or "").split()[0].strip().upper()
        raw = str(r[pcol]).strip().lower()
        try:
            amp = float(raw) > 0.5 if raw not in ("true", "false", "yes", "no", "amp") else raw in ("true", "yes", "amp")
        except ValueError:
            amp = raw in ("true", "yes", "amp", "1", "t")
        hemo = str(r[lcol]).strip() if lcol and r[lcol] is not None else "unknown"
        result[seq] = (amp, hemo)
    return result

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="逗号分隔的 DualAMP 打分 TSV（04 输出）")
    ap.add_argument("--macrel-out", required=True)
    ap.add_argument("--macrel-tsv", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--m1-thr", type=float, default=None, help="缺省读 .pt 均值打分产物中的参考 0.5，实际出厂阈值见文档；建议用训练阈值")
    ap.add_argument("--m2-thr", type=float, default=None)
    ap.add_argument("--cluster-tsv", default=None, help="mmseqs cluster tsv（rep<TAB>member，ID 口径=unique.faa 的表头）")
    ap.add_argument("--unique-faa", default=None, help="unique.faa（first_id→seq 映射，配合 --cluster-tsv 计算家族大小）")
    args = ap.parse_args()

    m1_thr = args.m1_thr if args.m1_thr is not None else 0.685
    m2_thr = args.m2_thr if args.m2_thr is not None else 0.885

    macrel_tsv = args.macrel_tsv or find_macrel_tsv(args.macrel_out)
    macrel = read_macrel(macrel_tsv) if macrel_tsv else None
    macrel_status = macrel_tsv if macrel_tsv else "NOT_FOUND"

    fam_size = {}
    if args.cluster_tsv and os.path.exists(args.cluster_tsv):
        id2seq = {}
        if args.unique_faa and os.path.exists(args.unique_faa):
            name, buf = None, []
            with open(args.unique_faa) as fh:
                def _emit():
                    if name is not None:
                        id2seq[name] = "".join(buf)
                for line in fh:
                    if line.startswith(">"):
                        _emit(); name = line[1:].split()[0]; buf = []
                    else:
                        buf.append(line.strip().upper())
                _emit()
        with open(args.cluster_tsv) as fh:
            for line in fh:
                rep, mem = line.rstrip("\n").split("\t", 1)
                rep_seq = id2seq.get(rep)
                if rep_seq is None:
                    continue
                fam_size[rep_seq] = fam_size.get(rep_seq, 0) + 1

    rows = []
    for pred in args.pred.split(","):
        with open(pred) as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                seq = r["seq"].strip().upper()
                p1 = float(r["m1_prob"]); p2 = float(r["m2_prob"])
                mamp, hemo = (macrel or {}).get(seq, (None, None))
                fam = fam_size.get(seq, 1)
                l = len(seq)
                if (p1 >= m1_thr and mamp) or (p2 >= m2_thr and mamp) or (p1 >= m1_thr and p2 >= m2_thr):
                    tier = 1
                elif (p2 >= m2_thr) or (p1 >= m1_thr and p2 >= 0.75) or (p2 >= 0.95):
                    tier = 2
                else:
                    tier = 3
                rows.append({
                    "Sequence": seq, "Length": l, "M1_Score": round(p1, 4), "M2_Score": round(p2, 4),
                    "M1_Pred": int(p1 >= m1_thr), "M2_Pred": int(p2 >= m2_thr),
                    "Macrel_AMP": "" if mamp is None else int(bool(mamp)),
                    "Macrel_Hemolytic": hemo if hemo is not None else "unknown",
                    "Net_Charge": net_charge(seq), "GRAVY": round(gravy(seq), 2), "pI": pI(seq),
                    "Family_Size": fam, "Tier": tier,
                })

    rows.sort(key=lambda r: (r["Tier"], -r["M2_Score"], -r["M1_Score"]))
    with open(args.out, "w", newline="") as fh:
        fields = list(rows[0].keys()) if rows else ["Sequence","Length","M1_Score","M2_Score","M1_Pred","M2_Pred","Macrel_AMP","Macrel_Hemolytic","Net_Charge","GRAVY","pI","Family_Size","Tier"]
        fh.write("\t".join(fields) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[k]) for k in fields) + "\n")
    tiers = {t: sum(1 for r in rows if r["Tier"] == t) for t in (1, 2, 3)}
    qc = {"pred_files": args.pred.split(","), "macrel_tsv": macrel_status,
          "total": len(rows), "tier1": tiers[1], "tier2": tiers[2], "tier3": tiers[3],
          "macrel_matched": sum(1 for r in rows if r["Macrel_AMP"] != ""),
          "thr": {"M1": m1_thr, "M2": m2_thr}}
    with open(os.path.splitext(args.out)[0] + "_qc.json", "w") as fh:
        json.dump(qc, fh, indent=2, ensure_ascii=False)
    print(json.dumps(qc, indent=2, ensure_ascii=False))
    print("QC 门槛：Tier1 占 unique 代表 0.1–3% 属正常；Tier1 若 >5% 提示阈值/输入同源块偏差，先检查 Macrel 匹配率 macrel_matched>90%。")

if __name__ == "__main__":
    import os
    main()
