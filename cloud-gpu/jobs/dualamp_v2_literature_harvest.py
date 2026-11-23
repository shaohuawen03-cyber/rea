#!/usr/bin/env python3
"""Conservative full-text evidence harvest for all v2 candidate IDs.

Raw positive inputs and the row-level evidence ledger stay in private /tmp and are
removed on exit. Only aggregate QC returns through the relay; the ledger is streamed
to a one-time Google Drive resumable-upload session URL.

No LLM/model inference is used to invent citations or evidence. A strict positive draft
requires, within one PMC full-text passage/table: exact sequence string, named
bacterial target, assay + numeric experimental readout. All other cases are unresolved.
"""
from __future__ import annotations
import csv, datetime as dt, hashlib, json, os, re, shutil, sys, tempfile, time, traceback
from collections import Counter, defaultdict
from pathlib import Path

INPUTS={"fasta":"FASTA_ID","metadata":"METADATA_ID","audit":"EVIDENCE_AUDIT_ID"}
COLS=["Sequence_ID","Evidence_ID","DOI","PMID","PMCID","Source_URL","Evidence_Level","Sequence_Match","Endpoint_Class","Bacterial_Target","Assay_Type","MIC_Value","MIC_Unit","Activity_Result","Evidence_Location","Fulltext_Access","Conflict_Flag","Conflict_Reason","Rule_Version","Decision_Timestamp","Auto_Reason"]
RULE="auto_evidence_v2_1.0"
AA="ACDEFGHIKLMNPQRSTVWY"
BACTERIAL_GENUS={"Escherichia","Staphylococcus","Pseudomonas","Bacillus","Klebsiella","Enterococcus","Salmonella","Streptococcus","Listeria","Acinetobacter","Enterobacter","Proteus","Serratia","Shigella","Vibrio","Clostridium","Clostridioides","Campylobacter","Helicobacter","Neisseria","Moraxella","Haemophilus","Burkholderia","Citrobacter","Yersinia","Mycobacterium","Corynebacterium","Micrococcus","Lactobacillus","Lactococcus","Aeromonas","Bordetella","Brucella","Chlamydia","Legionella","Mycoplasma","Rickettsia","Treponema","Stenotrophomonas","Prevotella","Porphyromonas","Fusobacterium","Bacteroides","Cutibacterium","Propionibacterium","Eikenella","Actinobacillus","Pasteurella","Edwardsiella","Photobacterium","Stenotrophomonas","VRE","MRSA"}
DOI_RE=re.compile(r"(?i)\b(10\.\d{4,9}/[0-9A-Z._;()/:+-]+)")
PMID_RE=re.compile(r"(?i)\b(?:PMID|PubMed\s*ID|pubmed/?)\s*[:=#]?\s*(\d{5,9})\b")
PMCID_RE=re.compile(r"(?i)\b(PMC\s*\d{4,9})\b")
TARGET_RE=re.compile(r"\b([A-Z][a-z]{2,}(?:ium|ella|ella|ococcus|coccus|obacter|monas|bacter|streptococcus|staphylococcus|bacillus|vibrio|clostridium|escherichia|pseudomonas|klebsiella|enterococcus|salmonella|listeria|acinetobacter|proteus|serratia|shigella|campylobacter|helicobacter|neisseria|burkholderia|mycobacterium|corynebacterium|lactobacillus|lactococcus|bacteroides|prevotella|porphyromonas|fusobacterium))\s+([a-z][a-z-]+)\b")
ASSAY_PATTERNS=[("MIC",re.compile(r"(?i)\b(?:MIC|minimum inhibitory concentration)\b")),("inhibition_zone",re.compile(r"(?i)\b(?:inhibition zone|zone of inhibition|agar diffusion|disk diffusion)\b")),("time_kill",re.compile(r"(?i)\b(?:time[- ]kill|time-kill assay)\b")),("CFU",re.compile(r"(?i)\b(?:CFU|colony[- ]forming units)\b")),("MBC",re.compile(r"(?i)\b(?:MBC|minimum bactericidal concentration)\b"))]
NUM_RE=re.compile(r"(?i)(?:MIC|MBC|inhibition\s+zone|zone\s+of\s+inhibition)[^.;\n]{0,80}?([<>≤≥]?\s*\d+(?:\.\d+)?)\s*(µg\s*/\s*mL|ug\s*/\s*mL|μg\s*/\s*mL|mg\s*/\s*L|mm|µM|uM)?")

