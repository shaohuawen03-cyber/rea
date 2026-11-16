# UniProt A 快照下载 · 总结（2026-11-11）

> 冻结口径（不变）：Release **2026_04**（2026-11-04）｜查询式
> `(reviewed:true) AND (taxonomy_id:2) AND (cc_scl_term:SL-0086) AND (length:[100 TO 5000]) AND (NOT ft_signal:*) AND (NOT ft_transmem:*)`
> ｜FASTA **117,172** 条｜TSV **117,172** 行（另 +1 表头）
>
> | 文件 | 字节 | md5 | sha256 |
> |---|---|---|---|
> | `uniprot_bacteria_cytoplasm_reviewed.fasta.gz` | 27,721,477 | `4f0bd8c8…2ed6` | `53edcd309eb1855b1c982ebf661216c8ca6fce287ae37045b0577e50e6cefb1a` |
> | `uniprot_bacteria_cytoplasm_reviewed.tsv.gz` | 3,704,283 | `7830d540…4f75` | `0f9b6bb2c818b36f6c2f697bac5b9d7edb06a178c5da619f34321dbdb92ec88b` |
>
> 2026-11-10 两次独立获取（06:44Z 本机路径 / 07:03Z Kaggle 路径）**逐字节一致**，`x-total-results=117172`，Release 2026_04。

## 1. 沙箱侧通道：全部实测失败（2026-11-11 复测确认）

| 通道 | 结果 |
|---|---|
| UniProt `rest.uniprot.org` / `ftp.uniprot.org` | TLS EOF（被阻断） |
| Expasy / EBI / DDBJ / 各 UniProt 镜像 | 同上（前序轮次已测） |
| Kaggle `api.kaggle.com` / `www.kaggle.com` | TLS EOF；`web-auth-prod.kaggle.com`、`kaggle-api.kaggle.com` 直接 DNS 失败 |
| Colab `colab.research.google.com` | TLS EOF |
| Google Drive 直连（`drive.google.com` / `www.googleapis.com`） | TLS EOF |
| Google Drive **连接器**（本账号可见范围） | 搜 `uniprot` → 仅 3 个审计小文件（`uniprot_A_audit.tsv` 等，位于 `06_uniprot_audit`）；搜 `cytoplasm` → 0 条。**快照不在本连接器可见的 Drive 中** |
| GitHub（contents/blobs/tarball/releases） | 无 Swiss-Prot 完整镜像；快照按 `.gitignore` 设计不入库 |
| GitHub LFS | 全部 403（匿名/带凭据一致）；`nodes.dmp` 实为 134 B LFS 指针 |
| GitHub Actions 中继 | 集成 403；本仓库无 workflow；leanrea 的 gpu-relay 已于 2026-11-10T07:12Z 移除 |
| PyPI / npm（simple 索引 ~48 MB 全量 grep） | 无 Swiss-Prot 数据包（仅 CLI 客户端） |

**结论：本沙箱无法拿到 A 快照字节。唯一可行路径 = 在可上网的机器上下载（方案 A 直连 / 方案 B Kaggle 内核），或由你把文件附件发入本对话。**

## 2. leanrea 分支核验（`hukeying85-ux/leanrea @ arena/4ecb70f1-leanrea`，commit `703de65a`）

- `dualamp-lite/docs/UNIPROT_DOWNLOAD.md` 记录两条获取路径（本机直连 / Kaggle 内核 + 一次性 resumable URI 回传**私有** Drive）与 2026-11-10 校验结果（字节数、md5、条数逐字节一致）。
- 两个 Kaggle 内核 slug：`uniprot-fasta-20261110`（queue.json @ `2a31aaf0`，2026-11-10T07:04Z，CPU）与 `uniprot-tsv-20261110`（queue.json @ `63dfe791`，07:08Z）。
- 快照字节**不在该仓库**（`.gitignore` 明确不入库）；一次性 resumable URI 已失效；其回传的私有 Drive **不是**本会话连接器可见的账号。
- 本项目只读引用该分支，未向其推送任何内容。

## 3. 方案 A —— 直连下载链接（推荐，零凭据）

在可上网的机器上执行（bash；`curl` 自带编码，避免查询串手误）：

```bash
QUERY='(reviewed:true) AND (taxonomy_id:2) AND (cc_scl_term:SL-0086) AND (length:[100 TO 5000]) AND (NOT ft_signal:*) AND (NOT ft_transmem:*)'

curl -fL --retry 5 --retry-delay 3 --max-time 2400 -G \
  "https://rest.uniprot.org/uniprotkb/stream" \
  --data-urlencode "query=${QUERY}" \
  --data-urlencode "format=fasta" \
  --data-urlencode "compressed=true" \
  -o uniprot_bacteria_cytoplasm_reviewed.fasta.gz

curl -fL --retry 5 --retry-delay 3 --max-time 2400 -G \
  "https://rest.uniprot.org/uniprotkb/stream" \
  --data-urlencode "query=${QUERY}" \
  --data-urlencode "format=tsv" \
  --data-urlencode "compressed=true" \
  --data-urlencode "fields=accession,id,protein_name,organism_name,length,cc_subcellular_location" \
  -o uniprot_bacteria_cytoplasm_reviewed.tsv.gz
```

等价直链（GET，同一查询式；注意必须整体编码）：

