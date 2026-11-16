#!/usr/bin/env bash
# 路线 B：细菌素/RiPP 前体候选 —— 状态：RiPP_workflow_scaffold（已实测可跑 antiSMASH，BAGEL4 解析/切割尚未完整实现）。
# 仅生成独立候选表，禁止并入线性 AMP 的 Tier1/2/3 统计。
# 用法：06_rule_b_ripp.sh <contigs.fna> <work_dir>
set -euo pipefail
CONTIGS="${1:?usage}"
WORK="${2:?work dir}"
mkdir -p "$WORK"
echo "== antiSMASH（RiPP/lantibiotic/lassopeptide 类 BGC）=="
if command -v antismash >/dev/null; then
  antismash "$CONTIGS" --cb-general --cb-subclusters --cb-knownclusters --cc-mibig \
    --output-dir "$WORK/antismash" --cpus 8
else
  echo "antiSMASH 未安装：conda create -n antismash -c bioconda -c conda-forge antismash"
fi
echo "== BAGEL4（RiPP 精确定位；scaffold 占位——尚未自动解析）=="
echo "BAGEL4 docker：docker run -v \$PWD:/input -v \$WORK:/output bagel4 -i /input/contigs.fna -o /output/bagel4"
echo "Web：<https://bagel4.molgenrug.nl/>；产物 <work>/bagel4/*_ripps.csv"
echo "TODO(正式 run 前必须完成)：1) 解析 BAGEL4 CSV 的 leader/core 边界 2) core 肽去重 3) 独立列（BGC_ID RiPP_class leader core_seq core_length host_taxon score）"
echo "QC：RiPP 输出单独成表（ripp_candidates.tsv）；送测前人工核对成熟肽边界；不得回流 Tier 表。"