def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1024*1024),b""):h.update(b)
 return h.hexdigest()
def fetch_drive(fid,path):
 import gdown
 got=gdown.download(f"https://drive.google.com/uc?id={fid}",str(path),quiet=True,fuzzy=True)
 if not got or not path.exists() or not path.stat().st_size:raise RuntimeError(f"Drive input download failed: {path.name}")
def fasta_map(path):
 out={}; sid=None; chunks=[]
 def flush():
  if sid is not None:
   seq="".join(chunks).replace(" ","").upper()
   if not seq:raise RuntimeError(f"Empty sequence for {sid}")
   if sid in out:raise RuntimeError(f"Duplicate FASTA ID {sid}")
   out[sid]=seq
 with open(path,encoding="utf-8") as f:
  for n,line in enumerate(f,1):
   line=line.strip()
   if not line:continue
   if line.startswith(">"):
    flush();sid=line[1:].split()[0];chunks=[]
   else:
    if sid is None:raise RuntimeError(f"Sequence before header at line {n}")
    chunks.append(line)
 flush();return out
def tsv(path):
 with open(path,encoding="utf-8-sig",newline="") as f:
  r=csv.DictReader(f,delimiter="\t")
  if not r.fieldnames:raise RuntimeError(f"Missing header: {path.name}")
  return list(r)
def find_id(row):return (row.get("Sequence_ID") or "").strip()
def citations(row):
 blob=" ".join((row.get(k) or "") for k in ("Citation_Candidates","References","Reference","PMID","DOI","PMCID"))
 dois=[]
 for d in DOI_RE.findall(blob):
  d=d.rstrip(".,;:)]}")
  if d not in dois:dois.append(d)
 pmids=PMID_RE.findall(blob)
 # Citation_Candidates is a project-generated field whose source values are numeric
 # literature identifiers. Accept 7-9 digit tokens from this field only; validate
 # each against PubMed before using it as a traceable PMID.
 pmids.extend(re.findall(r"(?<![A-Z0-9])(\d{7,9})(?!\d)",row.get("Citation_Candidates") or "",flags=re.I))
 pmids=list(dict.fromkeys(pmids))
 pmcids=list(dict.fromkeys(re.sub(r"\s+","",x).upper() for x in PMCID_RE.findall(blob)))
 return dois,pmids,pmcids
def norm_mic(text):
 m=NUM_RE.search(text)
 if not m:return "",""
 return m.group(1).replace(" ",""), (m.group(2) or "").replace(" ","")
def sequence_in(text,seq):
 # Permit whitespace/hyphen separators; demand amino-acid boundaries to avoid prose matches.
 pattern=r"(?<![A-Z])"+r"[\s\-]*".join(map(re.escape,seq))+r"(?![A-Z])"
 return re.search(pattern,text.upper()) is not None
def target_in(text):
 upper=text
 found=[]
 for m in TARGET_RE.finditer(text):
  genus,species=m.group(1),m.group(2)
  if genus in BACTERIAL_GENUS:
   nm=f"{genus} {species}"
   if nm not in found:found.append(nm)
 for alias in ["E. coli","Escherichia coli","S. aureus","Staphylococcus aureus","P. aeruginosa","Pseudomonas aeruginosa","B. subtilis","Bacillus subtilis","K. pneumoniae","Klebsiella pneumoniae","E. faecalis","Enterococcus faecalis","S. epidermidis","Staphylococcus epidermidis","S. typhimurium","Salmonella typhimurium","A. baumannii","Acinetobacter baumannii"]:
  if re.search(re.escape(alias),upper,re.I) and alias not in found:found.append(alias)
 return found
