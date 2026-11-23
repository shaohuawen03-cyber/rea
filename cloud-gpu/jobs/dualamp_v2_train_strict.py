#!/usr/bin/env python3
"""Train the strictly frozen DualAMP v2 set; upload all model binaries privately.

This is a small-cohort exploratory v2 run: the current strict core has 16 positives,
so test metrics are descriptive only and cannot support definitive performance claims.
"""
from __future__ import annotations
import csv, datetime as dt, hashlib, json, os, shutil, subprocess, sys, tempfile, traceback, zipfile
from pathlib import Path

BUNDLE_SHA256 = "e09c75018c983bdf449b8e46790e357f1e943cc42809abf911dbffeb6c0099bd"
POSITIVE_SHA256 = "45e237b3fa347d4279e6eb9a19ffa74bd33fb27cf13bb7943f69e635d4389ae6"


def sha(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""): h.update(b)
    return h.hexdigest()


def upload(uri: str, path: Path):
    import requests
    size = path.stat().st_size; offset = 0; response = None
    with path.open("rb") as f:
        while offset < size:
            chunk = f.read(8 * 1024 * 1024); final = offset + len(chunk) >= size
            headers = {"Content-Type":"application/zip", "Content-Length":str(len(chunk)),
                       "X-Goog-Upload-Offset":str(offset),
                       "X-Goog-Upload-Command":"upload, finalize" if final else "upload",
                       "X-Goog-Upload-Total-Size":str(size)}
            response = requests.put(uri, headers=headers, data=chunk, timeout=180)
            if response.status_code not in (200, 201): raise RuntimeError(f"private model upload failed: HTTP {response.status_code}")
            offset += len(chunk)
    return {"bytes":size, "sha256":sha(path), "drive_file":response.json() if response else {}}


def csv_dicts(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd) if rd.fieldnames else []


