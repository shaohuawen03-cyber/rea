#!/usr/bin/env bash
# 阶段 3：Macrel 独立筛选（与 DualAMP 并行，结果并集，不互斥）。
# 用法：02_macrel_predict.sh reps.faa out_dir
# macrel 官方 PyPI：pip install macrel（v1.6.1，<https://pypi.org/project/macrel/>）。CLI 大版本间参数有差异，
# 失败时先 macrel --help 对照官方文档（<https://www.big-data-biology.org/software/macrel>），EXTRA 参数经 MACREL_ARGS 覆盖。
set -euo pipefail
IN="${1:?usage: 02_macrel_predict.sh reps.faa out_dir}"
OUT="${2:?out dir required}"
mkdir -p "$OUT"
python3 -c "import macrel" 2>/dev/null || pip install -q macrel
EXTRA="${MACREL_ARGS:---threads 8}"
echo "running: macrel predict -i $IN -o $OUT $EXTRA"
macrel predict -i "$IN" -o "$OUT" $EXTRA
echo "---- 产物清单与前几行（QC：确认预测列存在；amp 占比文献参考 0.1–1.65%/宏基因组，异常偏高提示输入含大量已知 AMP 同源）----"
find "$OUT" -type f | head -20
for f in $(find "$OUT" -name '*.tsv'); do echo "== $f"; head -2 "$f"; done
