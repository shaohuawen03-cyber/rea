#!/usr/bin/env python3
"""Return privacy-safe counts describing citation fields for candidate_v1 source rows."""
import csv,datetime as dt,hashlib,json,os,shutil,sys,tempfile,traceback,re
from collections import Counter
from pathlib import Path

def fetch(fid,path):
 import gdown
 got=gdown.download(f"https://drive.google.com/uc?id={fid}",str(path),quiet=True,fuzzy=True)
 if not got or not path.exists() or not path.stat().st_size:raise RuntimeError("Drive download failed: "+path.name)
def readfa(p):
 ids=[];s=None
 with open(p,encoding="utf-8") as f:
  for line in f:
   line=line.strip()
   if line.startswith(">"):
    s=line[1:].split()[0];ids.append(s)
 if len(ids)!=len(set(ids)):raise RuntimeError("duplicate FASTA IDs")
 return set(ids)
def readtsv(p):
 with open(p,encoding="utf-8-sig",newline="") as f:
  rd=csv.DictReader(f,delimiter="\t")
  if not rd.fieldnames:raise RuntimeError("missing TSV header")
  return rd.fieldnames,list(rd)
def main():
 tmp=Path(tempfile.mkdtemp(prefix="dualamp-cite-diagnostic-",dir="/tmp"));out=Path("/kaggle/working");out.mkdir(parents=True,exist_ok=True)
 report={"stage":"candidate_citation_field_diagnostics","status":"running","started_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"raw_rows_or_sequences_emitted":False}
 try:
  for env in ("FASTA_ID","METADATA_ID","EVIDENCE_AUDIT_ID"):
   if not (os.environ.get(env) or "").strip():raise RuntimeError("missing "+env)
  paths={k:tmp/v for k,v in (("fasta","candidate.fasta"),("meta","metadata.tsv"),("audit","audit.tsv"))}
  for k,e in (("fasta","FASTA_ID"),("meta","METADATA_ID"),("audit","EVIDENCE_AUDIT_ID")):fetch(os.environ[e].strip(),paths[k])
  candidate_ids=readfa(paths["fasta"]);mcols,mrows=readtsv(paths["meta"]);acols,arows=readtsv(paths["audit"])
  candmeta={r.get("Sequence_ID","").strip() for r in mrows}
  matched=[r for r in arows if (r.get("Sequence_ID") or "").strip() in candidate_ids]
  report.update({"candidate_fasta_ids":len(candidate_ids),"candidate_metadata_rows":len(mrows),"source_audit_rows":len(arows),"candidate_metadata_id_set_matches_fasta":candmeta==candidate_ids,"candidate_ids_in_source_audit":len({r.get("Sequence_ID","").strip() for r in matched}),"source_audit_rows_for_candidates":len(matched),"source_audit_columns":acols})
  fields=[c for c in ("Citation_Candidates","Source_Record_IDs","Source_DBs","Record_TIERS","Activity_Types","Sample_Targets","Review_Status","Review_Reason","Adjudication") if c in acols]
  fr={}
  doi=re.compile(r"(?i)10\.\d{4,9}/[0-9A-Z._;()/:+-]+")
  pmidlabel=re.compile(r"(?i)\b(?:PMID|PubMed\s*ID|pubmed)\s*[:=#]?\s*\d{4,9}\b")
  pmcid=re.compile(r"(?i)\bPMC\s*\d{4,9}\b")
  refs=re.compile(r"(?i)\b(?:ref(?:erence)?|citation)\s*[.:#_-]?\s*\d+\b")
  for c in fields:
   vals=[(r.get(c) or "").strip() for r in matched]
   non=[v for v in vals if v]
   entry={"rows_nonempty":len(non),"unique_nonempty_values":len(set(non)),"total_characters":sum(map(len,non)),"median_value_characters":sorted(map(len,non))[len(non)//2] if non else 0}
   if c.lower().find("citation")>=0 or c in {"Source_Record_IDs","Review_Reason"}:
    entry["rows_with_doi_pattern"]=sum(bool(doi.search(v)) for v in non);entry["rows_with_explicit_pmid_pattern"]=sum(bool(pmidlabel.search(v)) for v in non);entry["rows_with_pmcid_pattern"]=sum(bool(pmcid.search(v)) for v in non);entry["rows_with_reference_number_pattern"]=sum(bool(refs.search(v)) for v in non)
    entry["rows_with_7plus_digit_token"]=sum(bool(re.search(r"(?<!\d)\d{7,9}(?!\d)",v)) for v in non)
   fr[c]=entry
  statuses=Counter((r.get("Review_Status") or "").strip() for r in matched) if "Review_Status" in acols else Counter()
  report["candidate_field_diagnostics"]=fr;report["candidate_review_status_counts"]={k:v for k,v in sorted(statuses.items())};report["finished_utc"]=dt.datetime.now(dt.timezone.utc).isoformat();report["status"]="DIAGNOSTIC_COMPLETE_NO_ROW_DATA"
  (out/"citation_diagnostics.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2))
 except Exception as e:
  report.update({"status":"FAILED","error":str(e),"traceback":traceback.format_exc(limit=4),"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat()});(out/"citation_diagnostics.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2),file=sys.stderr);raise
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=="__main__":main()