def main():
    out = Path("/kaggle/working"); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dualamp-v2-train-", dir="/tmp"))
    report = {"stage":"dualamp_v2_strict_training", "started_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
              "dataset_version":"positive_core_v2_strict", "training_started":False,
              "positive_records":16, "positive_unique_sequences":16, "test_positive_n":2,
              "warning":"Severely underpowered evaluation cohort. Metrics are exploratory/descriptive, not definitive or publication-ready."}
    try:
        bundle_id = (os.environ.get("EVIDENCE_AUDIT_ID") or "").strip()
        uri = (os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
        supplied_sha = (os.environ.get("FASTA_SHA256") or "").strip()
        if not bundle_id or not uri: raise RuntimeError("private frozen bundle ID and model-output upload session are required")
        if supplied_sha != POSITIVE_SHA256: raise RuntimeError("v2 strict FASTA SHA-256 is absent or does not match the frozen manifest")
        import gdown
        bundle = tmp / "frozen_bundle.zip"
        got = gdown.download(id=bundle_id, output=str(bundle), quiet=True)
        if not got or sha(bundle) != BUNDLE_SHA256: raise RuntimeError("private frozen bundle download/hash verification failed")
        with zipfile.ZipFile(bundle) as z:
            member = "frozen/positive_core_v2.fasta"
            if member not in z.namelist(): raise RuntimeError("strict positive FASTA missing from frozen bundle")
            positive = tmp / "positive_core_v2.fasta"
            positive.write_bytes(z.read(member))
        if sha(positive) != POSITIVE_SHA256: raise RuntimeError("strict positive FASTA hash differs from frozen manifest")
        branch = os.environ.get("REA_BRANCH", "arena/cac2205a-rea")
        repo = tmp / "rea"
        subprocess.run(["git","clone","--depth","1","--branch",branch,
                        "https://github.com/shaohuawen03-cyber/rea.git",str(repo)],check=True,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        code_sha = subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
        work, artifacts = tmp / "work", tmp / "artifacts"
        env = {**os.environ, "REA_REPO_DIR":str(repo), "REA_BRANCH":branch,
               "FASTA_SHA256":POSITIVE_SHA256, "LEANREA_REV":"f610dc00b1aec3ab948a5b77574af0e644259a13"}
        cmd = [sys.executable, str(repo/"cloud-gpu/jobs/dualamp_pilot.py"),
               "--mode","real","--dataset-version","positive_core_v2_strict",
               "--fasta-file",str(positive),"--fasta-sha",POSITIVE_SHA256,
               "--workdir",str(work),"--artifact-dir",str(artifacts),
               "--models","M1,M2,M3","--seeds","1,2,3,4,5","--folds","5",
               "--epochs","60","--neg-cap","10000","--neg-mode","standard"]
        report["training_started"] = True
        runlog = tmp / "training.log"
        with runlog.open("w",encoding="utf-8") as f:
            subprocess.run(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
        summary_path = artifacts / "_pilot_summary.json"
        metrics_path = artifacts / "manuscript_D10_test_metrics.csv"
        dataset_path = artifacts / "dataset_dataset_summary.json"
        model_summary_path = artifacts / "model_train_summary.json"
        if not all(p.exists() for p in (summary_path,metrics_path,dataset_path,model_summary_path)):
            raise RuntimeError("training finished without the required report artifacts")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        metrics = csv_dicts(metrics_path)
        # Keep all model/scaler binaries and row-level split artifacts out of GitHub outputs.
        private_files = []
        for root in (work/"run"/"model", work/"run"/"features"):
            if root.exists():
                for p in root.rglob("*"):
                    if p.is_file() and (p.suffix in {".pt", ".joblib"}): private_files.append(p)
        if not any(p.suffix == ".pt" for p in private_files): raise RuntimeError("no trained checkpoint found for private archive")
        private_zip = tmp / "dualamp_v2_strict_model_bundle.zip"
        with zipfile.ZipFile(private_zip,"w",compression=zipfile.ZIP_DEFLATED) as z:
            for p in sorted(private_files): z.write(p,p.relative_to(work/"run"))
            for p in (summary_path,metrics_path,dataset_path,model_summary_path):
                z.write(p,"reports/"+p.name)
            for p in sorted((work/"run"/"splits").glob("*.csv")) if (work/"run"/"splits").exists() else []:
                z.write(p,"splits/"+p.name)
            z.writestr("run_provenance.json",json.dumps({
                "dataset_version":"positive_core_v2_strict", "rule_version":"auto_evidence_v2_1.0",
                "strict_positive_fasta_sha256":POSITIVE_SHA256,"frozen_bundle_sha256":BUNDLE_SHA256,
                "repo_commit":code_sha,"leanrea_commit":"f610dc00b1aec3ab948a5b77574af0e644259a13",
                "seeds":[1,2,3,4,5],"folds":5,"epochs":60,"models":["M1","M2","M3"],
                "negative_semantics":"weak UniProt background; not experimental inactivity",
                "evaluation_warning":"test contains only two strict positives; descriptive only"
            },indent=2))
        private_upload = upload(uri,private_zip)
        report.update({
            "status":"COMPLETED_EXPLORATORY_V2_TRAINING",
            "completed_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "code_commit":code_sha,"leanrea_commit":"f610dc00b1aec3ab948a5b77574af0e644259a13",
            "dataset_summary":{k:dataset.get(k) for k in ("n_clusters","n_sequences","cluster_method","cluster_identity","cluster_coverage","layer_counts","split_counts","split_positives","split_negatives")},
            "metrics":metrics,
            "private_model_bundle":private_upload,
            "note":"Training completed on rule-frozen strict v2 data. With only 16 positives and 2 in test, this is a successful execution smoke/feasibility run, not a statistically adequate validation or final model claim. Weak negatives are not experimentally verified inactive peptides."
        })
        (out/"v2_training_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        # Include only sanitized summary and plain training log; never copy checkpoints/sequences to kernel outputs.
        shutil.copy2(runlog,out/"v2_training.log")
        print(json.dumps({k:v for k,v in report.items() if k not in {"metrics"}},ensure_ascii=False,indent=2),flush=True)
        print(json.dumps({"metrics":metrics},ensure_ascii=False,indent=2),flush=True)
    except Exception as e:
        report.update({"status":"FAILED_NOT_COMPLETED", "error":str(e),
                       "traceback":traceback.format_exc(limit=5), "finished_utc":dt.datetime.now(dt.timezone.utc).isoformat()})
        (out/"v2_training_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2),file=sys.stderr,flush=True)
        raise
    finally:
        shutil.rmtree(tmp,ignore_errors=True)

if __name__ == "__main__": main()
