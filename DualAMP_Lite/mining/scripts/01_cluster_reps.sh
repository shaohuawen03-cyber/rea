#!/usr/bin/env bash
# 阶段 2：近似同源家族聚类（0.90/0.90，仅用于降冗余提速，不作 40% 同源切分）。
# 产出：<work>/reps.faa（代表）+ <work>/mm_cluster.tsv（rep<TAB>member）+ <work>/cluster_qc.json（映射闭合检查）。
# 用法：01_cluster_reps.sh unique.faa work_dir [threads]
# 环境变量：ENGINE=mmseqs（默认 easy-cluster）| linclust（测速时用 easy-linclust；家族组成可能不同，不混用于同一批次）
set -euo pipefail
IN="${1:?usage: 01_cluster_reps.sh unique.faa work_dir [threads]}"
WORK="${2:?work dir required}"
THREADS="${3:-8}"
ENGINE="${ENGINE:-mmseqs}"
mkdir -p "$WORK"
command -v mmseqs >/dev/null || { echo "mmseqs 未安装：sudo apt-get install -y mmseqs2  （或 conda install -c bioconda mmseqs2）"; exit 1; }
if [ "$ENGINE" = "linclust" ]; then CMD=(easy-linclust); else CMD=(easy-cluster); fi
echo "engine: ${CMD[*]} ($ENGINE)"
mmseqs "${CMD[@]}" "$IN" "$WORK/mm" "$WORK/tmp" --min-seq-id 0.9 -c 0.9 --cov-mode 0 --threads "$THREADS" -v 2
mv "$WORK/mm_rep_seq.fasta" "$WORK/reps.faa"
python3 - "$IN" "$WORK/reps.faa" "$WORK/mm_cluster.tsv" "$WORK/cluster_qc.json" << 'PY'
import json, sys
uniq_ids = set()
with open(sys.argv[1]) as fh:
    for line in fh:
        if line.startswith(">"):
            uniq_ids.add(line[1:].split()[0])
rep_ids = set()
with open(sys.argv[2]) as fh:
    for line in fh:
        if line.startswith(">"):
            rep_ids.add(line[1:].split()[0])
member_ids = set()
n_members = 0
with open(sys.argv[3]) as fh:
    for line in fh:
        rep, mem = line.rstrip("\n").split("\t", 1)
        rep_ids.add(rep)
        member_ids.add(mem)
        n_members += 1
missing_reps = rep_ids - {l[1:].split()[0] for l in open(sys.argv[2]) if l.startswith(">")}
extra_members = member_ids - uniq_ids
qc = {"engine": sys.argv[3], "unique_inputs": len(uniq_ids), "representatives": len(rep_ids),
      "cluster_rows": n_members, "members_missing_from_reps": len(missing_reps),
      "members_not_in_unique": len(extra_members),
      "reps_fraction": round(len(rep_ids) / max(len(uniq_ids), 1), 4)}
json.dump(qc, open(sys.argv[4], "w"), indent=2, ensure_ascii=False)
print(json.dumps(qc, indent=2, ensure_ascii=False))
assert n_members == len(uniq_ids), "映射闭合失败：cluster 行数 != unique 序列数"
assert not extra_members, "存在不在 unique.faa 中的成员 ID"
print("QC 闭合：每条 unique 序列恰好属于一个家族 ✓")
print("QC 警示（非硬门槛）：reps_fraction 0.3–0.9；<0.3 家族过大，>0.95 降冗余价值低")
PY
