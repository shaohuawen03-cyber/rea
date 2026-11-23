#!/usr/bin/env python3
"""Private Kaggle preflight: fetch Drive inputs, hash/count, emit only sanitized stats.

Raw FASTA/TSV files are kept under /tmp and removed before kernel exit. Never place
positive sequences or full metadata in Kaggle output, GitHub Actions artifacts, or Git.
Required environment: FASTA_ID, METADATA_ID, REVIEW_ID, LIT_REVIEW_ID.
"""
import csv, datetime as dt, hashlib, json, os, shutil, sys, tempfile, traceback
from collections import Counter
from pathlib import Path

INPUTS = {
    "candidate_fasta": "FASTA_ID",
    "candidate_metadata": "METADATA_ID",
    "canonical_review": "REVIEW_ID",
    "literature_pass": "LIT_REVIEW_ID",
}

def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
    return h.hexdigest()

def download(file_id, out):
    import gdown
    url=f"https://drive.google.com/uc?id={file_id}"
    p=gdown.download(url, str(out), quiet=True, fuzzy=True)
    if not p or not out.exists() or out.stat().st_size==0:
        raise RuntimeError("Drive download did not produce a non-empty file")
    return out

def read_tsv(path):
    with open(path,encoding="utf-8-sig",newline="") as f:
        rd=csv.DictReader(f,delimiter="\t")
        if not rd.fieldnames: raise ValueError(f"empty TSV header: {path.name}")
        rows=list(rd)
    return rd.fieldnames,rows

def fasta_stats(path):
    ids=[]; seq_hashes=Counter(); lengths=Counter(); invalid=0; current=None; chunks=[]
    def emit():
        nonlocal invalid
        if current is None:return
        seq="".join(chunks).replace(" ","").upper()
        if not seq: invalid+=1; return
        ids.append(current.split()[0]); seq_hashes[hashlib.sha256(seq.encode()).hexdigest()]+=1; lengths[len(seq)]+=1
    with open(path,encoding="utf-8") as f:
        for n,line in enumerate(f,1):
            line=line.strip()
            if not line:continue
            if line.startswith(">"):
                emit();current=line[1:].strip();chunks=[]
            else:
                if current is None: raise ValueError(f"FASTA sequence before header at line {n}")
                chunks.append(line)
    emit()
    id_counts=Counter(ids)
    return {"records":len(ids),"unique_ids":len(id_counts),"duplicate_ids":sum(v-1 for v in id_counts.values() if v>1),
            "unique_exact_sequences":len(seq_hashes),"duplicate_exact_sequences":sum(v-1 for v in seq_hashes.values() if v>1),
            "empty_sequence_records":invalid,"length_min":min(lengths) if lengths else 0,"length_max":max(lengths) if lengths else 0,
            "length_histogram":{str(k):v for k,v in sorted(lengths.items())}},set(ids)

def id_set(rows):
    vals=[(r.get("Sequence_ID") or "").strip() for r in rows]
    nonblank=[v for v in vals if v]
    counts=Counter(nonblank)
    return set(nonblank),{"rows":len(rows),"unique_ids":len(counts),"blank_ids":sum(not v for v in vals),
        "duplicate_ids":sum(n-1 for n in counts.values() if n>1)}

def summarize_tsv(path):
    cols,rows=read_tsv(path)
    ids,stats=id_set(rows)
    result={"bytes":path.stat().st_size,"sha256":digest(path),"columns":cols,**stats}
    for c in ("Review_Result","Evidence_Stratum","Core_Stratum","Source_DB","Citation_Candidates"):
        if c in cols:
            vals=Counter((r.get(c) or "").strip() for r in rows)
            result[c+"_counts"]={k:v for k,v in sorted(vals.items())}
    return result,ids,rows

def main():
    started=dt.datetime.now(dt.timezone.utc).isoformat()
    output=Path("/kaggle/working");output.mkdir(parents=True,exist_ok=True)
    private=Path(tempfile.mkdtemp(prefix="dualamp-v2-inputs-",dir="/tmp"))
    report={"stage":"drive_download_preflight","status":"running","started_utc":started,
            "note":"Raw inputs are private /tmp only; no sequence or row-level metadata is emitted."}
    try:
        ids={name:(os.environ.get(env) or "").strip() for name,env in INPUTS.items()}
        missing=[k for k,v in ids.items() if not v]
        if missing:raise RuntimeError("missing required input IDs: "+", ".join(missing))
        files={name:download(fid,private/(name+({"candidate_fasta":".fasta"}.get(name,".tsv")))) for name,fid in ids.items()}
        fasta, fasta_ids=fasta_stats(files["candidate_fasta"])
        meta,meta_ids,meta_rows=summarize_tsv(files["candidate_metadata"])
        canonical,canonical_ids,canonical_rows=summarize_tsv(files["canonical_review"])
        lit,lit_ids,lit_rows=summarize_tsv(files["literature_pass"])
        source_col=next((c for c in ("Source_DB","Source_Database","Source") if c in meta_rows[0]),None) if meta_rows else None
        stratum_col=next((c for c in ("Core_Stratum","Evidence_Stratum") if c in meta_rows[0]),None) if meta_rows else None
        def counts(col,rows):
            return dict(sorted(Counter((r.get(col) or "").strip() for r in rows).items())) if col else None
        status_match={"metadata_vs_fasta_equal":meta_ids==fasta_ids,
                      "metadata_only_ids":len(meta_ids-fasta_ids),"fasta_only_ids":len(fasta_ids-meta_ids),
                      "canonical_review_rows_in_metadata":len(canonical_ids & meta_ids),
                      "canonical_review_rows_in_fasta":len(canonical_ids & fasta_ids),
                      "lit_pass_rows_in_canonical":len(lit_ids & canonical_ids),
                      "canonical_only_ids":len(canonical_ids-lit_ids),"lit_pass_only_ids":len(lit_ids-canonical_ids)}
        if not status_match["metadata_vs_fasta_equal"]:
            status="BLOCKED_ID_SET_MISMATCH"
        else:status="PREFLIGHT_OK_NOT_TRAINING"
        report.update({"status":status,"inputs":{
            "candidate_fasta":{"bytes":files["candidate_fasta"].stat().st_size,"sha256":digest(files["candidate_fasta"]),**fasta},
            "candidate_metadata":meta,"canonical_review":canonical,"literature_pass":lit},
            "metadata_source_counts":counts(source_col,meta_rows),"metadata_stratum_counts":counts(stratum_col,meta_rows),
            "id_reconciliation":status_match,"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "training_started":False})
        (output/"preflight_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if status!="PREFLIGHT_OK_NOT_TRAINING":raise RuntimeError(status)
    except Exception as exc:
        report.update({"status":"FAILED","error":str(exc),"traceback":traceback.format_exc(limit=4),
                       "finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"training_started":False})
        (output/"preflight_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2),file=sys.stderr)
        raise
    finally:
        shutil.rmtree(private,ignore_errors=True)

if __name__=="__main__":main()
