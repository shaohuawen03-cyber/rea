# sORF → AMP 大规模筛选流水线（bacteria，>1 亿条）

> 本目录 = “代码上传”的中文交付物：脚本 + 命令 + 每一步 QC + 文献依据。
> 2026-11-16 第 2 版：吸收一次外部 AI 审阅意见并全部落地（见文末「审阅意见处置」）。
> 真相文件：`cloud-gpu/results/dualamp-real4-fetch5-20261116/`（real4 训练产物）、
> `DualAMP_Lite/VERIFICATION_2026-11-16.md`（数据账本 + real4 聚类引擎更正，§10）。

---

## 0. 三个直截了当的回答（含权重坏消息）

1. **模型训好了吗？是。** real4（**MMseqs2-shim 0.40/0.80 整簇**，cluster 11,570）已完成：train/val/test = 9,505/2,037/2,037；
   M1 AUROC .9801/MCC .8769、M2 .9810/.8728、M3 .9815/.8664、M0_RF .9695/.8190。阈值（dev-OOF MCC）：M1 0.685 / M2 0.885 / M3 0.810。
   ⚠️ test 已用于模型比较，**不可再称盲测**；v2 正式版须重新锁定独立测试集。
2. **代码在哪？见 §7 链接。**推理代码 = 上游 `hukeying85-ux/leanrea` @`arena/4ecb70f1-leanrea`（dualamp-lite）；
   本目录是 >1 亿条 sORF 的可执行漏斗（00/00b/01–06），脚本全部通过语法与 mini 冒烟测试。
   **2026-11-16 用户指示：先训 real5（candidate_v1 口径，导出 .pt）恢复流水线可用性——产权重标注 `candidate_v1_pilot_weights`（仅调试/初筛，非 v2 正式模型）；v2 回填 Review_Result 后再训。**
3. **权重（.pt）拿不到。** Kaggle 内核工作区是 `/tmp/dualamp-pilot`，不在 `/kaggle/working`；
   `train.py` 把 .pt 写进 `<workdir>/model/`，内核结束即销毁。本轮 fetch6（仅 output 模式）复验 kernel 输出无任何 `.pt`。
   补救已就绪：pilot 已补丁（real5 起导出 .pt ≤2 MiB 到 artifact；`gpu_ctl` 放宽 `.pt`；`.gitignore` 排除入库）。
   **按本轮共识：real5 默认等 `positive_core_v2` 锁定后重训**；如果 v2 未回填但你急需测速，可先导
   `candidate_v1_pilot_weights`（仅流程调试，不能作为 v2 正式模型发布/论文披露）。

---

## 1. 目录结构（新）

```text
DualAMP_Lite/mining/
├── README.md                      # 本文
├── scripts/
│   ├── 00_standardize_faa.py     # 阶段1  标准化+精确去重（5–100 aa；多文件/manifest；来源映射分桶；低复杂度标记；流式）
│   ├── 00b_make_pilot.py         # 阶段0.5 按样本分层 reservoir 抽样（非“取前 N 条”）
│   ├── 01_cluster_reps.sh        # 阶段2  MMseqs2 0.90/0.90 家族聚类→代表；cluster 映射闭合 QC（ENGINE=linclust 可选测速）
│   ├── 02_macrel_predict.sh      # 阶段3a Macrel 独立筛选（并行路 A；macrel v1.6.1 实测）
│   ├── 03_train_export.sh        # 阶段3b 本机/GPU 重训并导出 M1/M2 .pt（v2 锁 positive_core_v2；POSITIVES_SHA256 环境变量锁定）
│   ├── 04_batch_score.py         # 阶段4  ESM-2 批量嵌入→M1/M2 5-seed 集成均值打分（分片/续跑；meta 绑定输入+权重 sha256）
│   ├── 05_join_macrel.py         # 阶段5  合并 Macrel + M1/M2 → Tier1/2/3 + Layer 分层 + 理化列（优先 leanrea biophys）
│   └── 06_rule_b_ripp.sh         # 路线B  antiSMASH+BAGEL4 scaffold（RiPP_workflow_scaffold，未完整验证，禁止与线性肽混用）
├── weights/                      # real/v2 .pt（Git 忽略；export_manifest.json + sha256）
└── out/                          # 运行时产物（Git 忽略）
```

