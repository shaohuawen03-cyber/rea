# sORF → AMP 大规模筛选流水线（bacteria，>1 亿条）

> 本目录 = “代码上传”的中文交付物：脚本 + 命令 + 每一步 QC + 文献依据。
> 真相文件：本仓库 `cloud-gpu/results/dualamp-real4-fetch5-20261116/`（real4 训练产物）与
> `DualAMP_Lite/VERIFICATION_2026-11-16.md`（数据账本）。权重 `.pt` 不在 Git（见 §6）。

---

## 0. 三个直截了当的回答

1. **模型训好了吗？是。** real4（CD-HIT 整簇 0.40/0.80 口径）已完成：train/val/test = 9,505/2,037/2,037；
   5 seeds × 5 folds × 60 epochs。Test：M2 AUROC 0.981 / MCC 0.873；M1 0.980 / 0.877；M3 0.981 / 0.866；M0_RF 0.970 / —。
   阈值（dev-OOF MCC）：M1 0.685 / M2 0.885 / M3 0.810。
   ⚠️ 现有 test 已用于模型比较，**不可再称盲测**；v2 正式版须重新锁定独立测试集。
2. **代码在哪？** 推理代码 = 上游 `hukeying85-ux/leanrea` @`arena/4ecb70f1-leanrea`（dualamp-lite，含 M1/M2/M3 结构与训练/特征逻辑，checkpoint 保存/读取见 `dualamp_lite/train.py`）；本仓库 `rea` 的 `cloud-gpu/jobs/dualamp_pilot.py` 负责 Kaggle 派发；**本目录是面向 >1 亿条 sORF 的可执行漏斗**（00–06），所有脚本均通过语法/小样本冒烟测试。
3. **推荐什么口径？**（§2，按文献）——Macrel 优先高特异性、M2 主评分、M1 高召回辅助、
   5–50 aa 为训练覆盖范围（**不先砍 5–9 aa**）、先精确去重再 MMseqs2 聚类、并集 + Tier1/2/3 分级、
   表达证据留到后续层。Ma 2022（Nat Biotechnol）和 Torres 2024（Cell）的共同教训：**先聚类家族→只跑代表→再叠证据**。

---

## 1. 目录结构

```text
DualAMP_Lite/mining/
├── README.md                      # 本文
├── scripts/
│   ├── 00_standardize_faa.py     # 阶段1  标准化+精确去重（sqlite 流式，>1亿可处理）
│   ├── 01_cluster_reps.sh        # 阶段2  MMseqs2 0.90/0.90 家族聚类→代表序列
│   ├── 02_macrel_predict.sh      # 阶段3a Macrel 独立筛选（并行路 A，不互斥）
│   ├── 03_train_export.sh        # 阶段3b 本机重训并导出 M1/M2 .pt（将来 v2 用）
│   ├── 04_batch_score.py         # 阶段4  ESM-2 批量嵌入→M1/M2 集成均值打分（分片/续跑）
│   ├── 05_join_macrel.py         # 阶段5  合并 Macrel + M1/M2 → Tier1/2/3 + 理化列
│   └── 06_rule_b_ripp.sh         # 路线B  细菌素/RiPP 隔离流（antiSMASH + BAGEL4）
├── weights/                      # real4 .pt（Git 忽略；见 §6）+ export_manifest.json(sha256)
└── out/                          # 运行时产物（Git 忽略）
```

输入约定：**sORF 已被上游（如 getorf/Prodigal-meta 的 micro-ORF 模式）预测为蛋白 FASTA**（FAA）。
若只有 contigs，先用 getorf/metaprodigal 预测 ORF 再进入阶段 1（本流水线不替你做 ORF caller，
因为 ORF 召回率决定漏斗上限，应单独审计）。

---

## 2. 推荐口径与文献依据（你问的“推荐什么”）

