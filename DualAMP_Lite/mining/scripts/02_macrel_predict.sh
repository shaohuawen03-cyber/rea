#!/usr/bin/env bash
# 阶段 3a：Macrel 独立筛选（v1.6.1 实测 CLI）。
# 用法：02_macrel_predict.sh reps.faa out_dir [threads]
# 产物：<out_dir>/macrel.out.prediction.gz（仅含预测为 AMP 的肽，表头：Access Sequence AMP_family AMP_probability Hemolytic Hemolytic_probability）
# 语义：match → Macrel 判阳；未匹配 → Macrel 判非 AMP（macrel 默认不输出非 AMP；加 MACREL_EXTRA=--keep-negatives 可保留）。
set -euo pipefail
IN="${1:?usage: 02_macrel_predict.sh reps.faa out_dir [threads]}"
OUT="${2:?out dir required}"
THREADS="${3:-8}"
# macrel 要求输出目录为空/不存在——只建父目录，避免它提前退出
mkdir -p "$(dirname "$OUT")"
if [[ -d "$OUT" && -n "$(ls -A "$OUT" 2>/dev/null)" ]]; then
  if [[ "${MACREL_FORCE:-0}" == "1" ]]; then
    echo "输出目录非空但 MACREL_FORCE=1，追加 --force"
    MACREL_EXTRA="${MACREL_EXTRA:---no-download-database} --force"
  else
    echo "输出目录 $OUT 非空——macrel 要求为空。加 MACREL_FORCE=1 覆盖，或换目录。" >&2
    exit 1
  fi
fi

if ! python3 -c "import macrel" 2>/dev/null; then
  echo "macrel 未在当前解释器中安装；建议：python3 -m venv .venv-macrel && .venv-macrel/bin/pip install macrel==1.6.1"
  echo "然后把本脚本中的 MACREL_BIN 指向 .venv-macrel/bin/macrel（默认用 PATH 中的 macrel）"
fi
MACREL_BIN="${MACREL_BIN:-macrel}"
EXTRA="${MACREL_EXTRA:---no-download-database}"
echo "running: $MACREL_BIN peptides -f $IN -o $OUT -t $THREADS $EXTRA"
"$MACREL_BIN" peptides -f "$IN" -o "$OUT" -t "$THREADS" $EXTRA
echo "---- QC：产物列检查 ----"
python3 - "$OUT" << 'PY'
import gzip, json, sys, glob
f = sorted(glob.glob(sys.argv[1] + "/**/*.prediction.gz", recursive=True))
assert f, "未找到 macrel.out.prediction.gz"
path = f[0]
with gzip.open(path, "rt") as fh:
    for line in fh:
        if line.startswith("#"):
            continue
        head = line.rstrip("\n").split("\t")
        break
need = {"Sequence", "AMP_probability", "Hemolytic"}
missing = need - set(head)
print(json.dumps({"prediction_file": path, "columns": head, "missing": sorted(missing)}, indent=2, ensure_ascii=False))
assert not missing
PY
echo "QC 门槛：AMP 率（预测行数/输入数）文献参考 0.1–1.65%（人肠道宏基因组）；>5% 先查输入同源块。"
