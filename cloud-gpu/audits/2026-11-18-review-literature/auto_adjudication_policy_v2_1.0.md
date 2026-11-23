# DualAMP v2.1 automatic evidence adjudication policy

**Policy version:** `auto_evidence_v2_1.0`  
**Recorded:** 2026-11-12 UTC (platform date)  
**Approval:** User selected “I have authority to represent the project; approve this automatic rule” in Arena on this turn. This is a policy authorization record, **not** a fabricated per-row human review, PI identity proof, wet-lab validation, or label-level signature.  
**Scope:** candidate_v1 full universe; the 230-row audit remains a sampled QC set only.

## Deterministic decision rules

One or more evidence rows may exist per `Sequence_ID`. All evidence rows are retained and cited. Rule application is fail-closed.

### `AUTO_CONFIRMED_ANTIBACTERIAL`
Require at least one evidence row satisfying **all** of:

- DOI, numeric PMID, or PMCID resolves to a traceable source;
- accessible full text / experimental table, not title or abstract alone;
- candidate sequence is an exact match or a fully documented derivative/chemical form;
- explicit bacterial endpoint and named target organism/strain;
- named experimental assay and positive antibacterial result;
- precise evidence location (table, figure, section, page, or paragraph);
- no unresolved identity/record conflict for that evidence.

MIC is captured when reported, but an alternative experimental antibacterial assay may qualify if its result and method are explicit. A `>` or `<` MIC is retained as censored text, not converted to an exact value.

### `AUTO_EXCLUDED_NONBACTERIAL`
Full-text evidence is sequence-matched, traceable, and supports only non-bacterial endpoints for the antibacterial task. This means excluded from the antibacterial-positive view; it does **not** label the peptide experimentally inactive against bacteria.

### `AUTO_SEQUENCE_MISMATCH`
The cited peptide/derivative cannot be reliably mapped to the candidate sequence, or the source record explicitly mismatches. Never convert to a negative.

### `AUTO_CONFLICT`
The evidence ledger flags an unresolved source, identity, or endpoint conflict. Keep every evidence row and isolate the record.

### `AUTO_UNRESOLVED_NO_EVIDENCE`
No row meets the strict requirements, including missing references, inaccessible full text, database-only annotation, incomplete sequence mapping, or unknown assay/endpoint. Unresolved is not negative.

## Dataset and execution requirements

- Every FASTA `Sequence_ID` must have at least one evidence-ledger row; no ID may silently disappear.
- FASTA and metadata ID sets must match exactly; all source accessions remain in the retained metadata/source map.
- Strict FASTA contains only `AUTO_CONFIRMED_ANTIBACTERIAL`; unresolved, non-bacterial, mismatch, and conflict are separate tables.
- Broad/relaxed data are analysis-only unless explicit evidence fields define their membership; never present as ground truth.
- Exact-sequence deduplication is performed only in the FASTA view; all original IDs map to the representative through `source_map_v2.tsv`. Homology/source-group split is a separate mandatory training preflight.
- Training is enabled only after full evidence coverage, ID closure, strict evidence rule validation, split leakage checks, negative-label audit, and a dataset manifest with input/output SHA-256.
- No model prediction is treated as experimental evidence. No unannotated sequence is silently treated as an experimental negative.
- The machine manifest records `DATASET_FROZEN_BY_RULES_NOT_PI_SIGNED`; do not emit `SIGNED` or claim a manual label review.

## One-time policy approval versus data evidence

This approval replaces the need to manually fill 230 `Review_Result` fields for future machine-rule execution. It does **not** waive the evidence requirements above, allow the sampled 230 rows to stand in for the full candidate universe, or permit training on incomplete/unresolved data. It authorizes the deterministic automation policy; each dataset version must still pass the machine gate and produce an immutable manifest.
