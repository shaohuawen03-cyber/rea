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

## 6. real6-repro 实测结果（2026-11-17T03:16→04:53Z，run 44009042607，success）

**内核侧核验（全部通过）**：版本 torch 2.11.0+cu130 / sklearn 1.6.1 / pandas 2.3.3 / numpy 2.1.3 / Bio 1.88（与 real5 一致）；阳性 sha256 OK（133,202 B）；负例统计与 real5 完全一致（seed 11，351,462→10,000，standard 模式）；leanrea pin 断言通过（`leanrea pinned at f610dc00…938a13`）；mmseqs shim 自检 OK；11,570 簇；split 计数一致（9,505/2,037/2,037@537 阳/500 scrambled）。

**但未达逐位一致（原因已定位）**：`clusters.tsv`、`splits/*.csv`、`dataset_all.fasta`、`features/*.npz`、`biophys_scaler.joblib` 的 sha256 与 real5 全部不同。计数相同、成员不同——即**划分被重新抽样**。根因：real5 未设置 PYTHONHASHSEED（每进程随机 hash 顺序）→ mmseqs 输出顺序/簇划分/split 抽样顺序改变。real6 = 同配方、同锁定代码（f610dc00）、同阳性（sha256 一致）、仅管道顺序不同 → **实质上是一次“重复划分（repeated split）稳健性检验”**。现内核已默认 PYTHONHASHSEED=0（741dbef）。

**划分间差异（real6 − real5）**：

| 模型 | ΔAUROC | ΔMCC | ΔAUPRC | 判定 |
|---|---|---|---|---|
| M0_RF | +0.0056 | −0.0094 | −0.0010 | 划分随机性（RF 对顺序敏感） |
| M1 | −0.0007 | −0.0046 | −0.0020 | 达标（≤ seed SD） |
| M2 | −0.0022 | −0.0197 | −0.0045 | AUROC 达标；MCC 漂移（阈值重拟合） |
| M3 | +0.0005 | −0.0180 | −0.0012 | AUROC 达标；MCC 漂移（阈值重拟合） |

**主结论在重复划分下全部稳健**：M3 vs M0_RF AUROC Δ+0.0115 [0.004, 0.019]，p=0.0018，BH q=0.008（FDR 显著；AUPRC q≈0；MCC Δ+0.036 q=0.020 显著）；M3 vs M1、M3 vs M2 均无 FDR 显著（q≥0.053）；scrambled 判阳率 69.0/59.0/63.8%（real4/real5/real6：M1 67–73 / M2 57–59 / M3 57–64 —— 组成偏好是稳健事实，不是抽样噪声）。

**论文表述建议**：
> “To assess split-to-split variability we repeated the full build with an independent pipeline order (real6-repro): ΔAUROC ≤ 0.006 across all models and both runs preserved every headline conclusion (neural > RF, FDR-significant; no measurable gain from the biophysical branch). Bitwise reproducibility additionally requires PYTHONHASHSEED and environment pinning (implemented since commit 741dbef; PYTHONHASHSEED=0). cuDNN algorithm selection remains a residual source of ~1e-6-level floating-point noise.”

**判定（诚实）**：复刻任务达成“流程级复现 + 附带 split 稳健性证据”，但**未达逐位一致**；real5 本身未固定 hash 种子，事后无法恢复其确切划分。论文中不得声称 bit-exact，只可声称“在 seed SD / split 方差内复现”（实测 CNN 模型 ≤0.002，所有模型 ≤0.006）。

**real7（组成匹配硬负例）**：未派发；依赖 Actions（现已恢复）与单独窗口；real6 结论确立后可随时派发。

## 7. real6b-verify 实测与三次构建汇总（2026-11-17T07:32→09:19Z，run 44039457272，conclusion=success）

**PYTHONHASHSEED=0 未恢复逐位一致**——real6b（同一配方、同锁定代码、同阳性、PYTHONHASHSEED=0）与 real6 的 56 个输入/产物 sha256 仍全部不同（仅 6 个与 real5 逐字节一致），split 计数与 real6/real5 完全一致（9,505/2,037/2,037@537；11,570 簇）。**结论修正**：划分重抽样的根因不是 hash 种子（或不止它），更可能是 mmseqs2 easy-cluster 的多线程输出顺序 / Kaggle 镜像与驱动漂移 / cuDNN autotune。论文不得声称 bit-exact，亦不得以 real6b≈real6 宣称环境已被完全钉死；可声称“三次独立构建统计结论一致”。

