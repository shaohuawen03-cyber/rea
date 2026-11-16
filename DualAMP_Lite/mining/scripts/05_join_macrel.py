#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 5：合并 DualAMP 打分 + Macrel 判定 → AMP_candidate_priority.tsv（Tier1/2/3）。
长度分层：5–9（探索）/10–50（双模型+Macrel 核心）/51–100（Macrel 专属，DualAMP 分数仅参考）/>100（应已为 00 剔除）。
Macrel 语义（v1.6.1 实测）：macrel prediction.gz 只列出 AMP 阳性记录（列出的=判阳）；
  未列出且 --no-negatives 时记为 NON_AMP（默认 macrel 只报 AMP，见 02 脚本）；
  若 Macrel 表缺失该输入 → NA（不能当阴性）。--macrel-missing {nonamp,na} 控制（默认 nonamp）。
Tier 规则（与 README 严格一致）：
  Tier1 = (M1+ ∨ M2+) ∧ Macrel+ 或 M1+ ∧ M2+
  Tier2 = M2 ≥ 0.95 或 M1 ≥ 0.90 且 M2 ≥ 0.75
  Tier3 = 其余
理化列优先使用 leanrea 的 dualamp_lite.biophys（Henderson-Hasselbalch，含端基），
  不可用时回退到内置等价实现。GRAVY = Kyte-Doolittle 平均。
用法：05_join_macrel.py --pred shard*.tsv --macrel-out <macrel out dir> --out AMP_candidate_priority.tsv \
        [--macrel-tsv <explicit>] [--leanrea-dir <path>] [--cluster-tsv cluster.tsv --unique-faa unique.faa] [--m1-thr 0.685 --m2-thr 0.885]