- FASTA：`https://rest.uniprot.org/uniprotkb/stream?query=%28reviewed%3Atrue%29+AND+%28taxonomy_id%3A2%29+AND+%28cc_scl_term%3ASL-0086%29+AND+%28length%3A%5B100+TO+5000%5D%29+AND+%28NOT+ft_signal%3A%2A%29+AND+%28NOT+ft_transmem%3A%2A%29&format=fasta&compressed=true`
- TSV：`https://rest.uniprot.org/uniprotkb/stream?query=%28reviewed%3Atrue%29+AND+%28taxonomy_id%3A2%29+AND+%28cc_scl_term%3ASL-0086%29+AND+%28length%3A%5B100+TO+5000%5D%29+AND+%28NOT+ft_signal%3A%2A%29+AND+%28NOT+ft_transmem%3A%2A%29&format=tsv&compressed=true&fields=accession,id,protein_name,organism_name,length,cc_subcellular_location`

## 4. 方案 B —— 用你本机的 Kaggle token 拉取内核输出

在你本机（不要在沙箱里）执行：

```bash
pip install kaggle
export KAGGLE_API_TOKEN=<你的token>          # 或 ~/.kaggle/kaggle.json
kaggle kernels output <你的Kaggle用户名>/uniprot-fasta-20261110 -p ./uniprot_a_2026-11-11
kaggle kernels output <你的Kaggle用户名>/uniprot-tsv-20261110  -p ./uniprot_a_2026-11-11
```

- `queue.json` 只记录了 slug（`uniprot-fasta-20261110` / `uniprot-tsv-20261110`），**owner 需要你填自己的 Kaggle 用户名**；2026-11-10T07:04Z / 07:08Z 创建，内核输出一般保留 30 天（过期后走方案 A）。
- token 只留在你本机；**不要**再粘贴进对话（见 §7 安全提醒）。

## 5. 本地校验命令

```bash
gzip -t uniprot_bacteria_cytoplasm_reviewed.fasta.gz
gzip -t uniprot_bacteria_cytoplasm_reviewed.tsv.gz
zcat uniprot_bacteria_cytoplasm_reviewed.fasta.gz | grep -c '^>'        # 预期 117172
zcat uniprot_bacteria_cytoplasm_reviewed.tsv.gz   | tail -n +2 | wc -l   # 预期 117172
sha256sum uniprot_bacteria_cytoplasm_reviewed.fasta.gz
sha256sum uniprot_bacteria_cytoplasm_reviewed.tsv.gz
```

预期值见顶部表格。**若 sha256 不符**，说明 UniProt 已发布新 Release（当前锁定 2026_04），快照口径需重登记——这属于口径变更，必须你确认后才能继续，不允许静默替换。

## 6. 按照之前做好的流程（下一步）

1. 你按方案 A 或 B 拿到两个 `.gz`；
2. 把文件附件发入本对话（27.7 MB 若超限：`split -b 20m uniprot_bacteria_cytoplasm_reviewed.fasta.gz fasta.part.`，把所有分卷发给我，我合并并校验）；
3. 我执行：`gzip -t` → 条数比对 → sha256 校验 → 落盘到 `/home/user/uniprot_snapshot_2026-11-11/`（**仓库外，不入 Git**）→ 更新 `data/processed/uniprot/uniprot_A_audit.tsv`（补 Download path / 实测 sha256）；
4. 然后回到既定顺序：01/02 Drive 原件回查 → 初筛处置确认（candidate_v1）→ 230 条抽核 → `/home/user/venv` 重建 → 阳性漏斗 / 40% identity 划分 / 最近邻审计。

约束复述：raw 不入训练、不改原件；任何 UniProt 替代品（B、RafaelGallo parquet、Ma2022）**不得未经你确认替代 A**；Ma2022 仅引用；CAMP/LAMP/ADAM 不下载；candidate_v1 不改 audit。

## 7. 安全提醒

- 你在对话中粘贴过 `KGAT_…` token；它可能进入对话记录与任何日志。**建议 Kaggle → Settings → API → 删除该 token 并 Create New Token 轮换**；轮换后方案 B 用新 token，互不影响。
- 最稳妥：不再分享 token，方案 A 直链零凭据。
- 本总结不包含任何凭据；本仓库按你的要求不写入 raw 数据与云凭据；`/home/user/uniprot_snapshot_2026-11-11/` 已建于仓库外。

## 8. 给下次会话的避坑清单（已实测，勿再试）

- 沙箱对 kaggle.com / uniprot.org / googleapis.com / drive.* 一律 TLS EOF，DNS 对 `web-auth-prod.kaggle.com` / `kaggle-api.kaggle.com` 失败；
- GitHub LFS 全系 403（匿名与带凭据一致）；Actions 端点 403；rea 无 workflow；
- PyPI simple 索引（48 MB 全量）与 npm 无 Swiss-Prot 数据包；GitHub code search 无完整镜像（只有 fixture / parser 库 / LFS 指针）；
- Google Drive 连接器：本账号只见 3 个审计小文件（`06_uniprot_audit/` 文件夹 `1X7Hgco2Ztndgkbm8qRhIwtSdjHeOUyFR`，Drive 根 `1tSZY37BEDxfSTfRilLfDbMbvplw6f3qb`），**无快照**；`download_file` 单个文件返回 ≤ 100 MB 且以 base64 内联，不适合 27.7 MB 大文件回灌沙箱——请用对话附件方式。
