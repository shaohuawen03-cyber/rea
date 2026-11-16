#!/usr/bin/env bash
# 阶段 2：近似同源家族聚类（0.90/0.90，仅用于降冗余提速，不作 40% 同源切分）。
# 产出：<work>/reps.faa（代表序列，后续 Macrel + ESM 只跑代表）+ <work>/*_cluster.tsv（家族映射）。
# 用法：01_cluster_reps.sh unique.faa work_dir [threads]
set -euo pipefail
IN="${1:?usage: 01_cluster_reps.sh unique.faa work_dir [threads]}"
WORK="${2:?work dir required}"
THREADS="${3:-8}"
mkdir -p "$WORK"
command -v mmseqs >/dev/null || { echo "mmseqs 未安装：sudo apt-get install -y mmseqs2  （或见 https://github.com/soedinglab/MMseqs2）"; exit 1; }
mmseqs easy-cluster "$IN" "$WORK/mm" "$WORK/tmp" --min-seq-id 0.9 -c 0.9 --cov-mode 0 --threads "$THREADS" -v 2
mv "$WORK/mm_rep_seq.fasta" "$WORK/reps.faa"
echo "unique: $(grep -c '^>' "$IN")  reps: $(grep -c '^>' "$WORK/reps.faa")"
python3 - "$IN" "$WORK/reps.faa" << 'PY'
import sys
n_u = sum(1 for l in open(sys.argv[1]) if l.startswith('>'))
n_r = sum(1 for l in open(sys.argv[2]) if l.startswith('>'))
print(f"QC 门槛：reps/unique = {n_r/max(n_u,1):.3f}（正常 0.3–0.9；<0.3 家族过大，检查 getorf 是否在高频重复区切片；>0.95 几乎无降冗余价值，ESM 直接跑 unique 即可）")
PY
