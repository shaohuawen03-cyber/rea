#!/usr/bin/env python3
"""Minimal GPU 冒烟载荷：为「额度减少」验证提供一次真实的 GPU 占用。

这不是科学实验，只做三件事：
  1. 打印 GPU 型号 / 可见卡数（证明真的拿到加速器）；
  2. 在 CUDA 上做一次足够长的矩阵乘 + 收敛校验（默认 120 s，可用 --seconds 调整）；
  3. 把结果写到 /kaggle/working/quota_smoke_result.json，供中继取回。

依赖只用 Kaggle 镜像自带的 torch，不装任何第三方包（enable_internet=false）。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=120.0, help="mind the GPU for this long")
    parser.add_argument("--size", type=int, default=2048, help="matmul dimension")
    args = parser.parse_args()

    out: dict = {
        "script": "quota_smoke",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "requested_seconds": args.seconds,
    }

    try:
        import torch
    except Exception as exc:  # noqa: BLE001
        out["fatal"] = f"torch import failed: {type(exc).__name__}: {exc}"
        print(json.dumps(out, indent=2), flush=True)
        return 1

    out["torch"] = {
        "version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_version": getattr(torch.version, "cuda", None),
        "device_count": torch.cuda.device_count(),
        "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
    }
    if not torch.cuda.is_available():
        out["fatal"] = "CUDA not available; this kernel would not consume GPU quota"
        print(json.dumps(out, indent=2), flush=True)
        return 1

    device = torch.device("cuda:0")
    torch.manual_seed(0)
    a = torch.randn(args.size, args.size, device=device, dtype=torch.float16)
    b = torch.randn(args.size, args.size, device=device, dtype=torch.float16)
    reference = (a.float() @ b.float()).sum().item()

    started = time.time()
    iterations = 0
    while time.time() - started < args.seconds:
        c = a @ b
        a, b = b, c.half()
        iterations += 1
        if iterations % 20 == 0:
            torch.cuda.synchronize()
    torch.cuda.synchronize()
    elapsed = time.time() - started

    result = (a.float() @ b.float()).sum().item()
    out["run"] = {
        "device": str(device),
        "matmul_dim": args.size,
        "iterations": iterations,
        "elapsed_sec": round(elapsed, 2),
        "gflops_approx": round(iterations * 2 * args.size ** 3 / elapsed / 1e9, 3),
        "memory_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
        "finite": bool(torch.isfinite(a).all().item() and torch.isfinite(b).all().item()),
        "checksum_changed": result != reference,
    }
    out["status"] = "completed"
    blob = json.dumps(out, ensure_ascii=False, indent=2)
    print(blob, flush=True)

    with open("/kaggle/working/quota_smoke_result.json", "w", encoding="utf-8") as f:
        f.write(blob + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
