#!/usr/bin/env python3
"""Private CPU-only Kaggle smoke: authenticate, run, download frozen v2 zip, verify hashes."""
import json, os, pathlib, subprocess, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gpu_ctl import resolve_kaggle_env

BUNDLE_ID = "1mrcNByKLxoyqM0Hvs1aEmhlJgrYQtZch"
BUNDLE_SHA = "e09c75018c983bdf449b8e46790e357f1e943cc42809abf911dbffeb6c0099bd"
FASTA_SHA = "45e237b3fa347d4279e6eb9a19ffa74bd33fb27cf13bb7943f69e635d4389ae6"

def main():
    env, source = resolve_kaggle_env()
    if not source:
        raise SystemExit("Kaggle credentials unavailable from relay vault")
    run_id = os.environ["GITHUB_RUN_ID"]
    slug = f"dualamp-v2-cpu-smoke-{run_id}"
    work = pathlib.Path(tempfile.mkdtemp(prefix="kaggle-cpu-smoke-"))
    code = '''import hashlib, json, subprocess, sys, zipfile
from pathlib import Path
result={"kernel_smoke":"started","gpu_enabled":False}
try:
 subprocess.check_call([sys.executable,"-m","pip","install","-q","gdown"])
 import gdown
 bundle=Path("/tmp/frozen_bundle.zip")
 got=gdown.download(id="1mrcNByKLxoyqM0Hvs1aEmhlJgrYQtZch",output=str(bundle),quiet=True)
 if not got: raise RuntimeError("gdown returned no file")
 def sha(path):
  h=hashlib.sha256()
  with open(path,"rb") as f:
   for b in iter(lambda:f.read(1<<20),b""): h.update(b)
  return h.hexdigest()
 bsha=sha(bundle)
 if bsha!="e09c75018c983bdf449b8e46790e357f1e943cc42809abf911dbffeb6c0099bd": raise RuntimeError("frozen bundle SHA-256 mismatch: "+bsha)
 with zipfile.ZipFile(bundle) as z: data=z.read("frozen/positive_core_v2.fasta")
 fsha=hashlib.sha256(data).hexdigest()
 if fsha!="45e237b3fa347d4279e6eb9a19ffa74bd33fb27cf13bb7943f69e635d4389ae6": raise RuntimeError("strict FASTA SHA-256 mismatch: "+fsha)
 result={"kernel_smoke":"PASS","gpu_enabled":False,"bundle_sha256":bsha,"strict_fasta_sha256":fsha,"bundle_bytes":bundle.stat().st_size}
except Exception as e:
 result={"kernel_smoke":"FAIL","gpu_enabled":False,"error":type(e).__name__+": "+str(e)[:500]}
Path("/kaggle/working/smoke_result.json").write_text(json.dumps(result,indent=2)+"\\n")
print("SMOKE_RESULT="+json.dumps(result),flush=True)
if result["kernel_smoke"]!="PASS": raise SystemExit(1)
'''
    metadata = {
        "id": "shaohuawen/" + slug,
        "title": "DualAMP v2 CPU smoke " + run_id,
        "code_file": "main.py", "language": "python", "kernel_type": "script",
        "is_private": "true", "enable_gpu": "false", "enable_internet": "true",
        "machine_shape": "", "dataset_sources": [], "competition_sources": [],
        "kernel_sources": [], "model_sources": [],
    }
    (work / "main.py").write_text(code)
    (work / "kernel-metadata.json").write_text(json.dumps(metadata, indent=2))
    push = subprocess.run(["kaggle", "kernels", "push", "-p", str(work)], env=env, text=True, capture_output=True, timeout=120)
    print("PUSH_RESULT=" + (push.stdout or push.stderr).strip()[:800], flush=True)
    if push.returncode:
        raise SystemExit("Kaggle CPU smoke submission failed")
    ref = "shaohuawen/" + slug
    status = "unknown"
    deadline = time.time() + 600
    while time.time() < deadline:
        q = subprocess.run(["kaggle", "kernels", "status", ref], env=env, text=True, capture_output=True, timeout=60)
        status = (q.stdout or q.stderr).strip()
        print("KERNEL_STATUS=" + status, flush=True)
        if any(s in status for s in ("COMPLETE", "ERROR", "CANCEL")):
            break
        time.sleep(15)
    out = pathlib.Path("cpu-smoke-output")
    out.mkdir(exist_ok=True)
    fetch = subprocess.run(["kaggle", "kernels", "output", ref, "-p", str(out), "-o"], env=env, text=True, capture_output=True, timeout=180)
    print("OUTPUT_FETCH=" + (fetch.stdout or fetch.stderr).strip()[:1000], flush=True)
    report_path = out / "smoke_result.json"
    summary = {"kernel_ref": ref, "kernel_status": status, "push_output": (push.stdout or "").strip()[:500]}
    if report_path.exists():
        summary.update(json.loads(report_path.read_text()))
    (out / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("FINAL_SMOKE_SUMMARY=" + json.dumps(summary), flush=True)
    if summary.get("kernel_smoke") != "PASS":
        raise SystemExit("CPU smoke did not pass")

if __name__ == "__main__":
    main()
