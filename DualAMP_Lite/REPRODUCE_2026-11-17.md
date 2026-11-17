# 可复现性审计与复刻说明（real5 / real6-repro）

**结论先行**：real5 的数据、阳性 sha256、随机种子、划分、聚类口径、模型代码（leanrea 分支 tip）均可被精确锁定；环境（torch/fair-esm/Kaggle 镜像）只能记录不能字节级锁定。论文中应写“冻结数据 sha256 + 锁定代码 commit + seeds/folds 完全一致”，并以 real6-repro 的实测偏差作为复刻误差边界。

## 1. 完全可锁定的部分（bit-exact 承诺范围）

| 项 | 值 / 来源 |
|---|---|
| 阳性 FASTA sha256 | `7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d`（133,202 B；未入库，论文附录写哈希即可） |
| 阴性构建 | Swiss-Prot A 快照（仓库内 `uniprot_bacteria_cytoplasm_reviewed.fasta.gz` / `.tsv.gz`），seed 11，cap 10,000，keyword 排除 18，>4-mer@0.8 与阳性重合剔除 1,096（`_pilot_summary.json`） |
| 唯一序列 / 同源簇 | 14,079 条 / **11,570 簇**（mmseqs2 easy-cluster，shim 桥接，identity 0.40 / coverage 0.80；0 跨标签簇） |
| 划分 | 簇 80/10/10；train 9,505（2,505 阳）/ val 2,037（537）/ test 2,037（537）/ scrambled 500 |
| ESM 特征 | fair-esm `esm2_t12_35M_UR50D`，layer 12，480 维，masked-mean，fp16；scaler train-only |
| 训练 | seeds 1–5 × folds 5 × epochs 60 × BCE；阈值 = dev OOF 最大化 MCC；early stop on val AUROC |
| 模型代码 | **leanrea @ `f610dc00b1aec3ab948a5b77574af0e644259a13`**（分支 `arena/4ecb70f1-leanrea` tip；经 `gh api …?until=2026-11-16T10:00:00Z` 核对：2026-11-11T11:59Z 落 tip 后未再移动，即 real4/real5 实际使用的就是这个树）。2026-11-17 起内核默认 `checkout --detach $LEANREA_REV` 并 `git rev-parse HEAD` 断言，防 tip 漂移 |
| 本仓库 | `arena/cac2205a-rea`，本文档生成时 HEAD = `5f54618`+本次提交（含本次内核/阻塞修复） |
| PYTHONHASHSEED | 内核 2026-11-17 起默认 `PYTHONHASHSEED=0` |

## 2. 只能记录、不能字节级锁定的部分

- Kaggle T4 驱动/CUDA：记录为 torch 2.11.0+cu130 / python 3.13.16 / cuDNN 未锁（实践中 cuDNN autotune 可能引起 ~1e-6 级浮点差异；AUROC 差异预期 < 1e-3，远小于 seed 间 SD 0.0005–0.0013）。
- fair-esm 版本：real5 时未 `pip show`（已补打日志）；fair-esm 0.4.x 行为稳定（t12_35M 权重自 2021 未变）。
- mmseqs2 apt 版本：`mmseqs version` 已补打日志；real4/real5 均为 18-8cc5c（见 kernel 日志）。
- 结果中的 `seconds` 会随机器负载波动（M2 1,530 s / M3 1,781 s 量级），不影响任何指标。

## 3. real5 → real6-repro：如何验证“复刻的结果一样”

> **2026-11-17 03:05Z 事件**：`gh workflow_dispatch workflow=gpu-relay.yml -f only_id=dualamp-real6-repro-20261117` 返回 HTTP 422 —— 仓库 GitHub Actions 被禁用（`Actions has been disabled for this repository`），push 触发同样未产生 run。real6 **未运行**。Positives Drive 链接窗口已立即关闭并 list_permissions 复核（仅 owner）。**恢复路径**：仓库 owner 于 Settings → Actions → General 重新启用 Actions，然后重推 queue.json 或 workflow_dispatch（only_id=dualamp-real6-repro-20261117）；重新派发前需临时开启 Positives anyone-with-link 窗口，跑后即焚。

1. 派发 `dualamp-real6-repro-20261117`（`--neg-mode standard`，行为零改动，仅 pin + 日志）。
2. 对比 `manuscript_D10_test_metrics.csv` 的 M2/M3/M1 的 AUROC/MCC/F1 与 real5：
   - **合格**：|ΔAUROC| ≤ 0.001（即 ≤ seed SD），MCC ≤ 0.005；
   - 若超出 → 先查 `leanrea pinned at f610dc00…` 行、mmseqs/fair-esm 版本行，再查阳性 sha256 行。
3. 论文中写法：「以 real6-repro 作为独立复刻，ΔAUROC = x，在 seed SD 范围内，证实流程可复现」。
4. 资源：单 T4 ≈ 100 分钟（4 模型 × 5 seeds × 5 folds）；产出落在 `cloud-gpu/results/dualamp-real6-repro-20261117/`，与 real5 并列保存、不覆盖。

## 4. scrambled / 组成偏好修复（real7，未派发）

- `dualamp_pilot.py --neg-mode composition-matched`：阴性候选仅保留其 20 维氨基酸组成与阳性均值组成**平方距离 ≤ 全量合格片段 30 百分位**的片段（同 seed 11、cap 10,000、其余流程不变）。
- 预期：scrambled 判阳率显著下降，test AUROC 也会下降（诚实成本）——这是 P1/P2 的直接检验，不是“提分”。
- 派发前提：real6 先跑完（同一 Positives 链接窗口会覆盖两个内核，分两次开/关更安全）。
- v2（230 条 Review_Result 回填）完成后，所有口径并入 v2 重训，不再沿用 candidate_v1 数字。

## 5. 一键复核清单（答辩/审稿现场）

```bash
sha256sum positive_core_candidate_v1.fasta   # 应等于 7b6ea121…aa1d
git log --oneline -1                          # 应等于包含本文件的提交
python cloud-gpu/scripts/pack_release_assets.py --help   # 权重打包（仅 Release 用）
# 指标复核：对比 manuscript_D10_test_metrics.csv（real5）与 real6-repro 同名文件
```
