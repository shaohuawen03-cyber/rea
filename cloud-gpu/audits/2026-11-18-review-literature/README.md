# 2026-11-18 review.qc 文献核验回填（NOT_SIGNED；canonical 未动）

用户 2026-11-18 裁定：回填必须由**具体文献（PMID/DOI）**支撑；无文献行不得 CONFIRMED；不接受“AI 标注”口径。

## 文件
- `positive_core_qc_review_LIT_pass_2026-11-18_NOT_SIGNED.tsv` — 230 行回填版（Review_Result / Review_Reason 重写）
- `lit_pass_audit.tsv` — 逐行核验表（Sequence_ID / stratum / Citation_Candidates / PMID 状态 / verdict / proposed result）
- `build_lit_review.py` — 复现脚本 + 核验规则 + per-PMID 判定表（journal/year/主题）

## 核验方法与边界
- 通道：NCBI eutils `esummary.fcgi?db=pubmed`（fetch_page，2026-11-18）+ 若干 web_search 摘要；title/journal/year 级验证。
- **未**逐篇重读 full text / 未复测 MIC；CONFIRMED 的含义＝“该行的主证据是记录正文以 [Ref.NNN] 引用的一篇本 pass 已核验存在且主题为抗菌肽的文献”（记录级一致＋文献存在＋主题一致）。
- 无 [Ref.NNN] 嵌入 或 主题非抗菌肽 的行一律 UNSURE（见脚本规则）。
- 所有 76 行带 PMID 行的 PMID 均已在本次 pass 中逐条核验存在；0 条“编造 PMID”；3 条因主题/嵌入原因降级 UNSURE。
- 154 行无 citation：按用户规则 **不得 CONFIRMED**。
- 与 AI 预填草案的关系：草案（NOT_REVIEW）228 CONFIRMED/2 REJECT 仅作线索；本件与其独立；任何冲突以本件＋签核为准。

## 签核路径（需 user/PI；本代理不代签）
1. user/PI 逐行（或批量）复核 `positive_core_qc_review_LIT_pass_2026-11-18_NOT_SIGNED.tsv`
2. 同意 → 回填 Drive canonical（`1cLGMOz7uugdRRjJj_02Q70a2mmu_bayn`）的 Review_Result/Review_Reason
3. 重新跑 `build_positive_core_v2.py report` 解除 v2 RUNBOOK 闸门（本件不自动解除）
4. 对 AMP_009038（抗原虫/possible REJECT_NOT_ANTIBACTERIAL）与 AMP_007292（无引用，抗肿瘤声明）单独裁定

## 已知误差/风险
- title/journal 级核验不等于逐篇 MIC 复核（e.g., APD6 记录多自引 APD；若记录把 [Ref] 标错，本 pass 不纠错）。
- esummary 可达性依赖 eutils 服务稳定性；fetch_page 偶发 500（pubmed.ncbi.nlm.nih.gov 曾 500，eutils 端点可用）。
- 该回填为 PILOT 侧独立证据；正式 v2 定稿仍需 user/PI 签核＋runbook 流程。
