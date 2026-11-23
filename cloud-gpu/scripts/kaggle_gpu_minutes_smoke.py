#!/usr/bin/env python3
"""Run one private, bounded Kaggle GPU workload and capture quota before/after."""
import json, os, pathlib, re, subprocess, sys, tempfile, time, urllib.request, urllib.error
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gpu_ctl import resolve_kaggle_env


def quota_snapshot(env):
    token = env.get("KAGGLE_API_TOKEN", "").strip()
    if not token:
        return {"error": "KAGGLE_API_TOKEN unavailable; authenticated CLI may use legacy credentials"}
    req = urllib.request.Request(
        "https://api.kaggle.com/v1/kernels.KernelsApiService/GetAcceleratorQuotaStatistics",
        data=b"{}", headers={"Authorization": "Bearer " + token,
        "Content-Type": "application/json", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        g = data.get("gpuQuota") or {}
        def sec(v):
            if isinstance(v, (int, float)): return float(v)
            m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)s", str(v or "").strip())
            return float(m.group(1)) if m else None
        used, reserved, total = map(sec, (g.get("timeUsed"), g.get("timeReserved"), g.get("totalTimeAllowed")))
        return {"time_used_seconds": used, "time_reserved_seconds": reserved,
                "total_allowed_seconds": total,
                "remaining_seconds": max(0, total-(used or 0)-(reserved or 0)) if total is not None else None,
                "quota_refresh_time": data.get("quotaRefreshTime")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:250]}"}


def main():
    env, source = resolve_kaggle_env()
    if not source: raise SystemExit("Kaggle credentials unavailable")
    run_id = os.environ["GITHUB_RUN_ID"]
    ref = f"shaohuawen/dualamp-v2-gpu-minutes-{run_id}"
    report = {"kernel_ref": ref, "credential_source": source,
              "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "gpu_quota_before": quota_snapshot(env)}
    work = pathlib.Path(tempfile.mkdtemp(prefix="kaggle-gpu-minutes-"))
    kernel_code = r'''import json, time, torch
from pathlib import Path
result={"gpu_test":"FAIL","gpu_enabled":True,"started_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
try:
 if not torch.cuda.is_available(): raise RuntimeError("CUDA unavailable")
 result["device"]=torch.cuda.get_device_name(0)
 dev=torch.device("cuda:0")
 a=torch.randn((1536,1536),device=dev,dtype=torch.float32)
 b=torch.randn((1536,1536),device=dev,dtype=torch.float32)
 torch.cuda.synchronize(); start=time.monotonic(); deadline=start+180; iterations=0
 while time.monotonic()<deadline:
  c=torch.mm(a,b); iterations+=1
  if iterations%25==0:
   torch.cuda.synchronize(); print("GPU_SMOKE_PROGRESS seconds=%.1f iterations=%d"%(time.monotonic()-start,iterations),flush=True)
 torch.cuda.synchronize()
 result.update({"gpu_test":"PASS","gpu_seconds":round(time.monotonic()-start,2),"iterations":iterations,"cuda_version":torch.version.cuda,"peak_memory_bytes":torch.cuda.max_memory_allocated()})
except Exception as e: result["error"]=type(e).__name__+": "+str(e)[:400]
result["completed_utc"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
Path("/kaggle/working/gpu_minutes_smoke_result.json").write_text(json.dumps(result,indent=2)+"\n")
print("GPU_MINUTES_SMOKE_RESULT="+json.dumps(result),flush=True)
if result["gpu_test"]!="PASS": raise SystemExit(1)
'''
    metadata = {"id": ref, "title": "dualamp v2 gpu minutes " + run_id,
        "code_file": "main.py", "language": "python", "kernel_type": "script",
        "is_private": "true", "enable_gpu": "true", "enable_internet": "false",
        "machine_shape": "NvidiaTeslaT4", "dataset_sources": [], "competition_sources": [],
        "kernel_sources": [], "model_sources": []}
    (work/"main.py").write_text(kernel_code)
    (work/"kernel-metadata.json").write_text(json.dumps(metadata,indent=2))
    pushed=subprocess.run(["kaggle","kernels","push","-p",str(work),"--accelerator","NvidiaTeslaT4"],env=env,text=True,capture_output=True,timeout=120)
    report["push"]=(pushed.stdout or pushed.stderr).strip()[:1000]
    print("PUSH="+report["push"],flush=True)
    if pushed.returncode: raise SystemExit("Kaggle GPU smoke submission failed")
    status="unknown"; deadline=time.time()+1200
    while time.time()<deadline:
        q=subprocess.run(["kaggle","kernels","status",ref],env=env,text=True,capture_output=True,timeout=60)
        status=(q.stdout or q.stderr).strip()
        report["kernel_status"]=status
        print("KERNEL_STATUS="+status,flush=True)
        if any(s in status.upper() for s in ("COMPLETE","ERROR","CANCEL")): break
        time.sleep(20)
    if not any(s in status.upper() for s in ("COMPLETE","ERROR","CANCEL")):
        subprocess.run(["kaggle","kernels","cancel",ref],env=env,text=True,capture_output=True,timeout=60)
        report["cancel_requested"]=True
    out=pathlib.Path("gpu-minutes-smoke-output"); out.mkdir(exist_ok=True)
    fetched=subprocess.run(["kaggle","kernels","output",ref,"-p",str(out),"-o"],env=env,text=True,capture_output=True,timeout=180)
    report["output_fetch"]=(fetched.stdout or fetched.stderr).strip()[:1000]
    result_path=out/"gpu_minutes_smoke_result.json"
    if result_path.exists(): report["kernel_report"]=json.loads(result_path.read_text())
    report["gpu_quota_after"]=quota_snapshot(env)
    report["finished_utc"]=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())
    report["gpu_quota_delta_seconds"]=(report["gpu_quota_after"].get("time_used_seconds")-report["gpu_quota_before"].get("time_used_seconds")
       if isinstance(report["gpu_quota_after"].get("time_used_seconds"),(int,float)) and isinstance(report["gpu_quota_before"].get("time_used_seconds"),(int,float)) else None)
    (out/"result.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print("FINAL_GPU_SMOKE="+json.dumps(report,ensure_ascii=False),flush=True)
    if (report.get("kernel_report") or {}).get("gpu_test")!="PASS": raise SystemExit("GPU smoke did not produce a PASS report")

if __name__=="__main__": main()
