# 云端算力打通验证记录(2026-11-16)

本文件记录 **工程链路** 验证,不包含任何科学结论,不包含凭据。

## 链路(已全线贯通)

```
沙箱(仅出网 github/pypi/npm)
  │  git push(cloud-gpu/jobs/queue.json 入队 / cloud-gpu/secrets/*.enc 一次性信封)
  ▼
GitHub Actions 中继(.github/workflows/gpu-relay.yml,push 触发)
  │  kaggle CLI(凭据由信封解密 → ~/.kaggle,私钥只在 Actions 缓存)
  ▼
Kaggle 云端 GPU(私有 kernel,is_private)
  │  kaggle kernels output 取回产物
  ▼
results/<job-id>/(≤2 MiB 小文件)随 bot 提交回分支 → 沙箱 git pull 收货
```

## 凭据模型

- 一次性 RSA-4096-OAEP + AES-256-CBC 信封(`cloud-gpu/secrets/*.enc`),公钥 `relay_pubkey.pem`
  由中继首跑自动发布;信封在同一 run 内被消费删除,历史里只留密文。
- 仓库无明文云凭据;`kaggle.json`/`kaggle_token` 等在 `.gitignore` 黑名单。

## 验证结果(`gpu-smoke-20261113`,kernel `shaohuawen/arena-gpu-smoke-20261113`)

| 项 | 值 |
| --- | --- |
| GPU | **2× Tesla T4**(各 15360 MiB) |
| torch / CUDA | 2.11.0+cu130 / 13.0,`cuda_available: true`,2 devices |
| fair-esm | 2.0.0,`esm2_t12_35M_UR50D`,device=cuda |
| 耗时 | 权重加载 1.76s,前向 0.959s |
| embedding | dim 480,8 条序列,finite,mean_norm 6.9407,pairwise_min_cos 0.6842 |
| 结果回传 | `gpu_smoke_v4_result.json` + 日志,经 results/ 回到分支 ✅ |

单轮链路时延:入队 push → 探针+公钥发布 ≈45s;任务 push→执行→取回 ≈3min(空队列)。

## 已知事项

- Kaggle 免费额度约 30h GPU/周、单次会话 ≤12h;长任务用 `--async-push` + `mode: status/output` 轮询。
- `sk-` 开头的密钥不是 Kaggle 格式(Kaggle 为 `KGAT_…` 或 kaggle.json 的 username+key)。
- Colab 通道(`colab/Kaggle_Colab_Bridge.ipynb`)保留为备用/交互调试入口。
- 复刻自 hukeying85-ux/leanrea `arena/4ecb70f1-leanrea` 的 gpu-relay 方案(2026-11-10 首验)。

## 额度体检实测记录（2026-11-23）

- 读数通道已验证：`api.kaggle.com/.../GetAcceleratorQuotaStatistics` 返回
  `gpuQuota.timeUsed = 52.17s`、`totalTimeAllowed = 108000.0s`（30 h/周）、
  `quotaRefreshTime = 2026-11-28T00:00:00Z`。工具 `scripts/kaggle_quota_report.py` 与工作流
  `.github/workflows/gpu-quota.yml` 端到端跑通（before/after/delta 三份 JSON 回传）。
- 本分支凭据：一次性信封 `secrets/KAGGLE_API_TOKEN.enc` → 中继解密写入 `~/.gpu-relay` 金库后
  `git rm` 删除（仓库只留密文；`gh secret set` 因 GitHub App 无 secrets 权限返回 403）。
- **当前阻塞**：`kaggle kernels push` 返回 `Maximum batch GPU session count of 2 reached`，
  说明该账号已有 2 个 GPU 会话占满（Kaggle 交互式 notebook 打开着就算，且这些会话对 API 不可见）。
  因此 `dualamp-real8-v2-retry1-20261123` 自 11:23 起未真正开始、冒烟内核也推不上去，
  额度停在 52.17s。关掉浏览器里的 Kaggle 会话后重跑同一请求文件，
  即可看到 `used_seconds` 增加、`remaining_hours` 减少。

排查辅助：`scripts/kernel_status.py`（`--raw` 直接调 `GetKernelSessionStatus`，返回 `{}` 表示
**没有活动会话**，CLI 的 `QUEUED` 只是枚举默认值）、`scripts/kaggle_probe.py`（原始 Connect-RPC
探测）、`scripts/cancel_kernel.py`（取消需要 `kernel_session_id`，只给 slug 会 403）。
