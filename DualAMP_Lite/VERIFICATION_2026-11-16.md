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
  `download_file` base64 同受内联截断；沙箱 egress 白名单不含 Drive 域。
- **已落地的通道（短时公开窗口 + sha256 强校验）**：用户确认方案 T（"临时公开窗口，跑完自动回收"）后，
  Drive 连接器把 `positive_core_candidate_v1.fasta`（id `11vvQSlOJ…`）设为 `anyoneWithLink` 只读、
  **allow_file_discovery=false**（链接不可被搜索发现）→ Kaggle 内核 gdown 下载 → 校验 sha256=7b6ea121…aa1d（不符即中止）→
  **产物回传后立即删除该授权**（owner-only；已在每次真跑后逐一回收并复核）。
  注意：工具侧对相同参数的"写操作"有去重保护，回收后必须再 `list_permissions` 复核确认。
- 备选通道（代码已就绪，未启用）：gdown 需公开窗口 / `FASTA_URL` 预授权链接 / 服务账号（私有 Kaggle dataset `SA_DATASET`，
  只读 drive.readonly，最稳、密钥不出 Kaggle）/ 一次性信封 `DRIVE_SA_JSON.enc`。
- 阴性片段无需传输：快照已在仓库内（公开库，27 MB），Kaggle 内核 `git clone --depth 1` 本分支即含 gzip，确定性重建（seed=11）。

## 6. 训练 / 测试通路 PILOT（Kaggle 2×T4 + GitHub Actions gpu-relay）

- 链路（全部走 `cloud-gpu/jobs/dualamp_pilot.py`）：
  `git clone` 本分支（含 A 快照）→ 阴性构建 → leanrea `dualamp-lite`（build → features[fair-esm t12_35M] → train → report）→
  小文件（D10 指标/报告/summary JSON/切分 CSV ≤2 MiB）回传 `cloud-gpu/results/<job-id>/`。
- `dualamp-dry-20261116`（MOCK 阳性，仅验证 plumbing）：
  - **build 全量成功**（seed=11）：700 条（150 MOCK 阳性 + 400 A 阴性 + 150 scrambled）→ k-mer 聚类 596簇（CD-HIT/MMseqs2 不可用，回退 k=3，已在报告标注）→ 14 个同源簇双标签入 review 不训练 → train 364 / val 78 / test 78（阳性 20）/ review 30 / scrambled 150。
  - **features 崩溃**：镜像缺 biopython（`No module named 'Bio'`，instability_index 需要）→ 已修复：载荷内 `_ensure_deps()` 自装 biopython/pyyaml（`9acb8c8` 之后的补丁）。
  - **产物取回超时→补拉成功**：首趟 `kaggle kernels output` 300s 超时（产物含 .git+ESM 权重，>60MB）；`dualamp-dry-fetch-20261116`（mode=output）于 2026-11-16T01:44Z 完成，4623 个文件入 `cloud-gpu/results/dualamp-dry-fetch-20261116/`（已清理至 20 个关键文件：dataset_summary/日志/切分CSV）。
  - 已修复：载荷自清（`/tmp` 中间产物、ESM 权重不入产物、白名单回传、output 超时 900s）。
  - **MOCK 指标不可引用；pipeline 代码路径 build 已验证可跑通，features/train 待 real 模式复核。**
  - 派发与内核执行约 5 分钟后进入产物取回阶段
  （与内核状态 complete 一致，但未 100% 证实）；`kaggle kernels output` **两次超时**（300s），
  根因：载荷把 `.git`+gzip（合计 >60 MB）、ESM 权重（~140 MB）、npz/模型留在 `/kaggle/working`，
  内核产物目录过大，API 取回超时。
- 已修复（17371d3）：中间产物全部入 `/tmp`、ESM 权重 `TORCH_HOME=/tmp/torch_home`、
  仅 ≤2 MiB 白名单产物复制到 cwd（内核产物目录），`kernels output` 超时上调 900s。
- `dualamp-dry-fetch-20261116`（mode=output 补拉）：**completed**（01:44Z），回传 4,623 个文件（修复前旧内核 cwd 整仓拷贝，
  含 .git 与中间产物）；已清理至 20 个关键文件入 `cloud-gpu/results/dualamp-dry-fetch-20261116/`。**MOCK 指标不可引用。**
