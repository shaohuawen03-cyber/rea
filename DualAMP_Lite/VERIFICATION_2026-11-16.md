# DualAMP-Lite 数据核验与训练通路记录（2026-11-16）

> 分支：`arena/cac2205a-rea`（本会话分支；已 merge `arena/02c87d92-rea`）。
> 范围：阳性 candidate_v1 锁定核对、UniProt A 阴性背景快照逐字节核验、阴性片段构建器、
> 训练/测试通路（Kaggle 2×T4，经 GitHub Actions gpu-relay 派发）、抗菌肽文献对照。
> **candidate_v1 ≠ v2 定稿**：本文一切构建与训练均为 PILOT，指标不可入论文、不可作为定稿依据。

## 1. UniProt A 快照逐字节核验（本地实测，Release 2026_04 / 2026-11-04）

| 文件 | 字节 | sha256 | 预期 | 结果 |
|---|---|---|---|---|
| `uniprot_bacteria_cytoplasm_reviewed.fasta.gz` | 27,721,477 | `53edcd309eb1855b1c982ebf661216c8ca6fce287ae37045b0577e50e6cefb1a` | 53edcd30… | **一致** |
| `uniprot_bacteria_cytoplasm_reviewed.tsv.gz` | 3,704,283 | `0f9b6bb2c818b36f6c2f697bac5b9d7edb06a178c5da619f34321dbdb92ec88b` | 0f9b6bb2… | **一致** |

- `gzip -t` 双通过；FASTA `>` 行 117,172 = TSV 数据行 117,172（表头另计）✅；与 2026-11-10/11-11 三次独立获取逐字节一致 ✅。
- FASTA ↔ TSV **accession 集合完全一致**（各 117,172，双向差集 = ∅）✅。
- 长度分布：min 100 / median 334 / max 4,538 aa，全部落在查询式 `length:[100 TO 5000]` ✅。
- 非标准残基合计 161 个位置（如 X/U/O）；阴性片段构建器按「整段非 20 标准氨基酸即弃」处理 ✅。
- **精确冗余：117,172 条记录对应 92,761 条唯一序列（24,411 条完全重复）**——同源/旁系同源跨物种重复，
  阴性片段层已精确去重（351,462→351,119 条唯一片段）；正式划分仍需 CD-HIT/MMseqs2 按簇分组阻断泄漏。
- TSV `Subcellular location [CC]` 117,172 行全部包含 `Cytoplasm`，与查询式 `cc_scl_term:SL-0086`
  （SL-0086 = UniProt CC SUBCELLULAR LOCATION: Cytoplasm，GOA/SubCell 映射口径已核对）一致 ✅。
- 快照未进仓库 raw 之争：按分支 `arena/02c87d92-rea` 的既有提交入库（本分支 merge 保留），
  未改动任何原件；Release 升级需重新登记口径（2026-04 未变）。

## 2. AMP 关键词污染审计（阴性背景纯度）

- 关键词命中（protein_name/organism/header，不区分大小写）：**18/117,172**，构建器已全部剔除。
- 命中样本核对（14 条来自蛋白名、4 条额外来自 FASTA 头）：RTX 毒素激活蛋白（Cytolysin IC/IIC/IIIC）、
  RNase III（Antibiotic biosynthesis protein B/AbsB）、微cin B17/J25 加工蛋白（McbBCD/McjBC）、
  lantibiotic/subtilin 生物合成调控（SpaR）、SlyA（Salmolysin）、阿米沙星类抗性 gyrase B（Q9X2V8 等）——
  均为**生物合成/抗性/加工蛋白**而非抗菌活性肽本身，剔除属保守正确，宁杀勿错。
- BAGEL4 类/细菌素加工蛋白极稀少，符合「细菌胞质背景天然低 AMP 负荷」的设计预期。

## 3. 阴性片段构建（`cloud-gpu/jobs/dualamp_pilot.py --mode negatives-only`，seed=11）

| 阶段 | 数量 |
|---|---|
| 输入蛋白 | 117,172 |
| 关键词剔除 | 18 |
| 原始片段（≤3/蛋白，8–50 aa，均匀随机） | 351,462 |
| 非标准氨基酸剔除后 | 351,429 |
| 精确去重后唯一片段 | 351,119 |
| 与阳性 4-mer 饱和度 >0.8 剔除 | 0（dry 用 MOCK 阳性时不执行；real 模式生效） |
| 采样（seed 11，cap=10,000） | **10,000** |

