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
3. ~~v0.4 论文附录~~（已完成：v0.4 附录 H.5–H.7 已写入并上传 Drive，见 §10.4；本节保留原始记录）
4. P1/P2 选项 A（冻结 real5 跨分布评估）需授权；选项 B 的 scrambled 四构建证据见 §10.3。

## 10. 2026-11-18 状态更新（本节由审计补记；不改写 §1–§9 的原始记录）

### 10.1 权重（real5 candidate_v1 pilot）存放状态
- GitHub **prerelease** `dualamp-real5-candidate_v1-pilot-weights-20261116`（Pre-release，2026-11-16 发布；M1/M2/M3 × seeds 1–5，15 个 .pt + README 性质说明）：资产 `dualamp-real5-candidate_v1-pilot-weights-20261116.tar.gz`（20,928,730 B）、`export_manifest.json`（5,207 B）、`SHA256SUMS.txt`（2,439 B）。发布任务完成于 2026-11-16T11:58Z（run 归档经 weights-release workflow）。
- **本地 sha256 复核未完成**：本 sandbox 网络对 `release-assets.githubusercontent.com` 连接 EOF（2026-11-18T01:38Z 第三次尝试；此前两次亦失败）；资产主机不在可达白名单（github.com 可达、资产主机不可达）。`SHA256SUMS.txt` / `export_manifest.json` 由发布 runner 生成，与 tar.gz 自洽，但**不是独立复核**。独立复核需要：(a) GitHub-hosted runner 上的校验 job（可用 weights-release workflow 的最小只读变体，需并入 main），或 (b) Kaggle kernel（互联网可达 GitHub releases）。派发前需用户明确授权。
- .pt 阈值与元数据内嵌于 checkpoint（`threshold`/`n_physio`/`embed_dim`）；M0_RF 为 sklearn 模型，不在 prerelease 中。

### 10.2 230 条 Review_Result：AI 预填草案（非人工复核）
- review.tsv（Drive id `1cLGMOz7uugdRRjJj_02Q70a2mmu_bayn`）本身仍 **0/230**，未改动。
- 机器预分析识别：2 条全文无抗菌证据（AMP_009038 Leishmania 抗原虫 [CORE_GENERIC_UPGRADED]、AMP_007292 SK-MEL-28/G-361 抗肿瘤 [CORE_GENERIC_UPGRADED]）；AMP_000041 为奈瑟菌（Neisseria，MIC 20µg/ml）——**抗菌**，初版正则漏判。
- 草案已上传 Drive：`positive_core_qc_review_AI_PREFILL_DRAFT_2026-11-18_NOT_REVIEW.tsv`，id `1im2t9u2Q6rh3TBwXp9ce2fw4AgT-BpTI`。草案结论：228/230 CONFIRMED_CORE（157 条命名细菌靶点直接证据 + 71 条仅 Activity 字段断言，其中 67 条属 TARGET_UNKNOWN 分层），2/230 REJECT_NOT_ANTIBACTERIAL。每格均带“AI草案·待人工签核”字样；**不得冒充人工复核结论**。
- **154/230 行 Citation_Candidates 为空**：TSV 上无法做文献核实；凡写 CONFIRMED 的行（含 157 条命名靶点）都只是“记录内部一致”，不是文献复核。据此 v2 闸门不解除；正确路径＝人工逐行签核（可批量签“同意草案”），随后回填 master 并重新统计。

