# DualAMP 交付索引（2026-11-26）

## 文档（Google Drive，可直接打开）

| 文档 | 链接 |
|---|---|
| v4 方法学验证报告 | https://docs.google.com/document/d/1NtwYu95s7R4r05Vf3O4Gffrbc6WaGa1L/edit |
| 发布事故说明 | https://docs.google.com/document/d/13ueTMu-39VgAhjwgFvW4sNcV-Ylda5yc/edit |
| v2.1 复现性验证报告（较早） | https://docs.google.com/document/d/1xNrumnKnWIu5U82KKnEJo_oamwBAaShB/edit |

下载直链：
- v4：`https://drive.google.com/uc?id=1NtwYu95s7R4r05Vf3O4Gffrbc6WaGa1L&export=download`
- 事故说明：`https://drive.google.com/uc?id=13ueTMu-39VgAhjwgFvW4sNcV-Ylda5yc&export=download`

生成脚本（数字全部从 JSON 读取，可重跑）：
`cloud-gpu/scripts/build_v4_verification_docx.py`、`cloud-gpu/scripts/build_release_incident_docx.py`

## 权重发布

| tag | 内容 | 状态 |
|---|---|---|
| `dualamp-v4-weights-15-files-20261126` | 15 × `.pt` + SHA256SUMS.txt + export_manifest.json（17 资产） | **新发布，Pre-release** |
| `dualamp-v2.1-weights-15-files-20261126` | 同上，v2.1 权重（17 资产） | 未改动，链接仍可打开 |
| `dualamp-v2.1-weights-20261125` | v2.1 tarball（3 资产） | 未改动 |

v4 release：https://github.com/shaohuawen03-cyber/rea/releases/tag/dualamp-v4-weights-15-files-20261126

发布后核查：`cloud-gpu/scripts/verify_v4_release.py` → `cloud-gpu/reports/v4_release_verification.json`（5/5 项通过）

## 关键结论（一句话）

v4 把组成诱饵接受率从 ~67% 降到 17%（M2/M3），但未达 <10% 门槛；
代价是固定特异度 0.99 下灵敏度从 85% 降到 45%。
因此 v4 = **探索性发布**，不是「高分 SCI 支持的硕士论文发表版模型」。
下一步是 `cloud-gpu/jobs/dualamp_v5_train.py` 里的四个对照 arm。