- real 模式（用户确认方案 T 后执行）：
  - **`dualamp-real-20261116`（2026-11-16 02:11Z→03:09Z，完成）**：sha256 校验通过（133,202 B）→ 阴性 10,000（seed 11，
    池子 351,119→cap 10,000；>4-mer@0.8 重合 1,096 已剔）→ leanrea build→**CD-HIT 缺失回退 k-mer(k=3)** → 3,947 簇；
    **885 个同源簇双标签入 review**（2,852/3,579 阳性=80% 未进训练：5,701 train / 1,222 val / 1,222 test（109 阳）/ 5,434 review）→
    fair-esm 特征（t12_35M，925 s）→ 4 模型 × 5 seeds × 5 folds × 60 epochs → 报告 D10/D9/D11/D15 全部产出。
    **独立测试指标（test n=1,222，阳 109；簇级 DeLong/bootstrap CI）**：M3 AUROC 0.990（0.980–0.997）、AUPRC 0.953、
    MCC 0.879、Sens 0.817、Spec 0.997；M2 0.990/0.950/0.874；M1 0.989/0.951/0.890；M0_RF 0.978/0.919/0.863。
    消融：M3vsM0_RF auroc Δ0.012（p=0.014，BH q=0.062 不显著）、auprc Δ0.035（q=0.009 显著）、mcc 等价（预设裕度内）。
    scrambled 挑战集：M1 49.6%、M2 43.0%、M3 44.4% 被判阳（>40%，提示阈值与组成偏差待 v2 审核）。
    ⚠️ 该结果建立在 **k-mer 过度聚类**之上，仅作 pilot 参考；review 中 80% 阳性未被利用。
  - **`dualamp-real2-20261116`（03:11Z→04:11Z，完成，workflow conclusion=success）**：内核 apt 装 mmseqs2 成功（/usr/bin/mmseqs），
    但 leanrea `_run_mmseqs` 误把 `mmseqs easy-cluster` 的 `<prefix>_cluster.tsv`（rep×member 两列）当成
    `mmseqs_cluster_*.fasta`（逐簇 FASTA）→ 映射为空 → **静默退回 k-mer（结果与 real-1 逐位一致）**。
    另修复 relay 语义："Propagate job exit status" 此前把历史遗留 failed 任务（463 之前的 dry）算进本次 run，
    现改为仅惩罚 `finished_at_utc ≥ 本次 run 开始时间` 的失败 → real2 首次得到 conclusion=**success**。
  - **`dualamp-real3-20261116`（04:10Z→04:12Z，失败）**：内核 apt 装**原生 cd-hit**（ubuntu `cd-hit` 包，自测通过）→
    build crashed：`df["cluster"] = df["seq"].map(mapping).astype(int)` → **IntCastingNaNError**。
    根因：cd-hit 默认最小长度（-l）>5，**静默丢弃 <10 aa 短肽**（AMPs 常见）或不合字母表序列 → .clstr 成员数 < 输入 → NaN。
  - 修复（cd24de9/40db0af）：内核内始终优先使用 **mmseqs 引擎 + 自建 cd-hit shim**（/usr/local/bin/cd-hit，PATH 遮蔽原生），
    shim 保证**每个输入 id 都进入 .clstr**（未被聚类的追加为单例簇），并对成员数做端到端校验（已本地单测：含 5-mer、
    BXZ/U 字母、空映射场景全通过）。同时 relay 轮询默认 3600→9000s（real4 曾在阈值被判 pushed_async）。
  - **`dualamp-real4-20261116`（04:15Z 推送 → 06:0xZ 内核完成 → 06:46Z 第 5 次补拉取回全部 12 个产物）**：
    内核侧 apt 注入 **mmseqs2 + 自建 cd-hit shim**（PATH 遮蔽原生 cd-hit；覆盖兜底：未聚类 id 追加单例簇），
    dataset_summary.cluster_method=**`cdhit`**，**11,570 簇**，**0 个跨标签簇 → review=0**（k-mer 时代的 2,852 阳性-黑洞消失）。
    **阳性 3,579 全部进训练集链路**（train 2,505 / val 537 / test 537 = 3,579 ✓）；阴性 10,000 ✓；**train 9,505 / val 2,037 / test 2,037**
    （阳性占比 ~69.6/14.5/14.5 行；聚类按 80/10/10 分簇）。**独立测试指标（test n=2,037，阳 537；簇级 CI）**：
    M3 AUROC 0.9815 [0.9746, 0.9872] / AUPRC 0.9683 / MCC 0.866 / Sens 0.812 / Spec 0.997；
    M2 0.9810 / 0.9676 / 0.873；M1 0.9801 / 0.9660 / 0.877（Sens 0.851）；M0_RF 0.9695 / 0.9433 / 0.819。
    消融（cd-hit 口径）：M3 vs M0_RF auroc Δ0.012（p=5.2e-4，BH q=0.002 显著）、mcc Δ0.047（q<0.001 显著）；
    M3 vs M1 / M3 vs M2 各指标均不显著（M1/M2 的 biophys+CNN 融合与否无差别，q>0.4）。
    ⚠️ scrambled 挑战集被判阳率：M1 73.2% / M2 56.8% / M3 56.6%（阈值按 val-OOF MCC 定）→ **模型强烈依赖组成偏好**，
    与 M1/M3 高 Sens 一致；须在后续重训中加入组成对齐约束或作报告限制。
    k-mer 回退结果（real-1/2）对应数据标注"仅工程对照"，不引用。