输入约定：sORF 已被上游（getorf / Prodigal-meta 的 micro-ORF 模式等）预测为蛋白 FASTA；ORF caller 召回率决定漏斗上限，单独审计。

> **最常犯的两个错**：① 把 >1 亿条全部送 ESM-2 打分（必须先去重/聚类，只跑代表序列）；
> ② 把整份 FASTA 的“前 100,000 条”当 pilot（本目录 00b 提供按样本分层抽样）。

---

## 2. 推荐口径与文献依据

| 决策 | 推荐 | 依据 |
|---|---|---|
| 长度分层 | **5–9 aa：探索层**（单列报数，不进结论）·**10–50：核心层**（DualAMP+Macrel）·**51–100：Macrel 专属层**（DualAMP 不输出/不外推）·**>100：前体蛋白/长 sORF 表**（单独查 RiPP/细菌素） | real4 训练范围 5–50（dataset_summary.json）；Macrel 官方 10–100 aa |
| 模型角色 | **M2 主评分 + M1 高召回辅助**（并集、不强求双阳）；M3 不主用；Macrel 独立复核（并行、取并集，非唯一准入） | real4 结果；Santos-Júnior 2020 PeerJ 8:e10555（Macrel，特异性导向；gut AMP 流行率 0.1–1.65%） |
| 去重顺序 | 多文件 → **精确去重**（sqlite，全来源映射分桶保留）→ **MMseqs2 easy-cluster 0.90/0.90**（仅降冗余提速）→ **只对代表序列跑 Macrel/ESM** → 分数回映全部成员 | Ma 2022（Nat Biotechnol）：2,349→216→181（>83% 活性）；Torres 2024（Cell）：444,054 家族→323 SEPs，78 合成 70.5% 活性；Santos-Júnior 2024（Cell）：63,410 宏基因组；863,498 候选；100 合成 79 活性 |
| Tier | Tier1 = (M1+ ∨ M2+) ∧ Macrel+，或 M1+ ∧ M2+；Tier2 = M2≥0.95 或 (M1≥0.90 ∧ M2≥0.75)；Tier3 = 其余/组成驱动（scrambled 判阳率 M1 73.2%/M2 56.8%） | real4 scrambled 结果；Macrel NA 不视为阴性 |
| 表达证据 | 预测后叠加：metatranscriptomics/metaproteomics 回帖 + 多组装命中 + 家族代表性交叉，性价比从高到低排序 | Torres 2024（metaproteome 证据层可选）；AMPSphere |
| 输出 | `all_sORF_master.tsv`、`sORF_source_map.bucket-*.tsv`、`DualAMP_prediction.tsv`、`Macrel_prediction.tsv`、`AMP_candidate_priority.tsv`（含 Layer/Tier）、`ripp_candidates.tsv`（隔离） | 与 AI 建议一致 + 来源映射闭合要求 |

**两条纪律**：
- 全量（>1e8）前**必须先 pilot 1e5 标定漏斗**；任何全量结果在 pilot 标定前=探索性；
- real4 结果是 candidate_v1 口径的 pilot，不是最终验证；v2 以 positive_core_v2 重训，test 重锁。

---

## 3. 运行手册（命令 + QC + 预期漏斗）

> 环境：WSL2/Linux · Python ≥3.10 · GNU awk · `mmseqs2`（`sudo apt-get install -y mmseqs2`）·
> python3 -m venv：`pip install macrel==1.6.1`（已验证 1.6.1）· `pip install fair-esm torch`（GPU 推荐）·
> `seqkit`（可选，仅 splits 便利）

### 阶段 0｜输入登记
```bash
# input_manifest.tsv: file_path<TAB>sample_id[<TAB>assembly_id]
python3 - << 'PY'
import hashlib, os
for line in open("input_manifest.tsv").read().splitlines()[1:]:
    f = line.split("\t")[0]
    print(f, os.path.getsize(f), hashlib.sha256(open(f,"rb").read()).hexdigest())
PY > input_manifest.sha256.txt
```
QC：文件数/大小/哈希与上游一致；header 以 `>` 开头；**sha256 落盘**。