**三次独立构建（real5 / real6 / real6b）汇总**：

| 模型 | AUROC real5→real6→real6b | spread | mean±sd | MCC 三次 |
|---|---|---|---|---|
| M0_RF | 0.9640 / 0.9697 / 0.9701 | 0.0061 | 0.9679 ± 0.0034 | 0.831 / 0.821 / 0.807 |
| M1 | 0.9782 / 0.9775 / 0.9820 | 0.0045 | 0.9792 ± 0.0024 | 0.863 / 0.859 / 0.882 |
| M2 | 0.9810 / 0.9789 / 0.9839 | 0.0051 | 0.9813 ± 0.0025 | 0.878 / 0.858 / 0.862 |
| M3 | 0.9806 / 0.9812 / 0.9839 | 0.0032 | 0.9819 ± 0.0017 | 0.875 / 0.857 / 0.878 |

- **M3 > M0_RF（AUROC）三次均 FDR 显著**：real5 Δ+0.0166 (q=2.1e-05)；real6 Δ+0.0115 (q=0.008)；real6b Δ+0.0138 (q=2.3e-05)。方向、量级、显著性完全一致。
- **M3 ≈ M1 ≈ M2 三次均无 FDR 显著**（real6b 中 M3-M2 MCC q=0.027 翻转为显著、M3-M1 AUPRC 不显著——单次边缘翻转，不构成结论变更；M3-M2 AUPRC real6b Δ-0.00005 几乎为零）。
- **scrambled 判阳率三次一致**：M1 0.670/0.690/0.734；M2 0.586/0.590/0.542；M3 0.574/0.638/0.564——顺序稳定（M1 ≫ M2 ≈ M3）→ 组成偏好是**稳健事实**，不是抽样噪声。
- real6b 内核核验：阳性 sha256 OK（133,202 B）；`leanrea pinned at f610dc00…` 断言通过；torch 2.11.0+cu130 等与 real6 一致。

**对论文的意义**：三次独立构建已经给出 headline 全部稳健的证据链；“复刻相同结果”这一字面验收未达（也不可能，除非钉死 Kaggle 镜像+mmseqs 单线程），但**指标级复现界限明确**：AUROC spread ≤ 0.0061（全部模型），seed_sd 0.0002–0.0015；MCC 漂移主要来自 dev-OOF 阈值重拟合（M2 阈值 0.725→0.840→0.892）。

**real7（composition-matched 硬负例）**：2026-11-17T09:28Z 派发（run 44055910802），Positives 窗口 09:28 开→09:47 关并复核仅 owner；结果待回收后写入 §8。

## 8. real7 实测（composition-matched 硬负例，2026-11-17 派发）

### 8.0 派发事故与窗口协议修正（重要）
- **第 1 次派发**（run 44055910802，09:23Z）：内核 **10:50 才真正启动**（T4 排队 ~87 分钟），而 Positives 窗口已按惯例于 ~20 分钟后（09:47）关闭 → 内核在 `positives sha256` 下载处 `gdown` 失败，**verdict=failure（未产出任何科学结果，未动任何既有产物，fail-safe 生效）**。
- **协议修正**：窗口必须保持开放直到 **relay run 完成**（`gh run` 结论出后立即关闭并复核），不能按固定分钟数关闭——内核何时拉取阳性由 Kaggle 排队决定。
- **第 2 次派发**（run 44072623616，`force=true` 重跑同一 job，11:15Z）：窗口 **11:15 开 → 13:12 运行完成后立即关闭，list_permissions 复核仅 owner**。失控窗口暴露时间共 1h57m（可接受，协议已修正）。