def parse_bioc(payload):
 if isinstance(payload,dict):docs=payload.get("documents",[])
 elif isinstance(payload,list):docs=payload
 else:return []
 out=[]
 for doc in docs:
  docid=doc.get("id","")
  for p in doc.get("passages",[]):
   text=p.get("text","")
   if text:out.append({"docid":docid,"text":text,"infons":p.get("infons",{}),"offset":p.get("offset","")})
 return out
class NCBI:
 def __init__(self):
  import requests
  self.s=requests.Session();self.s.headers.update({"User-Agent":"DualAMP-v2-evidence-harvest/1.0 (automated conservative public literature access)"});self.last=0.;self.cache={}
 def get(self,url,params=None):
  wait=0.36-(time.time()-self.last)
  if wait>0:time.sleep(wait)
  self.last=time.time()
  for attempt in range(4):
   try:
    r=self.s.get(url,params=params,timeout=35)
    if r.status_code==429 or r.status_code>=500:time.sleep(1.5*(attempt+1));continue
    r.raise_for_status();return r
   except Exception:
    if attempt==3:return None
    time.sleep(0.8*(attempt+1))
  return None
 def valid_pmid(self,pmid):
  key=("valid_pmid",pmid)
  if key in self.cache:return self.cache[key]
  r=self.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",{"db":"pubmed","id":pmid,"retmode":"json"})
  valid=False
  if r:
   try:
    data=r.json().get("result",{});valid=pmid in data.get("uids",[]) and data.get(pmid,{}).get("uid")==pmid
   except Exception:pass
  self.cache[key]=valid;return valid
 def pmid_for_doi(self,doi):
  key=("doi",doi.lower())
  if key in self.cache:return self.cache[key]
  r=self.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",{"db":"pubmed","term":f'"{doi}"[DOI]',"retmode":"json","retmax":3})
  ids=[]
  if r:
   try:ids=r.json().get("esearchresult",{}).get("idlist",[])
   except Exception:pass
  result=ids[0] if ids else "";self.cache[key]=result;return result
 def pmcid_for_pmid(self,pmid):
  key=("pmc",pmid)
  if key in self.cache:return self.cache[key]
  r=self.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi",{"dbfrom":"pubmed","id":pmid,"db":"pmc","retmode":"json"})
  ids=[]
  if r:
   try:
    for s in r.json().get("linksets",[]):
     for l in s.get("linksetdbs",[]):ids.extend(l.get("links",[]))
   except Exception:pass
  result="PMC"+ids[0] if ids else "";self.cache[key]=result;return result
 def abstract(self,pmid):
  key=("abstract",pmid)
  if key in self.cache:return self.cache[key]
  r=self.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",{"db":"pubmed","id":pmid,"retmode":"xml"})
  if not r:self.cache[key]="";return ""
  try:
   import xml.etree.ElementTree as ET
   root=ET.fromstring(r.text)
   value=" ".join(x.text or "" for x in root.findall(".//Abstract/AbstractText"))
  except Exception:value=""
  self.cache[key]=value;return value
 def fulltext(self,pmcid):
  key=("fulltext",pmcid)
  if key in self.cache:return self.cache[key]
  url=f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode"
  r=self.get(url)
  passages=[]
  if r:
   try:passages=parse_bioc(r.json())
   except Exception:pass
  self.cache[key]=passages;return passages
 def europe_fulltext(self,pmid):
  key=("europe_fulltext",pmid)
  if key in self.cache:return self.cache[key]
  r=self.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",{"query":f"EXT_ID:{pmid}","format":"json","pageSize":1})
  passages=[];pmcid=""
  if r:
   try:
    results=r.json().get("resultList",{}).get("result",[])
    if results:pmcid=results[0].get("pmcid","") or ""
   except Exception:pass
  if pmcid:
   url=f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
   x=self.get(url)
   if x:
    try:
     import xml.etree.ElementTree as ET
     root=ET.fromstring(x.text);parents={child:parent for parent in root.iter() for child in parent}
     def tag(e):return e.tag.rsplit("}",1)[-1].lower()
     def in_abstract(e):
      p=e
      while p in parents:
       p=parents[p]
       if tag(p)=="abstract":return True
      return False
     seen=set()
     for e in root.iter():
      t=tag(e)
      if t=="table-wrap":
       text=" ".join("".join(e.itertext()).split())
       if text:passages.append({"docid":pmcid,"text":text,"infons":{"type":"table","section_type":"table"},"offset":""})
      elif t=="p" and not in_abstract(e):
       text=" ".join("".join(e.itertext()).split())
       if text and text not in seen:seen.add(text);passages.append({"docid":pmcid,"text":text,"infons":{"type":"paragraph","section_type":"body"},"offset":""})
    except Exception:pass
  self.cache[key]=(pmcid,passages);return pmcid,passages
 def resolve(self,kind,ident):
  key=("resolve",kind,ident)
  if key in self.cache:return self.cache[key]
  doi=ident if kind=="doi" else "";pmid=ident if kind=="pmid" else "";pmcid=ident if kind=="pmcid" else ""
  if doi and not pmid:pmid=self.pmid_for_doi(doi)
  if pmid and not self.valid_pmid(pmid):pmid=""
  if pmid and not pmcid:pmcid=self.pmcid_for_pmid(pmid)
  value=(doi,pmid,pmcid);self.cache[key]=value;return value

