#!/usr/bin/env bash
# 阶段 3.5：本地重训并导出 DualAMP M1/M2 权重（Kaggle relay 不回传 .pt，故必须在本机重训一次）。
# 复现 real4 口径：candidate_v1(sha256 锁) + A 快照阴性(seed 11, cap 10000) → cd-hit 聚类 0.40/0.80 →
# ESM-2 t12_35M 特征 → M1/M2 各 5 seeds × 5 folds × 60 epochs。
# 只训 M1/M2（M3 与 M0_RF 按人工审定不作为主模型，可省 ~1 h）。
# 用法：03_train_export.sh /path/to/positive_core_candidate_v1.fasta /path/to/export_dir [workdir]
set -euo pipefail
# 正式权重 = positive_core_v2 锁定后的重训产物（不是 candidate_v1）。
# POSITIVES_SHA256：正式 run 前必须设为 v2 阳性 fasta 的 sha256；缺省值是 candidate_v1 锁（仅调试）。
POS="${1:?usage: 03_train_export.sh positives.fasta export_dir [workdir]}"
EXPORT="${2:?export dir required}"
WORK="${3:-$HOME/dualamp-export-work}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
LEANREA_DIR="${LEANREA_DIR:-$REPO_DIR/.external/leanrea}"
LEANREA_BRANCH="${LEANREA_BRANCH:-arena/4ecb70f1-leanrea}"
LOCK="${POSITIVES_SHA256:-7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d}"

python3 - "$POS" "$LOCK" << 'PY'
import hashlib, sys
got = hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest()
want = sys.argv[2]
assert got == want, f"阳性 fasta sha256 不匹配: {got} != {want}（用错文件或 fasta 被改动；中止）"
print("positives sha256 OK")
PY

mkdir -p "$WORK/data" "$EXPORT"
cp "$POS" "$WORK/data/positives.fasta"

echo "== 阴性重建（seed 11，A 快照；仓库自带 gz）=="
python3 "$REPO_DIR/cloud-gpu/jobs/dualamp_pilot.py" --mode negatives-only --workdir "$WORK" --neg-cap 10000

echo "== 确保聚类工具（mmseqs 引擎 + cd-hit shim，复用 rea 载荷内自检逻辑）=="
python3 - "$REPO_DIR" << 'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/cloud-gpu/jobs")
import dualamp_pilot as p
p._ensure_clustering()
PY

[ -d "$LEANREA_DIR/dualamp-lite" ] || git clone --depth 1 -b "$LEANREA_BRANCH" https://github.com/hukeying85-ux/leanrea.git "$LEANREA_DIR"

cat > "$WORK/data/sources.yaml" << YAML
sources:
  - id: candidate_v1
    path: $WORK/data/positives.fasta
    format: fasta
    layer: general_amp
  - id: uniprot_a_fragments
    path: $WORK/data/negatives.fasta
    format: fasta
    layer: negative_background
YAML

export PYTHONPATH="$LEANREA_DIR/dualamp-lite"
echo "== build（聚类：与 real4 相同 shim→MMseqs2 口径，0.40/0.80；方法如实记录在 dataset_summary.json）=="
echo "   注意：real4 实测为 mmseqs easy-cluster shim（非原生 cd-hit），v2 沿用同一引擎并固定版本；若坚持原生 cd-hit 需重跑分组"
python3 -m dualamp_lite build --manifest "$WORK/data/sources.yaml" --workdir "$WORK/run" --cluster-method auto
echo "== features（fair-esm t12_35M）=="
python3 -m dualamp_lite features --workdir "$WORK/run" --backend fair-esm --batch-size 64
echo "== train M1,M2（5 seeds × 5 folds × 60 epochs）=="
python3 -m dualamp_lite train --workdir "$WORK/run" --models M1,M2 --seeds 1,2,3,4,5 --folds 5 --epochs 60 --loss bce

echo "== 导出权重到 $EXPORT =="
mkdir -p "$EXPORT/model" "$EXPORT/features"
cp "$WORK"/run/model/M1_seed*.pt "$WORK"/run/model/M2_seed*.pt "$EXPORT/model/"
cp "$WORK"/run/features/biophys_scaler.joblib "$EXPORT/features/"
cp "$WORK"/run/manuscript/D10_test_metrics.csv "$EXPORT/" 2>/dev/null || true
python3 - "$EXPORT" "$LEANREA_DIR" << 'PY'
import hashlib, json, os, sys, subprocess
exp = sys.argv[1]
files = []
for root, _, names in os.walk(exp):
    for n in names:
        p = os.path.join(root, n)
        with open(p, "rb") as fh:
            h = hashlib.sha256(fh.read()).hexdigest()
        files.append({"file": os.path.relpath(p, exp), "bytes": os.path.getsize(p), "sha256": h})
try:
    sha = subprocess.check_output(["git", "-C", sys.argv[2], "rev-parse", "HEAD"]).decode().strip()
except Exception:
    sha = "unknown"
with open(os.path.join(exp, "export_manifest.json"), "w") as fh:
    json.dump({"leanrea_commit": sha, "note": "M1/M2 candidate_v1 pilot models; thresholds inside each .pt blob; NOT for publication until positive_core_v2 freeze",
               "files": files}, fh, indent=2, ensure_ascii=False)
print(json.dumps({"exported": len(files), "leanrea_commit": sha}, indent=2))
PY
echo "完成。export_manifest.json 已记录全部 sha256。"