### 10.3 P1/P2 跨分布零 GPU 评估：本 sandbox 无法执行，需授权（选择其一）
所需输入（real7 的 split CSVs、ESM `features/*.npz`、positives FASTA）均**未持久化**到任何本 sandbox 可达位置：results 目录仅含 CSV/MD meta，原始 npz 留在 kernel workdir；positives 为私有 Drive 文件；Swiss-Prot 快照虽在库内，但精确划分受 mmseqs 多线程漂移影响无法 bit 复现；ESM 权重源 `dl.fbaipublicfiles.com` 与 prerelease 资产主机从 sandbox 均不可达（2026-11-18 实测）。
**选项 A（完整）**：新 Kaggle kernel（新 job id，例如 `dualamp-eval-frozen-real5-20261118`，T4 ≈ 60–90 min）：拉 prerelease tar.gz → 重建 composition-matched 数据集（同 manifest、seed 11、neg_mode composition-matched；与 real7 同构建口径，划分按概率漂移属可接受误差）→ 只编码 test + scrambled → 冻结 real5 的 15 个 .pt 打分 → 产出 matched-vs-standard 判阳率/AUROC 对照表 → 烧窗即焚。**这不是零 GPU**，需你明确授权。
**选项 B（零成本，已备证据）**：接受四次构建 scrambled 判阳率（n=500，12/12 > 0.5）作为组成偏好的直接证据：

| build | M1 | M2 | M3 | M1 mean_score | M2 | M3 |
|---|---|---|---|---|---|---|
| real5（standard） | .670 | .586 | .574 | .7635 | .6510 | .6429 |
| real6（repro） | .690 | .590 | .638 | .7633 | .6657 | .7258 |
| real6b（PYTHONHASHSEED=0） | .734 | .542 | .564 | .7748 | .6389 | .6321 |
| real7（composition-matched） | **.764** | **.656** | **.640** | .7721 | .6942 | .6829 |

结论不变：组成匹配重训没有缓解组成偏好；M1>M2≈M3 的偏好幅度在四次构建间稳定存在。须继续在 limitations 中原样披露（P1–P22 不软化）。

### 10.4 其他
- Gmail connector 2026-11-18 起返回 auth_error（连接失效）；230 条人工复核如有回执邮件，需你在 Arena 重新连接 Gmail 后我才能代查。
- v0.4 论文（附录 H 含 H.5 real6b / H.6 real7 / H.7 四构建一致性）已在 Drive：`论文_方法与结果_candidate_v1_v0.4.docx`，id `1QaIN9jlgR9Pvaefmh1WhYRyahRP_v7aX`。
- 本 sandbox 的 git 曾再次被重置到分支基点（工作树保留）；已复位到 `origin/arena/cac2205a-rea`（`654c97d`）并删除重复未跟踪副本；docx 未入库不变。

## 11. 2026-11-18 选项 A 执行结果（冻结 real5 × 跨分布负例；T4 kernel）

job `dualamp-eval-frozen-real5-20261118`（新 job id；Positives 窗#3 开窗→gdown 下载→烧窗，已复核仅 owner）：
- **run 44220409016 (failure)**：223s；kernel 侧失败（当时日志不可取）。
- **run 44221937068 (failure)**：热修复后（curl/UA 下载、verify 非致命化并拒绝错误权重、先建负例再校验、顶层 traceback 落盘 `_eval_failed.json`）重派（force=true）；实测根因＝kernel 环境缺 `biopython`（biophys.instability_index → Bio.SeqUtils，02:16:36~Z ImportError）；negatives 重建与权重 sha256 均已 PASS。运行后 burn 窗#2。
- **run 44222395769 (success, T4)**：dd5844b（biopython 兜底安装）后重派；positives sha256 `7b6ea1…768aa1d` 一致；negatives 重建双模式全部 PASS（standard 10000 / matched 10000，cutoff 0.0417674，eligible 350023，kept 105007）；**权重独立校验 ON KERNEL（经 GitHub releases 私有 prerelease 下载）→ sha256 PASS**（落盘 `summary.json.weights_verify=PASS`，`_eval_frozen_real5_done.json.stage=verify`）；结果见 `cloud-gpu/results/dualamp-eval-frozen-real5-20261118/`（`eval_frozen_real5_metrics.csv` / `summary.json` / `summary.md` / `recon_verification.txt`）。Positives 窗#3 已烧（仅 owner）。
  - 注：目录内 `_eval_failed.json` 为 run 44221937068 的失败落盘，**已被本次 done 取代**，保留不动（历史证据）。

### 11.1 指标（frozen real5 candidate_v1 pilot 权重 × 5 seeds；positives n=3579）

