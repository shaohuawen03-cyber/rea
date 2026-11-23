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

读数来源：`POST https://api.kaggle.com/v1/kernels.KernelsApiService/GetAcceleratorQuotaStatistics`
（Bearer 裸 token），返回 protobuf duration 字符串，精确到毫秒；CLI `kaggle quota` 作兜底。

| 时间（UTC） | 动作 | `gpuQuota.timeUsed` | 剩余 |
| --- | --- | --- | --- |
| 11:49 | 首次读数（凭据刚就位） | 52.170 s | 29.9855 h |
| 13:25 | `quota-smoke-21`（60 s 内核）跑过一小段后 ERROR | 58.886 s（+6.7 s） | 29.9836 h |
| 13:31 | `quota-smoke-24`（240 s 内核）之后 | 66.559 s（+7.7 s） | 29.9815 h |
| 13:34 | 收尾快照（无新内核） | 66.559 s | 29.9815 h |

结论：**额度确实随 GPU 会话下降**（52.17 s → 66.56 s，两次真实会话共 14.4 s），
总量 108000 s = 30 h/周，刷新时间 `2026-11-28T00:00:00Z`。

过程坑位（都已在工具里处理）：

1. `.github/workflows/*` 能推送（App 有 workflows 权限），但 `gh secret set` 仍 403 → 走一次性信封；
   Actions cache 金库是**分支作用域**的，换分支要重新封一封信封（对应工作流会先生成本分支密钥对并发布公钥）。
2. `kaggle kernels push` 报 `Maximum batch GPU session count of 2 reached`：账号里 2 个 GPU 会话已满
   （浏览器开着的 notebook 也算，且 API 看不见）。删掉从未启动的内核 `dualamp-real8-v2-retry1` 后名额释放。
3. `kaggle kernels status` 的 `QUEUED` 不可信：`GetKernelSessionStatus` 返回 `{}` = **没有会话**（默认枚举值而已）。
   只有原始返回里有 `{"status": "RUNNING"|"ERROR"|...}` 才是真会话。
4. Kaggle T4 队列排队很久（10~25 分钟量级），所以冒烟默认 `no_wait` 推送、另起一次读数做对照。
5. 第一版冒烟内核用 `a = a @ b` 反复自乘，fp16 很快溢出成 inf 导致内核 ERROR；现在改成固定算子 +
   预分配输出并做有界衰减，`finite` 与 `ok` 都会写进产物。

复现：写 `cloud-gpu/jobs/quota-request.json` → push → bot 提交回 `cloud-gpu/results/quota-latest.json`。
