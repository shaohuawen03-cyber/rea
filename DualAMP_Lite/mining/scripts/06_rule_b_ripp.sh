#!/usr/bin/env bash
# 路线 B：细菌素/RiPP 前体候选（与线性 AMP 统计隔离；不进 DualAMP 主流程）。
# 用法：06_rule_b_ripp.sh <contigs.fna> <work_dir>
# 需要：conda/pip 安装 antiSMASH v7+（bacteria 模式）与 BAGEL4（docker 或 web）。
set -euo pipefail
CONTIGS="${1:?usage}"
WORK="${2:?work dir}"
mkdir -p "$WORK"
echo "== antiSMASH（RiPP/lantibiotic/lassopeptide 类 BGC）=="
if command -v antismash >/dev/null; then
  antismash "$CONTIGS" --cb-general --cb-subclusters --cb-knownclusters --cc-mibig \
    --output-dir "$WORK/antismash" --cpus 8
else
  echo "antiSMASH 未安装：conda create -n antismash -c bioconda -c conda-forge antismash=7"
fi
echo "== BAGEL4（RiPP 精确位置与前体肽）=="
echo "BAGEL4 建议走官方 docker：docker run -v \$PWD:/input -v \$WORK:/output bagel4 -i /input/contigs.fna -o /output/bagel4"
echo "或 Web：<https://bagel4.molgenrug.nl/>；结果下载 *_ripps.csv"
echo "== 前体→ mature 切割（含 leader）=="
cat << 'PY'
# mature 肽长度典型 19–50 aa；用 leader 剪切 motif（双甘氨酸 GG、Еise基因类 motif）做保守切割占位：
import re
# 仅示例：真正 motif 依 RiPP 类别查 BAGEL4 输出中的 leader_cleavage_site 字段，勿照搬此占位为结论
for line in open("bagel4/bagel4_ripps.csv"):
    pass
PY
echo "路线 B 输出列（与线性 AMP 表分开存）：BGC_ID, RiPP_class, leader_seq, core_seq, core_length, core_charge, host_taxon, BAGEL4_score"
echo "QC：RiPP 候选数量通常 << 线性候选（先验 1e-4~1e-6 量级）；若 core 肽被送入 DualAMP 线性肽统计，会系统性高估/低估阈值——必须隔离。"