def base_row(sid,doi="",pmid="",pmcid="",url="",level="NONE",match="UNRESOLVED",endpoint="UNKNOWN",target="",assay="",mic="",unit="",activity="UNKNOWN",loc="",access="NONE",reason=""):
 return {"Sequence_ID":sid,"Evidence_ID":pmcid or pmid or doi,"DOI":doi,"PMID":pmid,"PMCID":pmcid,"Source_URL":url,"Evidence_Level":level,"Sequence_Match":match,"Endpoint_Class":endpoint,"Bacterial_Target":target,"Assay_Type":assay,"MIC_Value":mic,"MIC_Unit":unit,"Activity_Result":activity,"Evidence_Location":loc,"Fulltext_Access":access,"Conflict_Flag":"false","Conflict_Reason":"","Rule_Version":RULE,"Decision_Timestamp":dt.datetime.now(dt.timezone.utc).isoformat(),"Auto_Reason":reason}
def extract_ref(sid,seq,doi,pmid,pmcid,ncbi):
 if doi and not pmid:pmid=ncbi.pmid_for_doi(doi)
 if not pmcid and pmid:pmcid=ncbi.pmcid_for_pmid(pmid)
 url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/" if pmcid else (f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (f"https://doi.org/{doi}" if doi else ""))
 passages=ncbi.fulltext(pmcid) if pmcid else []
 if not passages and pmid:
  e_pmcid,e_passages=ncbi.europe_fulltext(pmid)
  if e_passages:
   pmcid=e_pmcid or pmcid;passages=e_passages;url=f"https://europepmc.org/articles/{pmcid}"
 hits=[]
 for p in passages:
  text=p["text"];inf=p["infons"] or {}; typ=str(inf.get("type", "")); sec=str(inf.get("section_type",inf.get("section", "")))
  if not sequence_in(text,seq):continue
  targets=target_in(text)
  assay=next((name for name,rx in ASSAY_PATTERNS if rx.search(text)),"")
  mic,unit=norm_mic(text)
  abstract_only="abstract" in typ.lower() or "abstract" in sec.lower() or typ.lower()=="title"
  positive=bool(not abstract_only and assay and targets and (mic or re.search(r"(?i)\b(?:inhibit(?:ed|ion|s)?|antibacterial|bactericidal|killing|susceptible)\b",text)))
  table="table" in typ.lower() or "table" in sec.lower()
  access="FULLTEXT_TABLE" if table else "FULLTEXT"
  level="FULLTEXT_EXPERIMENTAL_TABLE" if table and assay else "FULLTEXT_EXPERIMENTAL" if assay else "FULLTEXT_EXPERIMENTAL"
  loc=f"{pmcid} BioC passage type={typ or 'text'} section={sec or 'unspecified'} offset={p['offset']}"
  hits.append(base_row(sid,doi,pmid,pmcid,url,level,"EXACT","BACTERIAL" if targets else "UNKNOWN", "; ".join(targets),assay,mic,unit,"POSITIVE" if positive else "UNKNOWN",loc,access,"Exact sequence present in full-text passage; positive only if same passage also has named bacterial target and experimental assay/readout."))
  if positive:return hits[-1]
 if passages:
  # Full text fetched but exact sequence not co-located with bacterial assay; retain nonqualifying fulltext evidence row.
  any_seq=any(sequence_in(p["text"],seq) for p in passages)
  return base_row(sid,doi,pmid,pmcid,url,"FULLTEXT_EXPERIMENTAL" if any_seq else "FULLTEXT_UNVERIFIED", "EXACT" if any_seq else "UNRESOLVED", "UNKNOWN","","","","","UNKNOWN","", "FULLTEXT", "Full text available but no single passage established all strict evidence fields.")
 abstract=ncbi.abstract(pmid) if pmid else ""
 if abstract:
  match="EXACT" if sequence_in(abstract,seq) else "UNRESOLVED"
  return base_row(sid,doi,pmid,pmcid,url,"ABSTRACT_ONLY",match,"UNKNOWN","","","","","UNKNOWN","","ABSTRACT_ONLY","PubMed abstract only; cannot establish strict full-text experimental evidence.")
 if pmcid:return base_row(sid,doi,pmid,pmcid,url,"FULLTEXT_UNAVAILABLE","UNRESOLVED","UNKNOWN","","","","","UNKNOWN","","UNAVAILABLE","PMC link found but full-text endpoint unavailable.")
 return base_row(sid,doi,pmid,pmcid,url,"NONE","UNRESOLVED","UNKNOWN","","","","","UNKNOWN","","NO_FULLTEXT","No PMCID/full text resolvable from available citation identifier.")