### 阶段 0.5｜（仅 pilot）按样本分层抽样
```bash
python3 scripts/00b_make_pilot.py --manifest input_manifest.tsv --target 100000 --out work/pilot_100k/pilot.faa --seed 7
```
每文件 reservoir sampling，小文件给保底份额；合并 fasta header 重写为 `>sample_id|orig_header`。
QC：`pilot.faa.qc.json`（picked≈target；每文件 allocated 与记录数成比例）。**不要用 `--limit` 代替抽样。**

### 阶段 1｜标准化 + 精确去重（含来源映射）
```bash
python3 scripts/00_standardize_faa.py --manifest input_manifest.tsv --out-dir work/standardize
```
多文件可并用 `--input a.faa b.faa`；输出：
`all_sORF_master.tsv`（seq_sha256, seq, length, count, first_id, low_complexity）、`unique.faa`、
`unique_dualamp_5_50.faa`、`unique_macrel_10_100.faa`、`source_map/sORF_source_map.bucket-*.tsv`（64 桶）、
`sORF_source_map_index.json`、`qc_standardize.json`。
规则：5–100 aa · 标准 20 AA · 低复杂度**保留但标记**（`low_complexity=1`，不删）。
QC（数量级警示，非硬门槛）：`dup_reduction_ratio` 0.2–0.95；`source_map_rows == after_alphabet`（**闭合必须成立**）；
`unique_sequences` 与计数闭合。**中止条件：闭合失败、全部文件 0 记录、哈希链断裂。**

### 阶段 2｜家族聚类 → 代表序列
```bash
scripts/01_cluster_reps.sh work/standardize/unique.faa work/cluster 16   # ENGINE=linclust 可单独测速
```
产物：`work/cluster/reps.faa`、`mm_cluster.tsv`、`cluster_qc.json`。
QC：`cluster_rows == unique_inputs`（每条唯一序列恰好一家）；`members_not_in_unique == 0`；
`reps_fraction` 0.3–0.9（警示）。**中止条件：闭合失败。**

### 阶段 3a｜Macrel 独立筛选（并行路 A）
```bash
python3 -m venv .venv-macrel && .venv-macrel/bin/pip install macrel==1.6.1
MACREL_BIN=.venv-macrel/bin/macrel scripts/02_macrel_predict.sh work/standardize/unique_macrel_10_100.faa work/macrel 16
# 需要逐行核对时：MACREL_EXTRA="--keep-negatives"（保留阴性行；默认只输出 AMP 阳性）
```
产物：`work/macrel/macrel.out.prediction.gz`
列：`Access Sequence AMP_family AMP_probability Hemolytic Hemolytic_probability`（首行 `#` 注释；
v1.6.1 实测）。**语义：列出=判阳；未列出=非 AMP（或 NA，out-of-scope 时由 Layer 决定）。**
QC（脚本自检列；警示）：AMP 率>5% 先查同源块；<10 aa 与 >100 aa 不进 Macrel 层。

### 阶段 3b｜权重（二选一）
**A. 正式：等 positive_core_v2 锁定后** → `POSITIVES_SHA256=<v2 sha> scripts/03_train_export.sh <v2.fasta> weights`（≈1–2 h GPU）
**B. 调试/测速（非正式，2026-11-16 用户指示先跑）**：real5 = Kaggle 夜跑同口径 candidate_v1（5 seeds × 5 folds × 60 ep），
导出 .pt + scaler；产物标注 `candidate_v1_pilot_weights`，仅调试/初筛，不发布为正式模型、不写论文；
v2 回填后再训。**已跑通：2026-11-16T11:24Z Kaggle T4 完成（kernel shaohuawen/dualamp-real5-20261116）；
测试集 AUROC：M1 0.9767–0.9791 / M2 0.9796–0.9809 / M3 0.9770–0.9803；M0_RF ≈ 0.964。
15 个 .pt（M1/M2/M3 × 5 seeds，M2/M3 各 ≈2 MB 贴近 2 MiB 上限）+ scaler 按仓库约定不进 Git
（.gitignore `cloud-gpu/results/**/*.pt`）。**永久存放：GitHub prerelease `dualamp-real5-candidate_v1-pilot-weights-20261116`
（2026-11-16 发布；`.tar.gz` 20,928,730 bytes + `SHA256SUMS.txt` + `export_manifest.json` provenance），
workflow artifact `gpu-relay-run-18`（11/30 过期）仅为历史通道。归档由手动工作流 `weights-release.yml` 完成
（workflow_dispatch；不改 gpu-relay 派发链）。

