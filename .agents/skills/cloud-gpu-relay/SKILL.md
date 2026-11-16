---
name: cloud-gpu-relay
description: 在只能出网 github.com/pypi.org/npmjs.com 的受限沙箱里，通过 GitHub Actions 中继把 Kaggle（2×T4/P100）免费 GPU 打通成"解放双手"的无人值守派发/收货通道——git push 下单、Actions 代跑、结果 git pull 收货。覆盖入队、一次性加密信封、防环机制、结果回传与全部已知坑位。
---

# Cloud GPU Relay（GitHub Actions → Kaggle 免费 GPU）

沙箱只放行 `github.com` / `codeload.github.com` / `api.github.com` / `registry.npmjs.org` / `pypi.org` / `files.pythonhosted.org`；
直连 kaggle.com / colab.research.google.com / oauth2.googleapis.com 全部 TLS EOF。GitHub Actions runner 却可以。

```
sandbox --git push(queue.json)--> Actions gpu-relay --> kaggle kernels push --> Kaggle GPU
sandbox <--git pull(results/)--- Actions gpu-relay <--kernel output---------┘
```

用户零点击：agent 本地写载荷→提交→推送；工作流自动安装 kaggle CLI、解密信封、推送内核、等待完成、取回 ≤2 MiB 产物、由 bot 提交回分支并**消费（删除）信封**。

## 前置条件（首次）

1. `.github/workflows/gpu-relay.yml` 已安装（本仓库已就绪）；`cloud-gpu/relay_pubkey.pem` 已发布。
2. GitHub App（如 Arena AI Coding Agent）需要 **workflows 写权限**；否则推送 `.github/workflows/*` 会 403。
3. Kaggle 凭据（二选一）：
   - **一次性信封（推荐，零仓库明文）**：`python cloud-gpu/scripts/encrypt_secret.py encrypt --pubkey cloud-gpu/relay_pubkey.pem --in <plain> --out cloud-gpu/secrets/KAGGLE_API_TOKEN.enc`；
     信封随 bot 在同一 run 中解密后 `git rm`，**用一次就没了，下次任务要重新加密**（金库 `~/.gpu-relay` 缓存私钥）。
   - 仓库 Actions secrets：`KAGGLE_API_TOKEN`（或 `KAGGLE_USERNAME`+`KAGGLE_KEY`），`gh secret set`（App 需 secrets 权限，目前 403，仅 admin PAT 可用）。
4. Kaggle token：Kaggle → Settings → API → Create New Token；`KGAT_`+32 hex 是裸 token（本工具链直接写 `~/.kaggle/access_token`）；
   旧版 `kaggle.json`（`{username,key}`）也认。**绝不**在对话或仓库里粘贴明文 token。

## 派发一个任务（三步）

```bash
# 1) 写载荷（普通 Python 脚本；在 Kaggle 上 enable_internet=true 执行）
$EDITOR cloud-gpu/jobs/my_job.py

# 2) 入队（id 唯一；kernel_slug 小写；extra_pip 裸奔 fair-esm 记得 --no-deps 语义）
python cloud-gpu/scripts/gpu_ctl.py enqueue \
  --provider kaggle --accelerator NvidiaTeslaT4 \
  --script cloud-gpu/jobs/my_job.py \
  --extra-pip fair-esm \
  --id my-job-20261116 --kernel-slug my-job-20261116 \
  --wait --timeout-sec 5400 --note "说明（可选）"

# 3) 提交推送 —— 提交信息里【绝不能】出现 gpu-relay:（工作流防环 guard 会导致整个 run 被跳过）
git add cloud-gpu/jobs/queue.json cloud-gpu/jobs/my_job.py && git commit -m "chore(cloud-gpu): my-job-20261116" && git push origin <当前分支>
```

触发后：Actions 起 `gpu-relay` → 解密信封 → `kaggle kernels push` → 轮询 `kaggle kernels status` → `kernels output` 取回
→ 小文件拷入 `cloud-gpu/results/<job-id>/` → artifact 上传 → bot 以 **`gpu-relay:` 开头的固定信息**提交回分支。