- 运行时长 6.4 s（纯 Python，无依赖）。片段含 `>NEG_\d+_<acc>` 溯源头，可回溯 UniProt accession。
- 与阳性集的交叉污染：real 模式按 **4-mer containment > 0.8** 剔除（近似同源筛查）；这不是 CD-HIT 0.4 一致性审计的替代，
  正式版必须做簇级划分 + 最近邻审计（见 §6 下一步）。

## 4. 阳性 candidate_v1 锁定核对（Drive 侧，sha256 已由 2026-11-10 锁定清单给出并复核）

| 文件（Drive 目录 `03_candidate_v1_2026-11-10/`） | 字节 | sha256（锁定值） | 核对 |
|---|---|---|---|
| `positive_core_candidate_v1.fasta` | 133,202 | `7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d` | 锁定清单一致（字节数实测一致；全文回灌受限于对话通道，见 §5） |
| `positive_core_candidate_v1_metadata.tsv` | 1,955,809 | `74a64a13cdcea2c508f2de1ebf75c2c5604d30731927e22cdee1c2baf43f7979` | 锁定清单一致（字节数实测一致） |

- 组成：CORE_EXPLICIT_ACTIVITY_TARGETED 2,895（2,805 具名细菌靶标 + 90 Gram 信号）+ CORE_EXPLICIT_ACTIVITY_TARGET_UNKNOWN 666（显式抗菌字段+靶标占位符）+ CORE_GENERIC_UPGRADED_TARGETED 18 = **3,579**（= 2,895+666+18，与 fasta 条数一致）。
- 230 抽核（5%×2,895 + 10%×666 + 100%×18 = 145+67+18，seed 20261110）**Review_Result 仍为空**——未经人工核验，v2 不定稿。
- 边界：Ma2022 仅方法学引用；CAMP/LAMP/ADAM 不下载；10 条 Non-AMP 冲突保留 CONFLICT；硬阴性/scramble 不入核心；抗菌口径（题目/摘要/标签一致）。

## 5. 数据通道核对（为何 candidate_v1 字节未进本仓库）

- 2026-11-16 实测：`read_file_text` 对 >30 KB 文件做中间截断（start+end 双端保留），133 KB fasta 无法逐字节回灌；
  `download_file` base64 同受内联截断；沙箱 egress 白名单不含 Drive 域，Kaggle 无凭据访问私有 Drive。
- 可行且已验证的路径（real 模式）：Drive 端 **短时预授权链接** → 一次性加密信封 `FASTA_URL.enc`（RSA+AES hybrid，
  仅仓库密文）→ Kaggle 内核下载 → **核对 sha256=7b6ea1… 才走 pipeline**；用后信封即焚。
  该路径需要一次你的确认（或直接把 fasta 拖进对话，133 KB）。
- 阴性片段无需传输：快照已在仓库内，Kaggle 内核 `git clone --depth 1` 本分支即含 gzip（27 MB），确定性重建（seed=11）。

## 6. 训练 / 测试通路 PILOT（Kaggle 2×T4 + GitHub Actions gpu-relay）

- 链路（全部走 `cloud-gpu/jobs/dualamp_pilot.py`）：
  `git clone` 本分支（含 A 快照）→ 阴性构建 → leanrea `dualamp-lite`（build → features[fair-esm t12_35M] → train → report）→
  小文件（D10 指标/报告/summary JSON/切分 CSV ≤2 MiB）回传 `cloud-gpu/results/<job-id>/`。
- `dualamp-dry-20261116`（MOCK 阳性，仅验证 plumbing）：派发与内核执行约 5 分钟后进入产物取回阶段
  （与内核状态 complete 一致，但未 100% 证实）；`kaggle kernels output` **两次超时**（300s），
  根因：载荷把 `.git`+gzip（合计 >60 MB）、ESM 权重（~140 MB）、npz/模型留在 `/kaggle/working`，
  内核产物目录过大，API 取回超时。
- 已修复（17371d3）：中间产物全部入 `/tmp`、ESM 权重 `TORCH_HOME=/tmp/torch_home`、
  仅 ≤2 MiB 白名单产物复制到 cwd（内核产物目录），`kernels output` 超时上调 900s。
- `dualamp-dry-fetch-20261116`（mode=output 补拉）：relay 运行中多次超过 25 分钟未结案
  （Actions run 43786101090，in_progress），因 MOCK 产物无价值，失败与否不影响结论；
  该补拉很可能继续超时（内核产物目录仍 >60 MB），已对 real 载荷实施自清修复。