### 阶段 4｜ESM-2 → M1/M2 批量打分
```bash
for k in $(seq 0 9); do
  python3 scripts/04_batch_score.py --input work/cluster/reps_5_50.faa \
    --export-dir weights --leanrea-dir .external/leanrea \
    --out work/dualamp/pred_shard_${k}.tsv --shard $k --num-shards 10 \
    --batch-size 64 --device cuda &
done; wait
cat work/dualamp/pred_shard_*.tsv | awk 'NR==1 || $1 != "seq"' > DualAMP_prediction.tsv
```
续跑：`--resume`；meta（`<out>.meta.json`）绑定 输入 sha256 + 权重集 sha256 + shard 数，不匹配直接拒绝。
QC：合计行数 == reps 数；每行加载 5 seeds；M1/M2 无 NaN；极端偏态先怀疑 dtype。

### 阶段 5｜合并 + Tier + Layer
```bash
python3 scripts/05_join_macrel.py --pred work/dualamp/pred_shard_*.tsv \
  --macrel-out work/macrel --out AMP_candidate_priority.tsv \
  --unique-faa work/standardize/unique.faa --cluster-tsv work/cluster/mm_cluster.tsv \
  --m1-thr 0.685 --m2-thr 0.885   # v2 训练后必须改为 v2 的 dev-OOF 阈值
```
输出列：Sequence,Length,**Layer**,M1_Score,M2_Score,M1_Pred,M2_Pred,**Macrel_Result(AMP|NON_AMP|NA)**,Macrel_Hemolytic,Net_Charge,GRAVY,pI,Family_Size,Tier。
QC（警示）：Tier1 ≤5%；**macrel_na 与 macrel_non_amp 分开统计**；5–9 层全部 NA 属正常。

### 路线 B｜RiPP/细菌素（隔离）
```bash
scripts/06_rule_b_ripp.sh contigs.fna work/rule_b   # RiPP_workflow_scaffold：antiSMASH 可跑，BAGEL4 切割未完成
```

---

## 4. QC 总账（警示而非硬门槛；闭合类为硬条件）

| 阶段 | 必查（硬/软） | 中止条件 |
|---|---|---|
| 0 输入 | 文件数/sha256/header 唯一（硬） | 数量不符/哈希变 |
| 1 标准化 | 计数闭合 input=accept+reject（硬）；source_map_rows==after_alphabet（硬）；dup_ratio 0.2–0.95（软） | 闭合失败/0 记录 |
| 2 聚类 | cluster_rows==unique_inputs（硬）；members 全在 unique（硬）；reps_fraction 0.3–0.9（软） | 闭合失败 |
| 3a Macrel | prediction 表可解析、列齐（硬）；AMP 率<5%（软） | 无表/列缺失 |
| 3b 权重 | export_manifest sha256 逐文件核对；leanrea commit 钉死 | 校验失败 |
| 4 DualAMP | 每代表一行；5 seeds 全加载；无 NaN | shard 缺失/全 0.99 饱和 |
| 5 并集 | 行数闭合；Tier/Macrel NA 分开计数 | 证据状态不明被标阴性 |
| 家族回映 | 每条候选代表可回映 contig/ORF/sample（硬闭合） | provenance 丢 |

---

## 5. 硬性禁忌
- 先 ESM 再去重打分；
- 把 RiPP/细菌素 core 肽送入 Tier 统计；
- 5–9 aa 记为 Macrel 阴性（应为 NA）；
- real4 test 再称盲测；real4（candidate_v1）结果写入论文；
- HF write token 发在聊天里、写进任何 Git 文件；
- 原始 sORF FASTA / 权重二进制进入 Git 历史（Release/HF 承载）。

## 6. 权重交付方式（实况）
- real4 .pt：**已不可恢复**（/tmp 销毁；fetch6 复验 kernel 输出无 .pt）。
- v2 重训后：10 个 .pt + export_manifest.json(sha256) → tar.gz → **GitHub Release**（`dualamp-pilot-v2-weights`）→
  **HuggingFace**：由你在本机用仅目标仓库权限的 fine-grained token 一键上传，或最终直接读 Release 资产；
  **我不接收 chat 里的 token**（与安全实践一致）。

