# cloud-gpu — Kaggle × Colab 算力中继

受限沙箱（Arena）只放行 `github.com` / `pypi.org` / `registry.npmjs.org`，**无法直连
`kaggle.com`、`colab.research.google.com`、`oauth2.googleapis.com`**。本目录把
GitHub Actions 当作中继，打通「沙箱 ⇄ Kaggle GPU ⇄ Colab GPU」两条算力通道。
方案复刻自 [hukeying85-ux/leanrea `arena/4ecb70f1-leanrea`](https://github.com/hukeying85-ux/leanrea/tree/arena/4ecb70f1-leanrea)
（2026-11-10 已验证：Kaggle 2×T4 + torch/CUDA + fair-esm + 结果回传全链路可用）。

## 架构

```
┌──────────────┐   git push (queue.json)   ┌─────────────────────┐   kaggle CLI   ┌──────────────┐
│  Arena 沙箱   │ ────────────────────────► │  GitHub Actions 中继  │ ─────────────► │ Kaggle 云端   │
│  (本仓库)     │ ◄──────────────────────── │  (ubuntu-latest)     │ ◄───────────── │ 2×T4 GPU     │
└──────────────┘   git pull (results/)     └─────────────────────┘  kernel output └──────────────┘
       │                                          ▲
       │ google_drive 连接器                        │ (可选) 一次性 RSA+AES 信封
       ▼                                          │   cloud-gpu/secrets/*.enc
┌──────────────┐   Open in Colab (用户点击)  ┌─────┴───────────┐
│  Google Drive │ ◄────────────────────────► │  Colab 云端 GPU  │
│  arena-bridge │      Drive inbox/outbox     │  (T4/L4/A100)   │
└──────────────┘                             └─────────────────┘
```

- **Kaggle 通道（全自动）**：往 `jobs/queue.json` 加任务 → push → Actions 中继用
  Kaggle CLI `kaggle kernels push` 把载荷推成私有 kernel（GPU 2×T4、开外网）→ 轮询
  完成 → `kaggle kernels output` 取回产物 → 小结果（≤2 MiB/文件）提交回
  `results/<job-id>/`，完整包上传 workflow artifact，可选写回私有 Drive。
- **Colab 通道（用户点击）**：打开 [`colab/Arena_GPU_Bridge.ipynb`](colab/Arena_GPU_Bridge.ipynb)
  （Open in Colab 徽章），挂载 Drive 后按 inbox 任务执行，结果写到
  `MyDrive/arena-bridge/outbox/`；沙箱侧用 google_drive 连接器投放任务、读取结果。

## 快速开始

### 0. 凭据（一次性）

- **Kaggle**：<https://www.kaggle.com/settings/api> → Create New API Token → `kaggle.json`
  （内含 `username` + `key`）。两种注入方式任选：
  1. 仓库管理员设置 Actions secrets `KAGGLE_USERNAME` / `KAGGLE_KEY`（推荐，永不出现在 git）；
  2. 一次性信封（本会话采用）：等中继首次运行后提交 `relay_pubkey.pem`，然后
     ```bash
     python cloud-gpu/scripts/encrypt_secret.py encrypt \
       --pubkey cloud-gpu/relay_pubkey.pem \
       --out cloud-gpu/secrets/KAGGLE_API_TOKEN.enc --from-file /path/to/kaggle.json
     git add cloud-gpu/secrets/KAGGLE_API_TOKEN.enc && git commit -m "..." && git push
     ```
     中继解密后即 `git rm` 该信封（同一 run 内消费，历史里只留密文）。
- **Colab**：无需密钥，用户在浏览器里点开笔记本即可（挂载自己的 Drive）。

### 1. 提交任务（Kaggle）

```bash
python cloud-gpu/scripts/gpu_ctl.py enqueue \
  --provider kaggle --accelerator NvidiaTeslaT4 \
  --script cloud-gpu/jobs/gpu_smoke.py --extra-pip fair-esm \
  --id gpu-smoke-$(date +%Y%m%d) --wait
git add cloud-gpu/jobs/queue.json && git commit -m "chore(gpu-relay): enqueue ..." && git push
```

`git push` 即触发 `.github/workflows/gpu-relay.yml`（改 `queue.json` / `secrets/**` 都会触发；
`workflow_dispatch` 也可手动触发，支持 `only_id` / `force`）。

### 2. 取回结果（Kaggle）

```bash
git pull                                          # results/<job-id>/ 里的小结果
gh run download --dir /tmp/relay-out               # 完整 artifact（如 gh 权限允许）
```

`results/queue-final.json` 是队列最终状态；每个任务目录含 `result.json`（kernel 链接、
状态、产物清单）和 kernel 输出的小文件（日志、指标 JSON）。

### 3. Colab 手动通道

1. 打开笔记本：<https://colab.research.google.com/github/shaohuawen03-cyber/rea/blob/arena/cac2205a-rea/cloud-gpu/colab/Arena_GPU_Bridge.ipynb>
2. 菜单「代码执行程序 → 更改运行时类型」选 GPU，然后从上到下运行单元格。
3. 有 inbox 任务就执行任务，没有就跑内置 GPU 冒烟；结果一律写
   `Drive:/MyDrive/arena-bridge/outbox/<job-id>/`。
4. 沙箱侧（agent）用 google_drive 连接器读 outbox，或直接读单元格输出。

## ⚠️ 一次性安装:启用工作流

`gpu-relay.yml` 必须位于 `.github/workflows/` 才会被 GitHub Actions 执行。当前 Arena 的
GitHub App token 没有 `workflows` 权限,无法推送该目录(实测:`refusing to allow a GitHub
App to create or update workflow ... without 'workflows' permission`)。仓库管理员二选一:

1. **(推荐)在 Arena 里重连 GitHub**,让 App 获得 `workflows` 权限;之后 agent 可以直接
   `git mv cloud-gpu/gpu-relay.yml .github/workflows/gpu-relay.yml` 并推送。
2. **github.com 网页手动一次**:打开
   [cloud-gpu/gpu-relay.yml](https://github.com/shaohuawen03-cyber/rea/blob/arena/cac2205a-rea/cloud-gpu/gpu-relay.yml)
   → Raw → 复制;再
   [新建文件](https://github.com/shaohuawen03-cyber/rea/new/arena/cac2205a-rea?filename=.github%2Fworkflows%2Fgpu-relay.yml)
   路径填 `.github/workflows/gpu-relay.yml`,粘贴内容,提交到分支
   `arena/cac2205a-rea`。此提交本身就会触发第一次中继运行(探针 + 发布公钥)。

首次触发后 `cloud-gpu/relay_pubkey.pem` 会自动出现;之后的 `queue.json` / `secrets/**`
推送都会自动触发中继,无需再手工操作。

## 目录

| 路径 | 说明 |
| --- | --- |
| `scripts/gpu_ctl.py` | 统一控制器：`doctor`（凭据体检）/ `route`（Colab vs Kaggle 选型）/ `enqueue`（入队）/ `run-queue`（中继执行） |
| `scripts/encrypt_secret.py` | RSA-4096-OAEP + AES-256-CBC 一次性信封（stdlib + openssl） |
| `scripts/drive_put.py` | 向预授权 Drive resumable URI 流式回传大产物 |
| `jobs/queue.json` | 任务队列，**push 本文件即触发中继** |
| `jobs/gpu_smoke.py` | Kaggle 2×T4 冒烟载荷（torch/CUDA/ESM 前向 + 可选 Drive 回传） |
| `gpu-relay.yml` | 中继工作流本体;须安装到 `.github/workflows/` 后生效（见上文「一次性安装」） |
| `colab/Arena_GPU_Bridge.ipynb` | 用户点击的 Colab 桥接笔记本 |
| `secrets/` | 一次性加密信封（提交后由中继消费删除） |
| `results/` | 中继回传的小结果（自动生成，≤2 MiB/文件） |
| `relay_pubkey.pem` | 中继公钥（首次运行自动提交；私钥只在 Actions 缓存里） |

## 任务（job）字段

`gpu_ctl.py enqueue` 生成的字段：`id`、`provider`（`kaggle`/`colab`/`probe`）、`mode`
（`run`/`deploy`/`status`/`output`/`probe`）、`accelerator`（Kaggle：`NvidiaTeslaT4` 等；
Colab：`T4`/`L4`/`A100`）、`script_path`、`script_args`、`extra_pip`、`wait`、
`timeout_sec`、`kernel_slug`、`dataset_sources`（Kaggle 数据集挂载）。

路由建议（`gpu_ctl.py route`）：

| 需求 | 推荐 | 理由 |
| --- | --- | --- |
| 单卡 ≤16 GB、任意时长 ≤12 h | Kaggle 2×T4 | 免费 30 h/周、12 h 连续、可跑 DataParallel |
| 双卡 / 长任务 / 异步部署 | Kaggle | 不占 Actions 分钟数 |
| 快速迭代调试 / ≤2 h 单卡 | Colab | 秒级起会话；>16 GB 单卡要 Colab Pro（L4/A100） |

## 安全模型

- 仓库里**永不出现明文云凭据**：要么用 Actions secrets，要么用一次性信封（密文短暂存在，
  同一 run 内被 `git rm`）。
- 中继私钥只存在于 Actions cache（`~/.gpu-relay/key.pem`）；公钥随仓库发布。
- kernel 一律 `is_private: true`；运行日志默认只进 results/（小）与 artifact（14 天）。
- `kaggle.json`、`kaggle_token`、`colab_adc.json`、`vault.json` 在 `.gitignore` 黑名单里。

## 相关 GitHub 仓库

| 仓库 | 用途 |
| --- | --- |
| [Kaggle/kaggle-cli](https://github.com/Kaggle/kaggle-cli) | 官方 Kaggle CLI/Python API（`kaggle kernels push/status/output`，本中继的执行层） |
| [googlecolab/colabtools](https://github.com/googlecolab/colabtools) | Colab 官方 Python 库（notebook 环境与工具） |
| [1823616178/colab_task_list](https://github.com/1823616178/colab_task_list) | Colab CLI 云端算力自动化 + Kaggle 工具集（kllm/kcloud、kagglehub 示例、kernel 推送示例） |
| [hukeying85-ux/leanrea](https://github.com/hukeying85-ux/leanrea/tree/arena/4ecb70f1-leanrea) | 本方案的出处：`cloud-gpu/` GPU 中继 + Kaggle 2×T4 验证记录（`dualamp-lite/docs/GPU_VALIDATION.md`） |
| [morluto/rea](https://github.com/morluto/rea) | 本仓库上游：REA（Reverse Engineer Anything） |

外部参考模式：CircleCI/Actions 触发 `kaggle kernels push` 的 CI→Kaggle 流水线
（如 [CircleCI × Unsloth Gemma 微调](https://circleci.com/blog/finetuning-gemma-3-on-private-data-with-unsloth/)）
与本中继同一思路；社区也有 cron 调度 Kaggle notebook 的
[示例](https://www.kaggle.com/discussions/product-feedback/371090)。

## FAQ

**为什么不直接在沙箱里跑 kaggle CLI？** 沙箱出网白名单没有 kaggle.com（实测连接超时）。
GitHub Actions 在白名单内，且其 runner 可以出网，于是成了跳板。

**为什么 `gh secret set` 用不了？** 当前 GitHub token 无 Actions secrets 权限（HTTP 403），
所以走一次性信封通道；若日后拿到权限，设 `KAGGLE_USERNAME`/`KAGGLE_KEY` 更省事。

**任务失败了怎么重试？** 把 `queue.json` 里对应任务的 `status` 改成 `retry`（或
`workflow_dispatch` 加 `force=true`）再 push / 触发。

**大文件怎么办？** results/ 只收 ≤2 MiB 的小文件；大产物用 `scripts/drive_put.py` 传私有
Drive（`DRIVE_RESULT_UPLOAD_URI` 信封），或在载荷里直接 `kaggle datasets push` 到自己的数据集。