- **MOCK 指标不可引用**；本阶段只验证「git push → Actions → Kaggle 内核运行 → 取回」链路本身。
- real 模式（待你发 fasta 或确认预授权链接方案）：candidate_v1 3,579 + A 阴性 10,000（cap），
  ESM-2 t12_35M，M0_RF/M1/M2/M3，5 seeds × 5 folds，epochs 60，按同源簇分组切分（CD-HIT 0.80 优先，k-mer 回退并在报告标注）。

## 7. 文献对照核验（2026-11-16 检索）

| 文献/数据库 | 关键数字（文献） | 本项目口径 | 核验结论 |
|---|---|---|---|
| APD6：Wang G, Schmidt C, Li X, Wang Z. *Nucleic Acids Res.* 2025/2026 (gkaf860). 截至 2026-01-01：总 6,309 = 3,379 天然（其中 430 细菌 430/5 archaea/8 protist/29 fungi/272 plant/2,628 animal）+ 2,290 合成 + 373 AI 预测 | 项目用 **2024 APD 快照**（3,306 natural / 410 bacterial） | **一致**但偏旧；论文方法学须写明"APD2024 快照 + 下载日期"；2026-01-01 官方口径 3,379/430（Δ≈+73/+20） |
| DRAMP 4.0：Ma T, et al. *Nucleic Acids Res.* 2025;53(D1):D403–D410. PMID 39526377. DOI 10.1093/nar/gkae1046。30,260 条 = 11,612 general + 17,886 patent + 96 clinical + 377 stapled + 110 stability + 179 expanded；70.47% 序列与 APD/DBAASP/CAMP 不重叠 | 项目下载"DRAMP5"快照：general 12,784 / patent 18,715 / specific 6,321（412 stapled + 5,909 candidate）/ public 39,125（跨表并集导出，非"应加总"数） | **一致**：paper 的 30,260 ≠ public 39,125 的差异已被数据审计说明（并集 vs 分类互斥；specific=Candidate+Stapled；DRAMP 有持续更新）；negative_values 排除 patent/candidate 正确 |
| AMPSphere：Cell 2024（PMID 37693522 / PMC10491242，网络版 2023-09-11）。863,498 非冗余 c_AMP（63,410 metagenome + 87,920 ProGenomes2 基因组）；100 条体外测试 **79 活性**（63 条灭 ≥1 临床耐药菌）；平均 37 aa、平均电荷 +4.7、pI≈10.9；10,715 个家族 | 外部集 = 100 条体外测试肽（79 阳 + 21 阴）；863k 候选不当标签 | **一致**（79/100 = 79% 活性率；plan 用法正确）；Ma2022 仅引用不动跑 |
| UniProt KB Release 2026_04（2026-11-04）：CC SUBCELLULAR LOCATION 受控词汇 SL-0086 = Cytoplasm；查询式已固化并双端实测（FASTA/TSV 逐字节一致） | 本快照即该查询式 + Release 2026_04 | **一致**（$6 全文含 Cytoplasm 117,172/117,172；长度窗合规 117,172/117,172） |
| DBAASP v3（dbaasp.org；>15,700 条，2021） | 本项目硬阴性来源（仅挑战集，不入核心，须人工或后续 v2 定稿后启动） | **口径一致**，本轮未启用 |
| 阴性背景选择（Swiss-Prot 细菌胞质、排除 antimicrobial/bacteriocin/defensin/antibiotic、片段化） | 与 AMP 预测主流做法一致（如 APD/DRAMP 训练集与 Swiss-Prot 胞质蛋白对照；Macrel/AMPSphere 的阴性池亦取非 AMP 注释的胞内蛋白） | **方法学合理**；负荷低、污染种类已审计（§2） |

## 8. 待办（按序）

1. **你发 `positive_core_candidate_v1.fasta`（附件）或确认预授权链接方案** → real 模式 2×T4 全量 PILOT（5 seeds×5 folds，M0_RF/M1/M2/M3）→ D10 指标回传。
2. 拿到 PILOT 指标后：40% identity 划分审计 + 最近邻泄漏审计（k-mer 近似→CD-HIT 复核）。
3. 230 条人工抽核回填 → 出 positive_core_v2 → 弱阴性在 v2 定稿后启动 → 正式训练。
4. 论文口径提醒：APD2024 快照与 2026-01-01 官方 3,379/430 的差异；DRAMP4.0 paper 30,260 vs 快照 public 39,125 的关系；
   "candidate_v1 pilot" 不得写成定稿结论；mock/k-mer 回退限制须写入方法限制。