## 7. 交付链接（本分支）
- 分支根：`arena/cac2205a-rea`（本目录 README 即入口）
- leanrea 上游：`https://github.com/hukeying85-ux/leanrea/tree/arena/4ecb70f1-leanrea/dualamp-lite`
- real4 训练日志（含聚类引擎更正）：`cloud-gpu/results/dualamp-real4-fetch6-20261116/dualamp-real4-20261116.log`

## 8. 审阅意见处置（2026-11-16；外部 AI 审阅 → 逐条核实）
| 意见 | 处置 |
|---|---|
| real4 聚类实为 mmseqs shim（非原生 cd-hit） | **核实属实**（log：`cd-hit->mmseqs shim … clustering: cdhit at identity 0.40/coverage 0.80`）；VERIFICATION §10 更正方法学声明；v2 固定引擎 |
| 00 保留 5–100 aa 而非 50；低复杂度改为标记 | ✅ 落地（默认 max-len=100；新增 low_complexity 列；产分 5_50/10_100 两个 FASTA） |
| 00 增加来源映射（分桶 source_map + manifest） | ✅ 落地（source_map/*.bucket-*.tsv；闭合硬 QC；all_sORF_master 加 seq_sha256） |
| 04 `global n_written` NameError | ✅ 属实——改 `nonlocal`；`--resume` 改 meta 绑定 sha256（输入/权重集/shard） |
| 05 Tier2 与 README 不一致；pI/净电荷公式错 | ✅ 属实——Tier2 固定为 M2≥0.95 ∨ (M1≥0.90 ∧ M2≥0.75)；理化列优先复用 leanrea biophys（带端基 HH），回退内置等价实现 |
| pilot 需分层抽样，不可 `--limit` | ✅ 新增 `00b_make_pilot.py`（每文件 reservoir，保底份额）；`--limit` 降级为调试项 |
| 06 RiPP 为 scaffold，未完整验证 | ✅ 脚本头部已标注 `RiPP_workflow_scaffold` + TODO 清单 |
| Torres 55/78、Ma 181/216、AMPSphere 86 万 | ✅ 核对无误（Cell 2024 DOI:10.1016/j.cell.2024.07.027；Nat Biotechnol DOI:10.1038/s41587-022-01226-0；Cell 2024 DOI:10.1016/j.cell.2024.05.013） |
| Macrel NA ≠ 阴性；51–100 层 | ✅ 落地（Macrel_Result ∈ AMP/NON_AMP/NA；Layer 分层；5–9 全 NA） |
| QC 阈值改警示、闭合做硬条件 | ✅ README §4 按此改写 |
| 不要 real5 先训 v1 | ✅ v1 仅调试；正式等 positive_core_v2 |
| HF token 别发聊天 | ✅ §6 明确 |
| easy-linclust 可测速但家族可能不同 | ✅ ENGINE=linclust 可选；不得与 easy-cluster 混用同一批次 |
| 多 FASTA per manifest 输入 | ✅ 00/00b 支持 `--manifest` 或 `--input` 多个 |

## 9. 文献
1. Ma Y, Guo Z, Xia B, et al. *Nat Biotechnol* 2022;40:921–931. DOI:10.1038/s41587-022-01226-0
2. Torres MDT, Brooks EF, Cesaro A, et al. *Cell* 2024;187:5453–5467.e15. DOI:10.1016/j.cell.2024.07.027
3. Santos-Júnior CD, Pan S, Zhao X, Coelho LP. *PeerJ* 2020;8:e10555. DOI:10.7717/peerj.10555
4. Discovery of AMPs in the global microbiome. *Cell* 2024. DOI:10.1016/j.cell.2024.05.013（≈86 万候选；AMPSphere: https://zenodo.org/records/6511404）
5. 长度/聚类口径：real4 真实训练范围 5–50（`dataset_dataset_summary.json`）；real4 聚类实为 mmseqs shim 0.40/0.80（本文、VERIFICATION §10）；CD-HIT 整簇 40%/0.80 若坚持原生口径需重跑。