| model | neg set | FP rate @thr (mean±sd) | mean score | AUROC (mean±sd) | TPR |
|---|---|---|---|---|---|
| M1 | standard | 0.0059 ± 0.0011 | 0.0542 | 0.9918 ± 0.0011 | 0.908 |
| M1 | matched | 0.0071 ± 0.0012 | 0.0403 | 0.9938 ± 0.0009 | 0.908 |
| M1 | scrambled | 0.6136 ± 0.0275 | 0.7100 | 0.8074 ± 0.0046 | 0.908 |
| M2 | standard | 0.0020 ± 0.0019 | 0.0076 | 0.9974 ± 0.0002 | 0.971 |
| M2 | matched | 0.0063 ± 0.0027 | 0.0103 | 0.9982 ± 0.0001 | 0.971 |
| M2 | scrambled | 0.5560 ± 0.0495 | 0.5909 | 0.8591 ± 0.0084 | 0.971 |
| M3 | standard | 0.0010 ± 0.0005 | 0.0025 | 0.9974 ± 0.0002 | 0.974 |
| M3 | matched | 0.0050 ± 0.0020 | 0.0082 | 0.9982 ± 0.0002 | 0.974 |
| M3 | scrambled | 0.5344 ± 0.0390 | 0.5809 | 0.8565 ± 0.0084 | 0.974 |

- 冻结权重对 matched 与 standard 负例判别几乎无差异（AUROC 差 ≤0.003）→ **real7 重训未见收益的结论现由冻结权重交叉验证支持**。
- scrambled（评估期 shuffle，n=500，seed 20261118）：FP 53–61%；AUROC 降至 0.81–0.86 → 组成偏好在冻结权重上同样存在，与 §10.3 四构建表一致。
- **PILOT 口径**：candidate_v1 pilot 冻结评估，仅作 v2 定稿的内部参考；非 v2 正式模型；不可入论文结论；scrambled 为 composition-control，非 D-split 测试集。
- 权重 sha256 独立复核（on-kernel，非 sandbox）已关闭 §10.1 的验证缺口；M0_RF 仍不在 prerelease 中（sklearn joblib 在 kernel artifact，未入库）。

## 12. 2026-11-18 Review v2：文献核验回填（NOT_SIGNED，canonical 未动）

路径：`cloud-gpu/audits/2026-11-18-review-literature/`
- `positive_core_qc_review_LIT_pass_2026-11-18_NOT_SIGNED.tsv`：230 行回填版（3 列不变；Review_Result/Review_Reason 由文献核验填写）。
- `lit_pass_audit.tsv`：逐行 PMID→判定（on-topic / flagged / no-citation）。
- `build_lit_review.py`：复现脚本（规则见脚本头注释；核验通道＝eutils esummary db=pubmed + web_search，2026-11-18；title/journal/year 级，未重读 full text MIC 数据）。
结果：**CONFIRMED_CORE 73 行**（cited PMID 全部存在、主题为抗菌肽、且记录正文以 [Ref.NNN] 引用该 PMID 作为该条目的活动证据）；**UNSURE_NEEDS_LITERATURE 157 行**＝154 无引用（无具体文献支撑，按 2026-11-18 用户规则不得 CONFIRMED）+ AMP_001716（唯一引用 PMID 19028675 为抗氧化肽组学，非抗菌活性证据）+ AMP_000112（唯一引用 PMID 29266746 为猪蛔虫 CP-1 肽，与记录主条 cecropin P1 不匹配；记录正文无该 [Ref]）+ AMP_009038（记录仅声明抗原虫活性；抗菌核心资格待 user/PI 裁定，草案曾建议 REJECT_NOT_ANTIBACTERIAL）。
无 PMID 被标记为 CONFIRMED 而未实际经 eutils/web_search 核验存在且主题一致；0 行获 full-text MIC 复测。**canonical review.tsv（Drive id `1cLGMOz7uugdRRjJj_02Q70a2mmu_bayn`）保持 0/230 未动**；本件上传 Drive 供 user/PI 签核后再回填 master。v2 闸门＝仍关闭（需 157 行×签核或替换引用后方可按 runbook 定稿）。
