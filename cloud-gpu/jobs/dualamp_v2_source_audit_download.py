#!/usr/bin/env python3
"""Fetch Drive source-audit support files privately and emit aggregate integrity stats only."""
import csv,datetime as dt,hashlib,json,os,shutil,sys,tempfile,traceback
from collections import Counter
from pathlib import Path
FILES={
 "positive_evidence_audit":"EVIDENCE_AUDIT_ID",
 "apd6_natural_bacterial_mapping":"APD6_MAPPING_ID",
 "apd_1002_machine_screen":"APD_MACHINE_SCREEN_ID",
 "dramp_file_overlap_report":"DRAMP_OVERLAP_ID",
 "dramp5_public_dataset_distribution":"DRAMP_DISTRIBUTION_ID",
 "dramp5_file_stats":"DRAMP_STATS_ID",
 "dramp5_specific_discrepancy_analysis":"DRAMP_DISCREPANCY_ID",
 "dramp5_public_conclusion":"DRAMP_CONCLUSION_ID",
}
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()
def fetch(fid,path):
 import gdown
 ret=gdown.download(f"https://drive.google.com/uc?id={fid}",str(path),quiet=True,fuzzy=True)
 if not ret or not path.exists() or path.stat().st_size==0:raise RuntimeError(f"download failed for {path.name}")
def aggregate(path):
 ext=path.suffix.lower()
 if ext not in {".tsv",".csv"}:return {"bytes":path.stat().st_size,"sha256":sha(path),"parse":"text/non-tabular"}
 delimiter="\t" if ext==".tsv" else ","
 with open(path,encoding="utf-8-sig",newline="") as f:
  rd=csv.DictReader(f,delimiter=delimiter)
  if not rd.fieldnames:return {"bytes":path.stat().st_size,"sha256":sha(path),"parse":"empty table"}
  cols=rd.fieldnames; rows=list(rd)
 out={"bytes":path.stat().st_size,"sha256":sha(path),"rows":len(rows),"columns":cols}
 for c in ("Sequence_ID","ID","APD_ID","DRAMP_ID","Source_Record_ID"):
  if c in cols:
   vals=[(r.get(c) or "").strip() for r in rows]
   out[c+"_unique"]=len(set(v for v in vals if v));out[c+"_blank"]=sum(not v for v in vals)
 for c in ("Citation","Citation_Candidates","PMID","PubMed_ID","Reference","Evidence_Level","Review_Status","Core_Stratum","Source_DB","Activity","Biological_Activity","Decision","Status"):
  if c in cols:
   cnt=Counter((r.get(c) or "").strip() for r in rows)
   if c.lower().find("citation")>=0 or c.lower() in {"pmid","pubmed_id","reference"}:
    out[c+"_nonempty"]=sum(n for k,n in cnt.items() if k)
   if len(cnt)<=40:out[c+"_counts"]={k:v for k,v in sorted(cnt.items())}
 return out
def main():
 outdir=Path("/kaggle/working");outdir.mkdir(parents=True,exist_ok=True);tmp=Path(tempfile.mkdtemp(prefix="dualamp-source-audit-",dir="/tmp"))
 r={"stage":"source_audit_integrity","status":"running","started_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"note":"Private /tmp transfer; no sequences or row-level records emitted."}
 try:
  missing=[(n,e) for n,e in FILES.items() if not (os.environ.get(e) or "").strip()]
  if missing:raise RuntimeError("missing file IDs: "+", ".join(n for n,_ in missing))
  stats={}
  for name,env in FILES.items():
   ext=".txt" if name.endswith(("analysis","conclusion")) else (".csv" if any(k in name for k in ("overlap","distribution","stats")) else ".tsv")
   p=tmp/(name+ext);fetch(os.environ[env].strip(),p);stats[name]=aggregate(p)
  audit=stats["positive_evidence_audit"]
  r.update({"status":"SOURCE_FILES_DOWNLOADED_AND_HASHED","files":stats,"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"training_started":False})
  (outdir/"source_audit_integrity.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  print(json.dumps(r,ensure_ascii=False,indent=2))
 except Exception as e:
  r.update({"status":"FAILED","error":str(e),"traceback":traceback.format_exc(limit=4),"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat()})
  (outdir/"source_audit_integrity.json").write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
  print(json.dumps(r,ensure_ascii=False,indent=2),file=sys.stderr);raise
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=="__main__":main()