| 决策 | 推荐 | 依据 |
|---|---|---|
| 长度 | **5–50 aa（与真实训练集一致，`dataset_dataset_summary.json: min_len=5 max_len=50`）；5–9 aa 标“探索层”单列报数，不参与结论性排序**；不要为了“对齐 Macrel 10–100”而重切训练集 | DualAMP real4 训练范围；Macrel 仅收 10–100 aa SMORFs |
| M 模型角色 | **M2 主评分（test MCC .873）+ M1 高召回辅助（test MCC .877、高灵敏）**；M3 不主用（scrambled 判阳 56.6%，且依赖 biophys scaler） | 本仓库 real4 结果 |
| Macrel 角色 | **独立复核、并集**（不要求三模型全阳）；特异性优先、gut AMP 流行率 0.1–1.65% | Santos-Júnior 2020 PeerJ 8:e10555 |
| 去重顺序 | **先精确去重（本目录 00）→ 再 MMseqs2 easy-cluster 0.90/0.90（仅降冗余提速，不做 40% 同源切分）→ 只对代表序列跑 ESM/Macrel** | Ma 2022（先聚类家族再筛选）；>1亿规模算力铁律 |
| Tier 分级 | Tier1=DualAMP 阳 且 Macrel 阳（或 M1∧M2 双阳）；Tier2=单模型高分（M2≥0.95 或 M1≥0.90+M2≥0.75）；Tier3=其余/组成驱动（scrambled 判阳率 M1 73.2%/M2 56.8%） | 本文 §4 QC 与 real4 scrambled 结果 |
| 证据分层 | 预测→（后续）metatranscriptomic/metaproteomic 回帖 + 家族覆盖均衡抽样 → 合成验证 | Torres 2024（444,054 smORF 家族→323 SEPs；78 合成、70.5% 活性）；Ma 2022（2,349→216→181，>83% 活性，Nat Biotechnol 2022;40:921–931, DOI 10.1038/s41587-022-01226-0）；global microbiome ML，86 万+候选（Santos-Júnior 2024，Cell） |
| 路线 B（RiPP/细菌素） | **隔离流水线**：antiSMASH + BAGEL4 → leader 切割 → core 肽单独统计，**不得送进线性肽 Tier 统计** | 本文 06_rule_b_ripp.sh |
| 秩序统计 | 输出 4 张表：`all_sORF_master.tsv` / `DualAMP_prediction.tsv` / `Macrel_prediction.tsv` / `AMP_candidate_priority.tsv` | 与 AI 建议一致 |

**“先 pilot 再全量”**：先跑 `--limit 100000`（或 1e6）标定各阶段通过率、速度与 Tier 比例；
外推后再决定全量规模。**任何 1e8 级以上预测结果在 pilot 标定前=探索性，不得作为论文终点。**

---

## 3. 五阶段运行手册（命令 + QC + 预期漏斗）

> 环境：WSL2/Linux · Python ≥3.10 · GNU awk · `mmseqs2`（`sudo apt-get install -y mmseqs2`，或 conda）·
> `pip install macrel==1.6.1` · `pip install fair-esm torch numpy scipy`（CUDA 卡建议，≥1 张 T4/A10）

### 阶段 0｜输入登记（QC -1）
```bash
sha256sum sorf_proteins.faa | tee out/00_input.sha256
wc -l out/… && grep -c '^>' sorf_proteins.faa | tee out/00_n_records
# 来源映射：记录 contig_id → sample_id → assembly 的对应表（本流水线假设你已有该表，命名为 sample_map.tsv）
```
QC：记录数与上游一致；FASTA 头部不重复；全文件 sha256 落盘。**中止条件：数量对不上或 sha256 已知值不符。**

### 阶段 1｜标准化 + 精确去重
```bash
python3 scripts/00_standardize_faa.py --input sorf_proteins.faa --out-dir out/stage1
# pilot：加 --limit 100000
```
产物：`out/stage1/all_sORF_master.tsv`（seq,length,count,first_id）、`unique.faa`、`qc_standardize.json`、`master.sqlite`
QC：长度 5–50；标准 20 字母；单残基占比≤80%；`dup_reduction_ratio` 0.2–0.95（≈0=已去重数据；>0.95=输入异常）。
预期（外推，须 pilot 标定）：通过率 ≈ 90–100%，去重后 ↓ 10–50%。

### 阶段 2｜家族聚类 → 代表序列
```bash
scripts/01_cluster_reps.sh out/stage1/unique.faa out/stage2 16
```
产物：`out/stage2/reps.faa`、`mm_cluster.tsv`、`mm_rep_seq.fasta→(=reps.faa)`
QC：`reps/unique ∈ [0.3, 0.9]`；聚类前后序列总质量（length 分布）应无突变。**中止条件：mmseqs 报错或 reps>unique。**