### 8.1 构建核验（全部通过）
- 阳性 sha256 OK（133,202 B）；`leanrea pinned at f610dc00…` 断言通过；torch 2.11.0+cu130 / sklearn 1.6.1 一致。
- 硬负例过滤器生效：全量合格片段 350,023 → 与阳性均值 AAC 平方距离 ≤ 30 百分位（cutoff **0.0418**）→ 105,007 → seed 11 抽样 10,000。
- **簇 10,507（标准模式 11,570）；跨标签簇 2 → review=2**（标准模式 review=0）——组成越像，跨标签同源簇越多，泄漏在结构层面显形。

### 8.2 指标（real7 vs real5，Δ）
| 模型 | AUROC | AUPRC | MCC | scrambled 判阳 |
|---|---|---|---|---|
| M0_RF | 0.9888 (+0.0248) | 0.9736 (+0.0302) | 0.8751 (+0.0443) | — |
| M1 | 0.9926 (+0.0143) | 0.9854 (+0.0235) | 0.9238 (+0.0606) | 0.764 (real5: 0.670) |
| M2 | 0.9948 (+0.0138) | 0.9887 (+0.0219) | 0.9170 (+0.0394) | 0.656 (real5: 0.586) |
| M3 | 0.9947 (+0.0141) | 0.9889 (+0.0224) | 0.9235 (+0.0483) | 0.640 (real5: 0.574) |

D9：M3>M0_RF 仍 FDR 显著（Δ+0.0059，q=3.2e-04）；M3 vs M1（auroc q=0.032，auprc q=0.0068）首次翻为 FDR 显著（边缘）；M3 vs M2 仍无显著（q=0.861）。

### 8.3 诚实解读（不可写成“模型更强/修复生效”）
1. **scrambled 判阳率没有下降**（M1 0.764 / M2 0.656 / M3 0.640，均 ≥ 标准模式）→ P1/P2 的“组成偏好”没有被缓解。
2. **Vinpo 假象**：本设计把 composition-matched 负例同时用于 train 和 test。负类分布收窄（单峰、低熵）使任务变容易，AUROC 全线 +0.014~+0.025 是**分布收窄假象**，不是判别力提升；M1 的 frac_predicted_positive 上升（0.229→0.259）进一步说明决策边界整体右移/左移——灵敏度全面上升（M1 0.834→0.935）换来的是 specificity 全面下降（0.993→0.983）。
3. **机制确认**：组成匹配后，模型仍需要额外特征才能赢过难负例；结果与 “AMP 预测器以 AA 组成而非残基顺序为主要信号”（Bioinformatics bbac343；BATTLE-AMP preprint）一致；我们的观测（scrambled FPR 在所有四种配置中均 0.54–0.76）是**结构性事实，须在 limitations 中披露**（P1–P22 照旧）。
4. **真正的 P1/P2 检验（待做，零 GPU 成本）**：不重新训练，而是用 real5/real6 的冻结模型对 **composition-matched 负例做跨分布评估**（matched-vs-standard 负例的判阳率差）；更严格则是“标准训练、matched 测试”的迁移报告。请求你确认后再派（需从 real5 workflow artifact 取回 .pt 权重做评估）。
5. **结论**：real7 提交了一个**负面结果**——组成匹配硬负例不会自动消除组成偏好；论文应报告此点，而不是隐藏。头条结论（神经 > RF，FDR 显著；生物物理分支无可测量增益；组成偏好稳健存在）在四次构建（real5/6/6b/7）下全部保持。

### 8.4 可复用命令
- 重跑 hard-negative：queue job `dualamp-real7-negmatched-20261117`（`--neg-mode composition-matched`），结果在 `cloud-gpu/results/dualamp-real7-negmatched-20261117/`。
- 对照：`python cloud-gpu/scripts/compare_dualamp_runs.py cloud-gpu/results/dualamp-real5-20261116 cloud-gpu/results/dualamp-real7-negmatched-20261117`。

## 9. 当前未闭合项
1. **v2 正式主线**：230 条 Review_Result **0/230 已填**（见 V2_RUNBOOK_2026-11-17.md）——唯一人工闸门；完成后按 runbook 执行 build→real8→Release。
2. P1/P2 跨分布评估（§8.3-4）——等你确认。
3. v0.4 论文附录：real6b 三次汇总 + real7 负面结果待写入（本文件 §7–§8 已为笔）。
