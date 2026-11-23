#!/usr/bin/env python3
"""Validate a full-coverage literature evidence ledger and freeze non-lossy v2 views.

This tool does NOT retrieve papers or decide scientific evidence from prose. It is a
fail-closed rules/coverage gate for a separate, auditable evidence ledger. No human
signature is fabricated. All candidate IDs must appear exactly once in metadata and
at least once in the evidence ledger; only an explicit, full-text experimental
bacterial-positive record with a sequence match can enter the strict set.

Usage:
  python freeze_auto_evidence_v2.py freeze \
    --fasta positive_core_candidate_v1.fasta \
    --metadata positive_core_candidate_v1_metadata.tsv \
    --evidence evidence_ledger.tsv --outdir positive_core_v2_auto
  python freeze_auto_evidence_v2.py selftest

Input evidence ledger: one row per evidence item (multiple rows per Sequence_ID are
allowed), columns declared in EVIDENCE_COLUMNS below. The ledger must preserve the
paper-level evidence location and the matching rationale. A candidate with no
retrievable evidence is still represented by one row with Evidence_ID empty and
Evidence_Level=NONE; this ensures closed-world row coverage without turning it into
an experimental negative.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

DECISIONS = (
    "AUTO_CONFIRMED_ANTIBACTERIAL",
    "AUTO_EXCLUDED_NONBACTERIAL",
    "AUTO_UNRESOLVED_NO_EVIDENCE",
    "AUTO_SEQUENCE_MISMATCH",
    "AUTO_CONFLICT",
)
EVIDENCE_COLUMNS = (
    "Sequence_ID", "Evidence_ID", "DOI", "PMID", "PMCID", "Source_URL",
    "Evidence_Level", "Sequence_Match", "Endpoint_Class", "Bacterial_Target",
    "Assay_Type", "MIC_Value", "MIC_Unit", "Activity_Result",
    "Evidence_Location", "Fulltext_Access", "Conflict_Flag", "Conflict_Reason",
    "Rule_Version", "Decision_Timestamp", "Auto_Reason",
)
FULLTEXT_LEVELS = {"FULLTEXT_EXPERIMENTAL", "FULLTEXT_EXPERIMENTAL_TABLE"}
MATCH_OK = {"EXACT", "VERIFIED_DERIVATIVE"}
ACTIVITY_POSITIVE = {"POSITIVE"}
TRUE = {"1", "true", "yes", "y"}
FALSE = {"0", "false", "no", "n", ""}

class GateError(Exception):
    pass

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def read_tsv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            raise GateError(f"{path}: missing TSV header")
        return reader.fieldnames, list(reader)

def read_fasta(path: Path):
    records, name, chunks = [], None, []
    def emit():
        if name is None: return
        sid = name.split()[0]
        seq = "".join(chunks).replace(" ", "").upper()
        if not sid or not seq:
            raise GateError(f"{path}: empty ID/sequence around FASTA record {name!r}")
        records.append({"Sequence_ID": sid, "Header": name, "Sequence": seq})
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.strip()
            if not line: continue
            if line.startswith(">"):
                emit(); name = line[1:].strip(); chunks = []
            else:
                if name is None: raise GateError(f"{path}:{n}: sequence before first FASTA header")
                chunks.append(line)
    emit()
    ids = [r["Sequence_ID"] for r in records]
    if len(ids) != len(set(ids)):
        dup = [k for k, n in Counter(ids).items() if n > 1]
        raise GateError(f"FASTA has duplicate Sequence_ID values: {dup[:10]}")
    return records

def require_columns(path: Path, cols, expected):
    missing = [c for c in expected if c not in cols]
    if missing: raise GateError(f"{path}: missing required columns {missing}")

def truth(value):
    v = (value or "").strip().lower()
    if v in TRUE: return True
    if v in FALSE: return False
    raise GateError(f"invalid boolean value {value!r}; use true/false")

def idmap(rows, label):
    result = {}
    for r in rows:
        sid = (r.get("Sequence_ID") or "").strip()
        if not sid: raise GateError(f"{label}: blank Sequence_ID")
        if sid in result: raise GateError(f"{label}: duplicate Sequence_ID {sid}")
        result[sid] = r
    return result

def has_traceable_reference(row):
    doi = (row.get("DOI") or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    pmid = (row.get("PMID") or "").strip()
    pmcid = (row.get("PMCID") or "").strip()
    return bool(re.fullmatch(r"10\.\d{4,9}/\S+", doi, flags=re.I)
                or re.fullmatch(r"\d+", pmid)
                or re.fullmatch(r"PMC\d+", pmcid, flags=re.I))

def decide_group(sid, evidence_rows):
    """Apply conservative record-level rules to already-extracted evidence fields."""
    if not evidence_rows:
        return "AUTO_UNRESOLVED_NO_EVIDENCE", "No evidence rows"
    conflicts = [r for r in evidence_rows if truth(r.get("Conflict_Flag"))]
    if conflicts:
        reasons = sorted({(r.get("Conflict_Reason") or "flagged evidence conflict").strip() for r in conflicts})
        return "AUTO_CONFLICT", "; ".join(x for x in reasons if x)

    qualifying = []
    for r in evidence_rows:
        full = (r.get("Fulltext_Access") or "").strip().upper() in {"FULLTEXT", "FULLTEXT_TABLE"}
        level = (r.get("Evidence_Level") or "").strip().upper() in FULLTEXT_LEVELS
        match = (r.get("Sequence_Match") or "").strip().upper() in MATCH_OK
        endpoint = (r.get("Endpoint_Class") or "").strip().upper() == "BACTERIAL"
        target = bool((r.get("Bacterial_Target") or "").strip())
        assay = bool((r.get("Assay_Type") or "").strip())
        result = (r.get("Activity_Result") or "").strip().upper() in ACTIVITY_POSITIVE
        loc = bool((r.get("Evidence_Location") or "").strip())
        ref = has_traceable_reference(r)
        if full and level and match and endpoint and target and assay and result and loc and ref:
            qualifying.append(r)
    if qualifying:
        refs = sorted({(r.get("DOI") or r.get("PMID") or r.get("Evidence_ID") or "").strip() for r in qualifying})
        return "AUTO_CONFIRMED_ANTIBACTERIAL", "Qualifying full-text experimental bacterial-positive evidence: " + ";".join(refs)

    # A mismatch is an explicit sequence/link failure, never a negative label.
    if any((r.get("Sequence_Match") or "").strip().upper() in {"MISMATCH", "UNRESOLVED_MISMATCH"} for r in evidence_rows):
        return "AUTO_SEQUENCE_MISMATCH", "Evidence sequence does not match candidate or identity cannot be reconciled"

    usable = [r for r in evidence_rows
              if (r.get("Fulltext_Access") or "").strip().upper() in {"FULLTEXT", "FULLTEXT_TABLE"}
              and (r.get("Evidence_Level") or "").strip().upper() in FULLTEXT_LEVELS
              and (r.get("Sequence_Match") or "").strip().upper() in MATCH_OK
              and (r.get("Evidence_Location") or "").strip()
              and has_traceable_reference(r)]
    if usable and all((r.get("Endpoint_Class") or "").strip().upper() == "NONBACTERIAL" for r in usable):
        return "AUTO_EXCLUDED_NONBACTERIAL", "Retrieved full-text evidence supports only non-bacterial endpoints; excluded from antibacterial task, not labeled experimental negative"
    return "AUTO_UNRESOLVED_NO_EVIDENCE", "No record met the strict evidence criteria; unresolved is not a negative label"

def write_tsv(path: Path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        w.writeheader(); w.writerows(rows)

def write_fasta(path: Path, records):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(f">{r['Sequence_ID']}\n")
            seq = r["Sequence"]
            for i in range(0, len(seq), 60): f.write(seq[i:i+60] + "\n")

def freeze(args):
    fasta_path, meta_path, evidence_path = map(Path, (args.fasta, args.metadata, args.evidence))
    records = read_fasta(fasta_path)
    mcols, mrows = read_tsv(meta_path)
    require_columns(meta_path, mcols, ["Sequence_ID"])
    metas = idmap(mrows, "metadata")
    fasta = {r["Sequence_ID"]: r for r in records}
    if set(metas) != set(fasta):
        only_meta = sorted(set(metas)-set(fasta)); only_fa = sorted(set(fasta)-set(metas))
        raise GateError(f"metadata/FASTA ID sets differ; metadata-only={len(only_meta)} {only_meta[:5]}, FASTA-only={len(only_fa)} {only_fa[:5]}")
    ecols, erows = read_tsv(evidence_path)
    require_columns(evidence_path, ecols, EVIDENCE_COLUMNS)
    missing_id = [i for i,r in enumerate(erows,2) if not (r.get("Sequence_ID") or "").strip()]
    if missing_id: raise GateError(f"evidence ledger has blank Sequence_ID at lines {missing_id[:10]}")
    outside = sorted({r["Sequence_ID"].strip() for r in erows} - set(fasta))
    if outside: raise GateError(f"evidence ledger has IDs absent from FASTA: {outside[:10]}")
    grouped = defaultdict(list)
    for r in erows:
        # Validate explicit conflict flags; malformed boolean values fail closed.
        truth(r.get("Conflict_Flag")); grouped[r["Sequence_ID"].strip()].append(r)
    missing = sorted(set(fasta)-set(grouped))
    if missing: raise GateError(f"full-candidate evidence coverage failed: {len(missing)} FASTA IDs absent from ledger, e.g. {missing[:10]}")

    decisions = {}
    for sid, evs in grouped.items(): decisions[sid] = decide_group(sid, evs)
    count = Counter(d[0] for d in decisions.values())
    if sum(count.values()) != len(fasta) or set(decisions) != set(fasta):
        raise GateError("decision accounting does not close over the candidate FASTA")

    # Group exact duplicate sequences without discarding their record/source mappings.
    seq_groups = defaultdict(list)
    for sid, r in fasta.items(): seq_groups[r["Sequence"]].append(sid)
    group_id = {sid: "SEQ_" + hashlib.sha256(seq.encode()).hexdigest()[:16]
                for seq, ids in seq_groups.items() for sid in ids}
    status_rows = []
    for sid in sorted(fasta):
        d, reason = decisions[sid]
        status_rows.append({"Sequence_ID":sid, "Auto_Decision":d, "Auto_Reason":reason,
                            "Exact_Sequence_Group_ID":group_id[sid], "Sequence_Length":len(fasta[sid]["Sequence"]),
                            **metas[sid]})

    # FASTA outputs are unique-sequence representatives; TSVs retain every source record.
    strict_ids = [sid for sid in sorted(fasta) if decisions[sid][0] == "AUTO_CONFIRMED_ANTIBACTERIAL"]
    relaxed_ids = [sid for sid in sorted(fasta)
                   if decisions[sid][0] == "AUTO_CONFIRMED_ANTIBACTERIAL"
                   or (decisions[sid][0] == "AUTO_UNRESOLVED_NO_EVIDENCE"
                       and bool((metas[sid].get("Activity_Description") or "").strip()))]
    def unique_records(ids):
        seen, out = set(), []
        for sid in ids:
            seq = fasta[sid]["Sequence"]
            if seq in seen: continue
            seen.add(seq); out.append(fasta[sid])
        return out
    strict_records, relaxed_records = unique_records(strict_ids), unique_records(relaxed_ids)
    outdir = Path(args.outdir)
    if outdir.exists() and any(outdir.iterdir()) and not args.force:
        raise GateError(f"refusing to overwrite non-empty output directory {outdir}; use --force")
    outdir.mkdir(parents=True, exist_ok=True)

    status_by = {r["Sequence_ID"]: r for r in status_rows}
    write_fasta(outdir/"positive_core_v2.fasta", strict_records)
    write_tsv(outdir/"positive_core_v2.tsv", ["Sequence_ID","Auto_Decision","Auto_Reason","Exact_Sequence_Group_ID","Sequence_Length",*mcols],
              [status_by[s] for s in strict_ids])
    write_tsv(outdir/"positive_core_v2_evidence.tsv", ecols,
              [r for r in erows if decisions[r["Sequence_ID"].strip()][0] == "AUTO_CONFIRMED_ANTIBACTERIAL"])
    write_fasta(outdir/"positive_relaxed_v2.fasta", relaxed_records)
    write_tsv(outdir/"positive_relaxed_v2.tsv", ["Sequence_ID","Auto_Decision","Auto_Reason","Exact_Sequence_Group_ID","Sequence_Length",*mcols],
              [status_by[s] for s in relaxed_ids])
    for decision, filename in [
        ("AUTO_UNRESOLVED_NO_EVIDENCE","unresolved_v2.tsv"),
        ("AUTO_EXCLUDED_NONBACTERIAL","excluded_non_antibacterial_v2.tsv"),
        ("AUTO_SEQUENCE_MISMATCH","sequence_mismatch_v2.tsv"),
        ("AUTO_CONFLICT","conflict_v2.tsv")]:
        write_tsv(outdir/filename, ["Sequence_ID","Auto_Decision","Auto_Reason","Exact_Sequence_Group_ID","Sequence_Length",*mcols],
                  [status_by[s] for s in sorted(fasta) if decisions[s][0] == decision])
    write_tsv(outdir/"source_map_v2.tsv", ["Sequence_ID","Exact_Sequence_Group_ID","Auto_Decision","Source_Sequence_IDs"],
              [{"Sequence_ID":sid,"Exact_Sequence_Group_ID":group_id[sid],"Auto_Decision":decisions[sid][0],
                "Source_Sequence_IDs":";".join(sorted(seq_groups[fasta[sid]["Sequence"]]))} for sid in sorted(fasta)])
    # Preserve the machine evidence ledger as an immutable input snapshot in the output bundle.
    write_tsv(outdir/"evidence_ledger.tsv", ecols, erows)

    outputs = sorted(p for p in outdir.iterdir() if p.is_file() and p.name != "dataset_manifest.json")
    manifest = {
        "schema_version":"auto_evidence_v2_1.0",
        "rule_version":args.rule_version,
        "state":"DATASET_FROZEN_BY_RULES_NOT_PI_SIGNED",
        "input_files":{p.name:{"sha256":sha256(p),"bytes":p.stat().st_size} for p in [fasta_path,meta_path,evidence_path]},
        "output_files":{p.name:{"sha256":sha256(p),"bytes":p.stat().st_size} for p in outputs},
        "input_candidate_records":len(fasta),
        "input_unique_sequences":len(seq_groups),
        "evidence_rows":len(erows),
        "decision_counts":dict(sorted(count.items())),
        "strict_confirmed_records":len(strict_ids),
        "strict_unique_sequences":len(strict_records),
        "relaxed_records":len(relaxed_ids),
        "relaxed_unique_sequences":len(relaxed_records),
        "note":"This manifest records deterministic rule-based classification of an evidence ledger. It is not a PI signature, wet-lab validation, or authorization to publish clinical/efficacy claims. Unresolved records are not negatives. Exact sequence duplicates are represented once in FASTA and fully mapped in TSV.",
    }
    (outdir/"dataset_manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    return 0

def selftest():
    import tempfile
    from types import SimpleNamespace
    tmp=Path(tempfile.mkdtemp(prefix="auto_evidence_v2_test_"))
    fa=tmp/"in.fasta"; meta=tmp/"meta.tsv"; ledger=tmp/"evidence.tsv"; out=tmp/"out"
    fa.write_text(">P1\nACDEFG\n>P2\nACDEFG\n>P3\nKLMNPQ\n>P4\nRSTVWY\n>P5\nGHIKLM\n",encoding="utf-8")
    meta.write_text("Sequence_ID\tSource_DB\nP1\tDB1\nP2\tDB2\nP3\tDB1\nP4\tDB1\nP5\tDB1\n",encoding="utf-8")
    cols=list(EVIDENCE_COLUMNS)
    rows=[]
    def row(sid, **kw):
        x={c:"" for c in cols}; x.update({"Sequence_ID":sid,"Rule_Version":"test-1","Decision_Timestamp":"2026-11-12T00:00:00Z","Conflict_Flag":"false"}); x.update(kw); rows.append(x)
    row("P1",Evidence_ID="E1",DOI="10.1234/x",Evidence_Level="FULLTEXT_EXPERIMENTAL",Sequence_Match="EXACT",Endpoint_Class="BACTERIAL",Bacterial_Target="E. coli",Assay_Type="MIC",Activity_Result="POSITIVE",Evidence_Location="Table 2",Fulltext_Access="FULLTEXT")
    row("P2",Evidence_ID="E2",DOI="10.1234/x",Evidence_Level="FULLTEXT_EXPERIMENTAL",Sequence_Match="EXACT",Endpoint_Class="BACTERIAL",Bacterial_Target="E. coli",Assay_Type="MIC",Activity_Result="POSITIVE",Evidence_Location="Table 2",Fulltext_Access="FULLTEXT")
    row("P3",Evidence_ID="E3",PMID="123",Evidence_Level="FULLTEXT_EXPERIMENTAL",Sequence_Match="EXACT",Endpoint_Class="NONBACTERIAL",Activity_Result="NONBACTERIAL",Evidence_Location="Figure 1",Fulltext_Access="FULLTEXT")
    row("P4",Evidence_ID="E4",PMID="124",Evidence_Level="FULLTEXT_EXPERIMENTAL",Sequence_Match="MISMATCH",Endpoint_Class="BACTERIAL",Activity_Result="POSITIVE",Evidence_Location="Table 1",Fulltext_Access="FULLTEXT")
    row("P5",Evidence_ID="E5",PMID="125",Evidence_Level="ABSTRACT_ONLY",Sequence_Match="UNRESOLVED",Endpoint_Class="UNKNOWN",Activity_Result="UNKNOWN",Auto_Reason="abstract only")
    write_tsv(ledger,cols,rows)
    ns=SimpleNamespace(fasta=str(fa),metadata=str(meta),evidence=str(ledger),outdir=str(out),force=False,rule_version="test-1")
    freeze(ns)
    manifest=json.loads((out/"dataset_manifest.json").read_text())
    assert manifest["input_candidate_records"]==5
    assert manifest["strict_confirmed_records"]==2 and manifest["strict_unique_sequences"]==1
    assert manifest["relaxed_records"]==2 and manifest["relaxed_unique_sequences"]==1
    assert manifest["decision_counts"]["AUTO_EXCLUDED_NONBACTERIAL"]==1
    assert manifest["decision_counts"]["AUTO_SEQUENCE_MISMATCH"]==1
    assert manifest["decision_counts"]["AUTO_UNRESOLVED_NO_EVIDENCE"]==1
    assert sum(1 for l in (out/"positive_core_v2.fasta").read_text().splitlines() if l.startswith(">"))==1
    # Coverage failure must block, never treat missing IDs as negative.
    write_tsv(ledger,cols,rows[:-1])
    try: freeze(ns)
    except GateError as e: assert "coverage" in str(e)
    else: raise AssertionError("coverage hole was not rejected")
    print("selftest OK: full coverage, strict evidence, duplicate mapping, unresolved isolation")

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest="mode",required=True)
    sp=sub.add_parser("freeze")
    sp.add_argument("--fasta",required=True); sp.add_argument("--metadata",required=True); sp.add_argument("--evidence",required=True); sp.add_argument("--outdir",required=True)
    sp.add_argument("--rule-version",default="auto_evidence_v2_1.0"); sp.add_argument("--force",action="store_true")
    sub.add_parser("selftest")
    args=ap.parse_args()
    try:
        return freeze(args) if args.mode=="freeze" else selftest()
    except (GateError, OSError, csv.Error) as e:
        print(f"BLOCKED: {e}",file=sys.stderr); return 2
if __name__=="__main__": sys.exit(main())
