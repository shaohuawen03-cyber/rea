# v2 evidence/source input integrity review — 2026-11-23

**State:** `SOURCE_INPUTS_HASHED_NOT_EVIDENCE_ADJUDICATED`  
**Purpose:** document the controlled Kaggle inventory of supporting audit inputs. This is not a full-text evidence ledger, PI/manual sign-off, wet-lab validation, or training authorization.

## Controlled execution

- Kaggle job: `dualamp-v2-source-audit-20261112`, run through the private GPU relay on 2026-11-23.
- Kernel: <https://www.kaggle.com/code/shaohuawen/dualamp-v2-source-audit-20261112>
- Result: `SOURCE_FILES_DOWNLOADED_AND_HASHED`; private `/tmp` staging removed by the job; no sequences or row-level records were emitted to GitHub.
- Aggregate result: [`cloud-gpu/results/dualamp-v2-source-audit-20261112/source_audit_integrity.json`](../../results/dualamp-v2-source-audit-20261112/source_audit_integrity.json).
- Training was not started.

## Key inventory results

| Input | Rows | SHA-256 | Structural finding |
|---|---:|---|---|
| `positive_evidence_audit.tsv` | 9,314 | `cc07c5aa84c4c972bc06b228adf190072c80de37df6aaa8718edae07aa4e447f` | 9,314 unique Sequence_IDs; 3,222 records have nonempty citation candidates |
| `apd6_natural_bacterial_mapping.tsv` | 410 | `17f48c6553359cbdcbad9bfd828af94d9a8a441b09d956e0cf46a8f6564f629b` | mapping table available; row-level values not exported |
| `apd_1002_machine_screen.tsv` | 1,002 | `09027ed941fc7067ea55c805bf2ea88035af8e752848c95c1fcaa70ddefe1556` | 1,002 unique Sequence_IDs and source record IDs |
| DRAMP file overlap report | 21 | `fa5c04253d4db484dc629b7562fc9027cf67ff7b83b1a055a23ce2b3f8e5ed72` | file-pair overlap summary |
| DRAMP public dataset distribution | 8 | `2a0143aa78e0450ff033e114b26b3989e8565b28f164b03ccbc0fe75c0147da3` | compact distribution table |
| DRAMP file stats | 7 | `d2c303bfa3f39ce30668dfadd93d193f8d31f5489d7358b475932cf4506c1777` | compact file-level QC table |
| DRAMP discrepancy analysis | text | `940c1ae7ef1d2052cd2a01eefca14aa4056d721db0f90a108d4a181c76decd5d` | hash/size recorded; no row-level extraction |
| DRAMP public conclusion | text | `7e24ee8a2b83b0079a47fc880547e703b1de1de98785fcc06a50472ed5e75977` | hash/size recorded; no row-level extraction |

The audit's **source-wide** existing status counts are: 2,805 `confirmed_named_bacterial_target`, 756 `confirmed_antibacterial_field_gram`, 18 `upgraded_from_generic_bacterial_signal`, 4,161 `pending_generic_no_bacterial_signal`, 1,002 `pending_apd_file_level`, 67 `pending_literature_verification`, 10 `pending_conflict`, 4 `pending_negative`, and 491 `excluded_non_antibacterial`. These are pre-existing source annotations, not decisions generated from the approved full-text rule and not asserted to be counts for the 3,579-record candidate_v1 universe.

## Decision / gate

The inventory verifies source availability and integrity only. The source audit contains sequence and annotation fields, but its status/citation fields do not establish, for every candidate, traceable full-text experimental evidence, sequence identity, explicit bacterial assay/target, positive endpoint, and pinpoint evidence location. In particular, nonempty `Citation_Candidates` and labels such as `confirmed_named_bacterial_target` are **not** substitutes for those requirements.

Therefore:

1. Do not map these pre-existing statuses directly to strict positives or negatives.
2. The evidence ledger must still cover every one of the 3,579 candidate IDs and retain traceable, record-level evidence or an explicit unresolved decision.
3. Run `cloud-gpu/scripts/freeze_auto_evidence_v2.py` only after that full-coverage ledger is built and validated; then complete split-leakage and negative-label checks before any v2 training.
4. No v2 training has been authorized by this inventory result; it remains blocked by the evidence and split gates.

## Access control closure

All eight temporary Drive `anyoneWithLink` reader permissions used for this one-time retrieval were revoked after the Kaggle run. `list_permissions` verified each file had exactly one permission remaining: the owner. No source file or per-row data was added to Git.