def upload(uri,path):
 import requests
 size=path.stat().st_size; offset=0; last=None
 with path.open("rb") as f:
  while offset<size:
   chunk=f.read(8*1024*1024);final=offset+len(chunk)>=size
   h={"Content-Type":"text/tab-separated-values","Content-Length":str(len(chunk)),"X-Goog-Upload-Offset":str(offset),"X-Goog-Upload-Command":"upload, finalize" if final else "upload","X-Goog-Upload-Total-Size":str(size)}
   r=requests.put(uri,headers=h,data=chunk,timeout=180)
   if r.status_code not in (200,201):raise RuntimeError(f"Drive private ledger upload failed: HTTP {r.status_code}")
   last=r;offset+=len(chunk)
 return {"bytes":size,"sha256":sha(path),"response":last.json() if last and "json" in last.headers.get("Content-Type","") else {}}
def main():
 work=Path(tempfile.mkdtemp(prefix="dualamp-v2-literature-",dir="/tmp"));out=Path("/kaggle/working");out.mkdir(parents=True,exist_ok=True)
 report={"stage":"full_text_evidence_harvest","status":"running","rule_version":RULE,"started_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"training_started":False,"note":"Row-level ledger is uploaded to private Drive; no raw sequences or evidence rows are returned through relay."}
 try:
  miss=[x for x,e in INPUTS.items() if not (os.environ.get(e) or "").strip()]
  if miss:raise RuntimeError("missing Drive source IDs: "+", ".join(miss))
  fp=work/"candidate.fasta";mp=work/"metadata.tsv";ap=work/"source_audit.tsv"
  for p,k in ((fp,"fasta"),(mp,"metadata"),(ap,"audit")):fetch_drive(os.environ[INPUTS[k]].strip(),p)
  seqs=fasta_map(fp);metadata=tsv(mp);audit=tsv(ap)
  mids=[find_id(r) for r in metadata]
  if len(mids)!=len(set(mids)) or set(mids)!=set(seqs):raise RuntimeError("candidate FASTA/metadata IDs are not one-to-one closed")
  meta={find_id(r):r for r in metadata};byid=defaultdict(list)
  for r in audit:
   sid=find_id(r)
   if sid:byid[sid].append(r)
  candidate_audit={sid:byid.get(sid,[]) for sid in seqs}
  refs_by_id={};all_refs=[]
  for sid,rows in candidate_audit.items():
   refs=[]
   for r in rows:
    ds,ps,cs=citations(r)
    for d in ds:refs.append(("doi",d))
    for p in ps:refs.append(("pmid",p))
    for c in cs:refs.append(("pmcid",c))
   dedup=list(dict.fromkeys(refs));refs_by_id[sid]=dedup;all_refs.extend(dedup)
  unique_refs=list(dict.fromkeys(all_refs));ncbi=NCBI();ledger=[];refcache={};ref_success=Counter();
  for n,sid in enumerate(sorted(seqs),1):
   refs=refs_by_id.get(sid,[])
   if not refs:
    ledger.append(base_row(sid,reason="No DOI/PMID/PMCID identifier parsed from available source citation fields."));continue
   # Limit redundant retrievals by caching literature evidence by source identifier; sequence checks remain per candidate.
   for kind,ident in refs:
    key=(kind,ident)
    if key not in refcache:refcache[key]=ncbi.resolve(kind,ident)
    doi,pmid,pmcid=refcache[key]
    ledger.append(extract_ref(sid,seqs[sid],doi,pmid,pmcid,ncbi))
   ref_success["candidate_ids_with_citations"]+=1
   if n%100==0:print(f"harvested candidates {n}/{len(seqs)}; unique citation identifiers={len(unique_refs)}",flush=True)
  # Unique rows while preserving multiple references per candidate.
  seen=set();dedup=[]
  for r in ledger:
   k=(r["Sequence_ID"],r["DOI"],r["PMID"],r["PMCID"],r["Evidence_Location"])
   if k not in seen:seen.add(k);dedup.append(r)
  ledger_path=work/"evidence_ledger.tsv"
  with ledger_path.open("w",encoding="utf-8",newline="") as f:
   w=csv.DictWriter(f,fieldnames=COLS,delimiter="\t",lineterminator="\n");w.writeheader();w.writerows(dedup)
  covered={r["Sequence_ID"] for r in dedup};counts=Counter(r["Evidence_Level"] for r in dedup)
  strict_candidates={r["Sequence_ID"] for r in dedup if r["Evidence_Level"] in {"FULLTEXT_EXPERIMENTAL","FULLTEXT_EXPERIMENTAL_TABLE"} and r["Sequence_Match"]=="EXACT" and r["Endpoint_Class"]=="BACTERIAL" and r["Bacterial_Target"] and r["Assay_Type"] and r["Activity_Result"]=="POSITIVE" and r["Fulltext_Access"] in {"FULLTEXT","FULLTEXT_TABLE"} and r["Evidence_Location"] and (r["DOI"] or r["PMID"] or r["PMCID"])}
  report.update({"status":"LEDGER_GENERATED_AND_UPLOADED_PRIVATE","candidate_fasta_records":len(seqs),"metadata_rows":len(metadata),"source_audit_rows":len(audit),"source_audit_ids_intersecting_candidates":sum(bool(candidate_audit[s]) for s in seqs),"source_audit_rows_for_candidate_ids":sum(len(candidate_audit[s]) for s in seqs),"candidate_ids_with_citation_identifiers":sum(bool(refs_by_id[s]) for s in seqs),"unique_citation_identifiers":len(unique_refs),"ledger_rows":len(dedup),"ledger_candidate_coverage":len(covered),"candidate_ids_without_ledger_coverage":len(set(seqs)-covered),"evidence_level_counts":dict(sorted(counts.items())),"strict_rule_candidate_ids_draft":len(strict_candidates),"ledger_sha256":sha(ledger_path),"ledger_bytes":ledger_path.stat().st_size,"private_drive_upload":upload((os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip(),ledger_path),"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"training_started":False})
  (out/"literature_harvest_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2))
 except Exception as e:
  report.update({"status":"FAILED","error":str(e),"traceback":traceback.format_exc(limit=5),"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"training_started":False})
  (out/"literature_harvest_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(report,ensure_ascii=False,indent=2),file=sys.stderr);raise
 finally:shutil.rmtree(work,ignore_errors=True)
if __name__=="__main__":main()