"""
import argparse, csv, glob, json, os, sys

AA_NAMES = "ACDEFGHIKLMNPQRSTVWY"
KD_HYDROPATHY = {"A":1.8,"R":-4.5,"N":-3.5,"D":-3.5,"C":2.5,"Q":-3.5,"E":-3.5,"G":-0.4,"H":-3.2,
                 "I":4.5,"L":3.8,"K":-3.9,"M":1.9,"F":2.8,"P":-1.6,"S":-0.8,"T":-0.7,"W":-0.9,"Y":-1.3,"V":4.2}

def _fallback_biophys():
    PKA_NTERM, PKA_CTERM = 9.69, 2.34
    PKA_SIDECHAIN = {"D":3.9, "E":4.2, "C":8.3, "Y":10.1, "H":6.0, "K":10.5, "R":12.5}
    def net_charge(seq, ph=7.4):
        q = 1.0 / (1.0 + 10 ** (ph - PKA_NTERM)) - 1.0 / (1.0 + 10 ** (PKA_CTERM - ph))
        for aa in seq:
            pka = PKA_SIDECHAIN.get(aa)
            if pka is None:
                continue
            fp = 1.0 / (1.0 + 10 ** (ph - pka))
            q += fp if aa in "HKR" else -(1.0 - fp)
        return q
    def isoelectric_point(seq, tol=1e-3):
        lo, hi = 0.0, 14.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if net_charge(seq, mid) > 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < tol:
                break
        return 0.5 * (lo + hi)
    def gravy(seq):
        return sum(KD_HYDROPATHY.get(a, 0.0) for a in seq) / max(len(seq), 1)
    return net_charge, isoelectric_point, gravy

_PHYS = _fallback_biophys()

def load_leanrea_biophys(d):
    """若 leanrea 可用，返回其 biophys 三函数（(net_charge, isoelectric_point, gravy)），否则 None。"""
    if not d or not os.path.isdir(d):
        return None
    cand = os.path.join(d, "dualamp-lite") if os.path.isdir(os.path.join(d, "dualamp-lite")) else d
    if not os.path.exists(os.path.join(cand, "dualamp_lite", "biophys.py")):
        return None
    if cand not in sys.path:
        sys.path.insert(0, cand)
    try:
        from dualamp_lite import biophys  # type: ignore
        return biophys.net_charge, biophys.isoelectric_point, biophys.gravy
    except Exception:
        return None

def _phys3():
    return _PHYS

def net_charge(seq):
    import inspect
    fn = _PHYS[0]
    try:
        argc = len(inspect.signature(fn).parameters)
    except Exception:
        argc = 2
    return round(float(fn(seq, 7.4) if argc >= 2 else fn(seq)), 2)

def pI(seq):
    return round(float(_PHYS[1](seq)), 2)

def gravy(seq):
    return round(float(_PHYS[2](seq)), 2)

# ---------- Macrel 解析 ----------
def _open_maybe_gzip(path):
    if path.endswith(".gz"):
        import gzip
        return gzip.open(path, "rt")
    return open(path)

def find_macrel_tsv(outdir):
    for pat in ("*.prediction.gz", "*.prediction.tsv", "*prediction*.tsv", "*amp*.tsv"):
        cands = glob.glob(os.path.join(outdir, "**", pat), recursive=True)
        if cands:
            return cands[0]
    return None

def read_macrel(tsv, missing_as_nonamp=True):
    """返回 {seq: (is_amp: bool|None, hemolytic: str)}；NA 不进字典"""
    result = {}
    with _open_maybe_gzip(tsv) as fh:
        lines = [l for l in fh if not l.startswith("#")]
    header = lines[0].rstrip("\n").split("\t") if lines else []
    rows = list(csv.DictReader(lines, delimiter="\t")) if lines else []  # DictReader 自动以首行为 fieldnames
    def col(cands):
        for c in cands:
            for orig in header:
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
            amp = float(raw) > 0.5
        except ValueError:
            amp = raw in ("true", "yes", "amp", "1", "t")
        hemo = str(r[lcol]).strip() if lcol and r[lcol] is not None else "unknown"
        result[seq] = (amp, hemo)
    return result

def length_layer(l):
    if l < 10: return "5-9"
    if l <= 50: return "10-50"
    if l <= 100: return "51-100"
    return ">100"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", required=True, help="逗号分隔的 DualAMP 打分 TSV（04 输出）")
    ap.add_argument("--macrel-out", required=True)
    ap.add_argument("--macrel-tsv", default=None)
    ap.add_argument("--macrel-missing", choices=["nonamp", "na"], default="nonamp",
                    help="Macrel 表中缺席序列的判定（默认 nonamp；macrel 只输出 AMP 的前提下），out-of-scope(5-9/>100) 一律记 NA")
    ap.add_argument("--out", required=True)
    ap.add_argument("--m1-thr", type=float, default=0.685)
    ap.add_argument("--m2-thr", type=float, default=0.885)
    ap.add_argument("--leanrea-dir", default=os.path.join(".", ".external", "leanrea"),
                    help="leanrea 克隆目录（可选；用于加载其校验过的 biophys）")
    ap.add_argument("--cluster-tsv", default=None, help="mmseqs cluster tsv（rep<TAB>member）")
    ap.add_argument("--unique-faa", default=None, help="unique.faa（first_id→seq 映射，用于 Family_Size）")
    ap.add_argument("--cluster-members", default=None, help="替代：seq<TAB>family_size 直接给出")
    args = ap.parse_args()

    global _PHYS
    lr = load_leanrea_biophys(args.leanrea_dir)
    if lr:
        _PHYS = lr

    m1_thr, m2_thr = args.m1_thr, args.m2_thr

    macrel_tsv = args.macrel_tsv or find_macrel_tsv(args.macrel_out)
    macrel = read_macrel(macrel_tsv) if macrel_tsv else None
    macrel_missing_as_nonamp = args.macrel_missing == "nonamp"

    fam_size = {}
    if args.cluster_members and os.path.exists(args.cluster_members):
        with open(args.cluster_members) as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 2:
                    try:
                        fam_size[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    elif args.cluster_tsv and os.path.exists(args.cluster_tsv):
        id2seq = {}
        if args.unique_faa and os.path.exists(args.unique_faa):
            name, buf = None, []
            with open(args.unique_faa) as fh:
                for line in fh:
                    if line.startswith(">"):
                        if name is not None:
                            id2seq[name] = "".join(buf)
                        name, buf = line[1:].split()[0], []
                    else:
                        buf.append(line.strip().upper())
                if name is not None:
                    id2seq[name] = "".join(buf)
        with open(args.cluster_tsv) as fh:
            for line in fh:
                rep, mem = line.rstrip("\n").split("\t", 1)
                rep_seq = id2seq.get(rep)
                if rep_seq:
                    fam_size[rep_seq] = fam_size.get(rep_seq, 0) + 1

    rows = []
    n_m_amp = n_m_nonamp = n_m_na = 0
    for pred in args.pred.split(","):
        with open(pred) as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                seq = r["seq"].strip().upper()
                p1, p2 = float(r["m1_prob"]), float(r["m2_prob"])
                layer = length_layer(len(seq))
                if macrel is None or layer in ("5-9", ">100"):
                    macrel_result = "NA"
                elif seq in macrel:
                    macrel_result = "AMP" if macrel[seq][0] else "NON_AMP"
                else:
                    macrel_result = "NON_AMP" if macrel_missing_as_nonamp else "NA"
                hemo = (macrel.get(seq, (None, "unknown"))[1] if macrel else "unknown") if seq in (macrel or {}) else "unknown"
                mamp = True if macrel_result == "AMP" else (False if macrel_result == "NON_AMP" else None)
                n_m_amp += macrel_result == "AMP"
                n_m_nonamp += macrel_result == "NON_AMP"
                n_m_na += macrel_result == "NA"

                p1_pos = p1 >= m1_thr
                p2_pos = p2 >= m2_thr
                if (p1_pos or p2_pos) and mamp is True:
                    tier = 1
                elif p1_pos and p2_pos:
                    tier = 1
                elif (p2 >= 0.95) or (p1 >= 0.90 and p2 >= 0.75):
                    tier = 2
                else:
                    tier = 3
                rows.append({"Sequence": seq, "Length": len(seq), "Layer": layer,
                             "M1_Score": round(p1, 4), "M2_Score": round(p2, 4),
                             "M1_Pred": int(p1_pos), "M2_Pred": int(p2_pos),
                             "Macrel_Result": macrel_result, "Macrel_Hemolytic": hemo,
                             "Net_Charge": net_charge(seq), "GRAVY": gravy(seq), "pI": pI(seq),
                             "Family_Size": fam_size.get(seq, 1), "Tier": tier})

    rows.sort(key=lambda r: (r["Tier"], -r["M2_Score"], -r["M1_Score"]))
    fields = ["Sequence","Length","Layer","M1_Score","M2_Score","M1_Pred","M2_Pred","Macrel_Result",
              "Macrel_Hemolytic","Net_Charge","GRAVY","pI","Family_Size","Tier"]
    with open(args.out, "w", newline="") as fh:
        fh.write("\t".join(fields) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[k]) for k in fields) + "\n")
    tiers = {t: sum(1 for r in rows if r["Tier"] == t) for t in (1, 2, 3)}
    layers = {}
    for r in rows:
        layers[r["Layer"]] = layers.get(r["Layer"], 0) + 1
    qc = {"pred_files": args.pred.split(","),
          "macrel_tsv": macrel_tsv or "NOT_FOUND",
          "total": len(rows), "tier1": tiers[1], "tier2": tiers[2], "tier3": tiers[3],
          "macrel_amp": n_m_amp, "macrel_non_amp": n_m_nonamp, "macrel_na": n_m_na,
          "layers": layers, "thr": {"M1": m1_thr, "M2": m2_thr}}
    with open(os.path.splitext(args.out)[0] + "_qc.json", "w") as fh:
        json.dump(qc, fh, indent=2, ensure_ascii=False)
    print(json.dumps(qc, indent=2, ensure_ascii=False))
    print("QC：Tier1 占预测肽数 0.1–5% 为经验警示（非硬门槛）；macrel_na 不等于阴性")

if __name__ == "__main__":
    main()