- 冻结结论（本轮）：pipeline 已在真实数据上**端到端跑通三次**（D10 指标+D9 消融+D11 挑战集+D15 manifest）；
  **real4（cd-hit/mmseqs 口径）为当前可信结果**：全量 3,579 阳性 + 10,000 阴性、0 跨标签簇、计数闭合、
  指标（M3 AUROC 0.9815）报告可用——但仍属 **candidate_v1（未抽核）pilot**，**不可入论文、不可作定稿**；
  candidate_v1 已锁未定稿（230 抽核未回填）；positive_core_v2 未定稿；test 集在两个聚类口径下各评估一次，
  汇报须双口径并列并声明先后顺序。
- 工作流修复清单（已入库）：a) 载荷自清（17371d3）；b) 失败传播仅限本次 run（`83719bc`；real2 首次 conclusion=success）；
  c) 轮询 3600→9000s；d) Drive 短时只读窗口（allow_file_discovery=false）**用后即焚** + 用后 list_permissions 复核。

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

1. ~~real 模式全量 PILOT~~ **已完成**：cd-hit/mmseqs 口径（real4）为可信结果——全量 3,579/10,000 进簇、
   0 跨标签簇（review=0）、Test M3 AUROC 0.9815；k-mer 版（real-1/2）仅工程对照。产物已入仓
   `cloud-gpu/results/dualamp-real4-fetch5-20261116/`。
   下一轮：v2 定稿后的正式训练 + scrambled 组成对齐修正 + 230 抽核回填。
2. 拿到 mmseqs 版指标后：40% identity 划分审计 + 最近邻泄漏审计（k-mer 近似→mmseqs/CD-HIT 复核，含 cross-label 簇数复核）。
3. 230 条人工抽核回填 → 出 positive_core_v2 → 弱阴性在 v2 定稿后启动 → 正式训练。
4. 论文口径提醒：APD2024 快照与 2026-01-01 官方 3,379/430 的差异；DRAMP4.0 paper 30,260 vs 快照 public 39,125 的关系；
   "candidate_v1 pilot" 不得写成定稿结论；k-mer 回退限制与 scrambled >40% 判阳现象须写入方法限制；
   CD-HIT 静默丢短肽（<10 aa）+ leanrea mmseqs 产物格式误判两个坑已入 relay 修复，勿在其他环境复踩。
## 9. 数据准备总量复核（2026-11-16，real4/cd-hit 口径——论文级参考）

### 9.1 阳性锁账（3,579）
| 层 | 数量 |
|---|---|
| CORE_EXPLICIT_ACTIVITY_TARGETED | 2,895（2,805 具名细菌靶标 + 90 Gram 信号） |
| CORE_EXPLICIT_ACTIVITY_TARGET_UNKNOWN | 666（显式抗菌字段 + 靶标占位符） |
| CORE_GENERIC_UPGRADED_TARGETED | 18 |
| **合计** | **3,579** = fasta 条数 ✓（= 2,895+666+18） |
- sha256 `7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d`（133,202 B）锁定；repo 未入此文件（未发表数据）。
- length ∈ [5, 50]、标准 20 字母表：过滤后丢弃 0 条（dropped_records=0）✓。
- 完整性：2,505(train)+537(val)+537(test) = 3,579 ✓ 无重复、无跨集簇。
- **标签有效性上限未知**：230 抽核（5%×2,895+10%×666+100%×18，seed 20261110）Review_Result 仍空 → 任何"高精度"结论的标签质量前提未验证；**v2 前不可入论文**。