### 阶段 3a｜Macrel 独立筛选（并行路 A）
```bash
scripts/02_macrel_predict.sh out/stage2/reps.faa out/stage3_macrel
```
产物：Macrel 目录（`macrel_.../amp_prediction...tsv`，含 peptide/prediction/hemolytic）
QC：表头含 peptide+prediction 列；AMP 阳率异常高（>5%）提示输入同源块或未排除已知 AMP；无输出表则先 `macrel --help` 核对 CLI（大版本间参数有差异），再用 `MACREL_ARGS=…` 覆盖。
预期：AMP 率 0.1–3%（gut 宏基因组 0.1–1.65%，Santos-Júnior 2020）。

### 阶段 3b｜权重获取（二选一）
**A. 直接用 real4 已训权重（推荐）**：已由本轮 `gpu-relay` fetch6 任务取回（见 §6），`weights/` 内 10 个 `.pt`（M1/M2 × seed 1..5）+ `export_manifest.json`(sha256)。
**B. 本机重训导出**（用于将来 positive_core_v2 定稿训练）：
```bash
scripts/03_train_export.sh /path/to/candidate_v1.fasta model_export
```
⚠️ 本机 GPU 约 1–2 h（60ep×5seed×5fold）；阳性 fasta sha256 必须等于 `7b6ea121…`，否则中止；negatives 自动按 rea seed-11/A 快照/cap 10,000 重建；**不可在 Kaggle 自动派发前不告知耗时——本命令只在本地交互式执行。**

### 阶段 4｜ESM-2 → M1/M2 批量打分（分片可续跑）
```bash
for k in $(seq 0 9); do
  python3 scripts/04_batch_score.py --input out/stage2/reps.faa \
    --export-dir weights --leanrea-dir .external/leanrea \
    --out out/stage4/pred_shard_${k}.tsv --shard $k --num-shards 10 \
    --batch-size 128 --device cuda &
done; wait
cat out/stage4/pred_shard_*.tsv > out/stage4/DualAMP_prediction.tsv   # 去重表头：先处理第一行
```
续跑：加 `--resume`（按已有行数跳过）。吞吐参考（real4，fair-esm fp16，9505 条/13.2 s on 2×T4 ≈ 720 seq/s）：
> **>1亿条 ESM 全量打分是铁律禁忌——只跑 reps.faa**。1e8 → 去重/聚类后按 0.4–0.7 计，仍 4e7–7e7；单 T4 需数天，建议 ≥4×A10/A100 或先转 pilot 标定；`--limit` 可控规模。

QC：每分片输出 `seqs_per_sec`；shard 合计行数 == reps 数；`m2_prob` 分布应双峰；**若全 0.99（饱和）或全 <0.5，先查权重路径与 fp32/fp16。**

### 阶段 5｜合并 + Tier 分级 + 理化列
```bash
python3 scripts/05_join_macrel.py --pred out/stage4/pred_shard_*.tsv \
  --macrel-out out/stage3_macrel --out out/stage5/AMP_candidate_priority.tsv \
  --cluster-tsv out/stage2/mm_cluster.tsv --unique-faa out/stage1/unique.faa \
  --m1-thr 0.685 --m2-thr 0.885
```
产物：`AMP_candidate_priority.tsv`（Sequence,Length,M1_Score,M2_Score,Macrel_AMP,Macrel_Hemolytic,Net_Charge,GRAVY,pI,Family_Size,Tier）+ `_qc.json`
QC：Tier1 占比 0.1–3%（>5% 警惕）；`macrel_matched` ≥ 90%；Net_Charge 分布呈正电性偏移（AMP 典型 +2~+7）。

### 路线 B｜RiPP/细菌素（隔离，不进 Tier 统计）
```bash
scripts/06_rule_b_ripp.sh contigs.fna out/rule_b
```

---

## 4. 每一步 QC 总账（fail-fast）

