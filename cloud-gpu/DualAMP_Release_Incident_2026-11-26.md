# 发布事故说明（2026-11-26）

对象：GitHub Release `dualamp-v2.1-weights-15-files-20261126`
（https://github.com/shaohuawen03-cyber/rea/releases/tag/dualamp-v2.1-weights-15-files-20261126）

## 1. 发生了什么

该 release 的**资产是真的**（15 个 `.pt`：M1 496,487 B ×5 / M2 1,988,851 B ×5 / M3 2,033,511 B ×5，
外加 `SHA256SUMS.txt` 2,604 B 与 `export_manifest.json`），但它页面上的**文字描述属于另一个模型**：

| 字段 | Release 页面写的 | 实际资产 |
|---|---|---|
| 口径 | `candidate_v1_pilot_weights`（2026-11-16 的 pilot） | positive_core_v2_1（DRAMP 回填后） |
| 训练 commit | `8a2d232` | v2.1 训练提交（由 r4 运行报告 `code_commit` 记录） |
| 阳性 FASTA SHA256 | `7b6ea121…68aa1d` | `9ca0d407…a8e40c` |
| 测试 AUROC | M1 0.9767–0.9791 / M2 0.9796–0.9809 / M3 0.9770–0.9803 | M1 0.9832 / M2 0.9889 / M3 0.9893 |
| 来源 kernel | `dualamp-real5-20261116` | `dualamp-v2-1-weights-r4-20261125` |

## 2. 根因（两个，都不是"手滑"）

1. **workflow 把说明文字写死在 YAML 里**。`.github/workflows/weights-release.yml` 的
   release-notes heredoc 直接 `echo` 了 pilot 时期的字面量，`pack_release_assets.py` 的
   `PROVENANCE` / `LABEL_NOTE` 默认值同样是 pilot 时期的值，且只在设置了环境变量时才变。
   于是**资产换了、说明没换**，而流程本身没有任何断言能发现这一点。
2. **创建与发布被拆成两步，中间没有一致性校验**。`gh release create`（无资产）→ 逐个
   `gh release upload`。create 成功即发布，因此任何一次 upload 中的中断都会留下
   "已发布但内容/说明不匹配"的状态。本次 17 个资产全部上传成功，说明失败发生在文字侧，
   但流程本身无法区分这两种情况。

另有一个相关事实：GitHub 在首次创建 tag 时会沿用该 tag 之前仍存在的 draft 的 `created_at`，
所以四个 release 的 `created_at` 都是 `2026-10-15T01:29:04Z`。这是 GitHub 侧的行为，
不代表发布时间，容易被误读为"同一天批量发布"。

## 3. 影响范围

- 资产（权重文件）本身**未受影响**：sha256 清单与 15 个 `.pt` 均可核对。
- **受影响的是可追溯性**：任何人按页面文字去核对阳性序列、测试指标或训练 commit，都会对不上。
  这是论文附录里最容易被审稿人抓住的一类问题。

## 4. 已做的修复

1. `weights-release.yml` 的说明文字改为**由 workflow 输入参数生成**
   （`label_note` / `provenance_kernel` / `provenance_commit` / `provenance_sha256` /
   `provenance_models` / `metrics_table`），不再有写死的 pilot 字面量。
2. `pack_release_assets.py` 的 provenance 通过环境变量透传（`PROVENANCE_*`、`LABEL_NOTE`），
   与 release 正文取自同一组输入，避免两处各写一份。
3. 标题为 `candidate_v1_pilot`、实际装的是 pilot 权重的旧 release
   `dualamp-v2.1-weights-20261125`（3 个资产，tarball 形式）**保持原样不删**，
   以免破坏任何已有引用；新的 v4 release 使用**全新 tag**，不覆盖任何旧 tag。

## 5. 尚未修复（需要用户决定）

- 已发布的 `dualamp-v2.1-weights-15-files-20261126` 页面文字**目前仍是错的**。
  可选方案：
  - (a) 保留资产，仅替换 release 正文（推荐；不改任何 tag、不改任何文件）；
  - (b) 新建一个 `…-corrected` tag 发布同批资产并加注指向；
  - (c) 删除该 release 并重发（会破坏已有外链，不推荐）。
- 在用户选定之前，本文档即为该 release 的更正声明，并已同步到 v4 交付文档。

## 6. 教训（写进交付文档，不写进 release）

> 一个"能下载"的发布不等于"可追溯"的发布。发布流程必须让**说明文字与资产来自同一份输入**，
> 并且能在发布前断言二者一致；否则错误会安静地留在线上。