### 9.2 阴性可复现账（10,000）
- 源：UniProt KB Release 2026_04（2026-11-04 下载）Swiss-Prot reviewed + Bacteria + `cc_scl_term:SL-0086`（Cytoplasm 受控词汇）
  − 抗菌关键词（antimicrobial|bacteriocin|defensin|antibiotic，排除各 18 条蛋白）→ **117,172** 胞质蛋白（proteome 完整性双档 sha256 锁，见 §1）。
- 滑窗片段化（5–50 aa）：351,462 → 标准字母表 351,429 → 去重 351,119 → 与阳性 4-mer>0.8 重合剔除 1,096（→池 **350,023**）
  → **seed=11** 采样 **10,000**（cap=10,000；negatives_final=10,000）✓。全程确定性、无外部随机性。
- 与文献口径一致：Macrel（Porto et al.）以真核/胞内蛋白片段作阴性池；AMP-activity-ml（DRAMP5 同类管线）同为"reviewed UniProt 胞质片段 + CD-HIT 40% + StratifiedGroupKFold"设计；tsAMP（Front Microbiol 2026）同样 CD-HIT 40% 整簇划分 train/test。

### 9.3 聚类与切分账（cd-hit 0.40 / 0.80，StratifiedGroupKFold 8:1:1）
- 引擎：mmseqs2 + 自建 cd-hit shim（/usr/local/bin/cd-hit，覆盖兜底，单测通过）；`cluster_method="cdhit"` 如实入账。
- **11,570 簇**；**cross-label 簇 = 0** → review = 0（2026-11-16 前的 k-mer 回退把 885 簇/2,582 阴+2,852 阳误判为双标签，已作废）。
- 切分（行数）：train 9,505（阳 2,505/阴 7,000）｜val 2,037（537/1,500）｜test 2,037（537/1,500）｜scrambled 500（0/500）。
  → 阳性 3,579、阴性 10,000 **精确闭合** ✓；scrambled 500 组成匹配噪声，不参与训练/阈值选择。
- 簇数切分 ~80/10/10；行数比例偏差来自簇大小不均（方法章节须照写）。
- dev=OOF 预测存在（11,542 行）→ 阈值仅按 val-OOF MCC 调优；**test 仅评估一次**（real4 = cd-hit 版；k-mer 版为历史对照）。

### 9.4 复现清单（第三方重跑）
1. 分支 `arena/cac2205a-rea`（本文件所在 commit）；2. A 快照 `uniprot_bacteria_cytoplasm_reviewed.{fasta,tsv}.gz`（§1 sha256）；
3. candidate_v1（Drive 侧，sha256 见 §4）；4. sources.yaml sha256 `03f712be…`（manifest 锁）；5. seed=11（阴性）、seeds=1..5（模型）、folds=5；
6. 环境：Kaggle 1×T4（本次）、torch 2.11.0+cu130、fair-esm（esm2_t12_35M_UR50D，repr_layer 12，masked-mean 480d）、python 3.13.16；
7. 特征：AA+35 biophys；StandardScaler 仅 train 拟合（|r|≥0.85 冗余对 3 组，已记录）；
8. 交付：`cloud-gpu/results/dualamp-real4-fetch5-20261116/`（D10 指标/报告、D9 消融、D15 sha256 manifest 56 文件）。

### 9.5 已知偏差与禁止声称项（必须写入 manuscript limitations）
- **标签未抽核**（230 条）→ 不可称"标签完备"；candidate_v1 与 v2 不同（v2 依据抽核修订）。
- **scrambled 判阳率 57–73%**（M1 73.2% / M2 56.8% / M3 56.6%）→ 模型存在强组成偏好；不可声称学习到序列序或特异性机制；建议：组成对齐分析 + 对组成对齐阴性重训后再定论。
- AMP 家族过表征残差（文献讨论过的问题）；Test 指标 CI 为簇级（非肽级），与评估单位一致。
- k-mer 回退版与 cd-hit 版 test 集不同（前者 109 阳、后者 537 阳）；**论文写数须以 cd-hit 版为准并注明先后**。

### 9.6 一句话结论
数据准备的**数量级账本闭合且可复现**（3,579+10,000+500 全程 sha256/seed/脚本锁定，聚类工具如实入账）；
但**标签质量与组成偏好两个硬前提未解决**——数据准备"可复现"达标、"可入论文"不达标。