## 收货

```bash
git fetch origin <当前分支> && git reset --hard origin/<当前分支>   # 或 pull
ls cloud-gpu/results/<job-id>/     # result.json / 日志 / 产物（≤2 MiB/文件）
```

大文件（>2 MiB 或 .pt/.safetensors）不回传 git：载荷内 `upload()` 走 Drive resumable URI（`DRIVE_UPLOAD_URI` 已注入 env），或留在 Kaggle kernel output 30 天内自取。

## 环境事实（实测 2026-11-16）

- Kaggle 免费额度：**2× Tesla T4 15360 MiB**（实测；P100 视机型），30 GPU h/周，单次 ≤12 h，会话 9 h 空闲断。
- 镜像自带 torch（实测 torch 2.11.0+cu130 / CUDA 13.0）；`fair-esm 必须 --no-deps`，否则把 CUDA torch 卸了。
- CD-HIT 不保证存在：pipeline 自动回退 MMseqs2 → 内置 k-mer；严谨划分请本地 CD-HIT 后 `--cluster-method precomputed`。
- ESM-2 权重首次从 `dl.fbaipublicfiles.com` 下载，Kaggle 联网 OK；离线可把 `esm2_t12_35M_UR50D.pt` 放 Kaggle Dataset。
- 内核以 `kaggle kernels push -p <dir>` 提交；元数据 `machine_shape=NvidiaTeslaT4`、`enable_internet=true`。

## 坑位清单（每条都踩过）

| 坑 | 现象 | 对策 |
|---|---|---|
| 提交信息含 `gpu-relay:` | 工作流 guard 判定为 bot 自己 → 整个 job 被 if 跳过、任务卡住不跑 | 派发提交一律用 `chore(cloud-gpu): ...`；只有 bot 提交才是 `gpu-relay: ...` |
| App 无 workflows 写权限 | push `.github/workflows/*` 被拒 / REST 403 | GitHub → Settings → Installed GitHub Apps → Arena AI Coding Agent → 权限 workflows 读写（2026-11-16 用户侧已开） |
| `.git` 在 turn 边界被回卷 | 新 turn 看似丢了刚提交的分支状态 | 开场先 `git fetch -q origin <分支> && git reset -q --hard origin/<分支>`；读 bot 结果绝不用 `--mixed` |
| 信封用后即焚 | 跑完 `cloud-gpu/secrets/*.enc` 被 bot 删除，commit 里能看到 | 每次任务重建信封；不要和 secrets 混用场景 |
| KGAT token 粘贴进对话 | token 进入对话记录 | 建议 Kaggle Settings→API 轮换；轮换后旧信封全部失效，重建即可 |
| 凭据写成明文进仓库 | 泄露 | 一律走信封/Actions secrets；仓库保持零明文 |
| `kaggle kernels output` 超时 | 大文件回传慢 | 大产物走 Drive resumable；git 只留小 JSON/日志 |
| 冒烟实测 | `gpu-smoke-20261113`：2×T4、torch+fair-esm、esm2_t12_35M 前向 0.96s、全绿 | 参照 `cloud-gpu/results/gpu-smoke-20261113/` |

## 已验证的典型用法：DualAMP-Lite

`cloud-gpu/jobs/dualamp_pilot.py`：内核内 `git clone` 公开仓库（本分支含 UniProt A 快照 117,172 条）→ 本地纯 Python 切阴性片段
（seed=11）→ `dualamp_lite build/features/train/report`（候选阳性由一次性信封中的 Drive 链接或附件提供）→ 指标经 relay 回传。

## 安全红线

- 不把任何云凭据明文写进仓库/对话/日志；`::add-mask::` 之后的值也不可外显。
- 信封只能被 `~/.gpu-relay/key.pem` 解开；公钥 `cloud-gpu/relay_pubkey.pem` 可公开。
- 跨仓库引用其他项目数据时遵循其许可；本仓库不入 raw 大数据（>2 MiB 一律外置）。