| 步骤 | 产物 | 必查 QC | 异常即中止 |
|---|---|---|---|
| 0 | sample_map.tsv, sha256 | 总数/长度/头部唯一 | 数量不符或 sha256 变化 |
| 1 | unique.faa, master.tsv | dup_ratio∈(0.2,0.95)；长度合规率 | 解析崩溃/空产物 |
| 2 | reps.faa, cluster.tsv | reps/unique∈[0.3,0.9] | mmseqs 失败 |
| 3a | Macrel tsv | 列齐；AMP 率 0.05%–5% | 表缺列/全阳/全阴 |
| 3b | weights/*.pt, manifest | sha256 与 manifest 一致（10 个） | 校验失败 |
| 4 | pred_shard_*.tsv | 合计行数 == reps 数；分数分布合理 | checkpoint 缺失/NaN |
| 5 | AMP_candidate_priority.tsv | Tier1≤5%；macrel_matched≥90% | 理化列全空 |

全程贯穿：**4 张最终表** `all_sORF_master.tsv / DualAMP_prediction.tsv / Macrel_prediction.tsv / AMP_candidate_priority.tsv` + 每一步 `qc_*.json`。

---

## 5. 硬性禁忌

- ❌ 先 ESM-2 全量打分再去重。
- ❌ 把 RiPP/细菌素/lasso/翻译后修饰 core 肽送进线性 AMP 的 Tier 统计。
- ❌ 把本 PILOT 权重 + real4 测试结果写进论文（candidate_v1≠v2；test 已泄漏给模型选择）。
- ❌ 原始 sORF FASTA、>2 MiB 文件进 Git；权重走 Release/HF（§6）。
- ❌ 跳过任何 `qc_*.json` 直接进入下一阶段。

## 6. 权重（.pt）交付方式

- real4 kernel 输出里**确实有** `.pt`（`train.py: torch.save(paths.model/...)`），但 rea 的 fetch 管线
  `gpu_ctl.py:_copy_small_outputs` 显式排除 `.pt`（防大文件入 Git）——这就是之前 fetch5 只有 12 个 md/csv/json 的原因。
- 本轮修复：`_copy_small_outputs` 放宽 `.pt` + `cloud-gpu/results/**/*.pt` 加入 `.gitignore`
  → 派发 `dualamp-real4-fetch6`（仅 output 模式，**不重训、不改任何数据**）→ 用 `gh run download` 取回
  → 权重落到 `DualAMP_Lite/mining/weights/`（git-ignored）→ 打包 `dualamp-real4-pilot-weights.tar.gz`
  → 发布 **GitHub Release**（本仓库，tag `dualamp-real4-pilot`）。
- HuggingFace：你之前说可以给账号——给我一个**仅针对一个目标仓库的 write token**（例如 `…/dualamp-real4-pilot`），
  我即 `huggingface-cli upload`。（token 不会写入 Git；仅本次会话内使用。）

## 7. 文献（本 README 的事实源）

1. Ma Y, Guo Z, Xia B, et al. Identification of antimicrobial peptides from the human gut microbiome using deep learning. *Nat Biotechnol* 2022;40:921–931. DOI:10.1038/s41587-022-01226-0 — 2,349→216→181（>83% 活性）。
2. Torres MDT, Brooks EF, Cesaro A, et al. Mining human microbiomes reveals an untapped source of peptide antibiotics. *Cell* 2024;187:5453–5467.e15. DOI:10.1016/j.cell.2024.07.027 — 1,773 人肠道宏基因组；444,054 smORF 家族；323 SEPs；78 合成、70.5% 活性；prevotellin-2。
3. Santos-Júnior CD, Pan S, Zhao X, Coelho LP. Macrel: antimicrobial peptide screening in genomes and metagenomes. *PeerJ* 2020;8:e10555. DOI:10.7717/peerj.10555 — 特异性导向；gut AMP 流行率 0.1–1.65%。
4. Discovery of antimicrobial peptides in the global microbiome with machine learning. *Cell* 2024（s0092-8674(24)00522-1；≈86 万候选；AMPSphere / https://zenodo.org/records/6511404）。
5. 长度/聚类口径：真实训练范围 5–50（本仓库 `dataset_dataset_summary.json`）；CD-HIT 整簇 40%/0.80 的数据集切分口径见 `DualAMP_Lite/VERIFICATION_2026-11-16.md`。
