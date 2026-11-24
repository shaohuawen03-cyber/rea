# DualAMP v2.1 release candidate — NOT_RELEASABLE

Generated 2026-11-24T12:07:55Z from `cloud-gpu/results/dualamp-v2-1-smoke-20261124/v2_1_training_report.json`.

**Tier: `NOT_RELEASABLE`.**

## Why this is not a validated release

- training did not complete
- smoke configuration (downsampled cohort), not the frozen protocol
- internal test has 0 positives (<100)
- only 0 seeds
- no calibration table (Brier/ECE) in the report
- composition challenge verdict = None

## What is in this folder

| File | Content |
| --- | --- |
| `v2_1_release_summary.json` | tier, provenance, caveats, hashes |
| `v2_1_test_metrics.csv` | aggregate test metrics per model (no per-sequence rows) |
| `v2_1_metrics_summary.csv` | seed-level summary |
| `v2_1_calibration.csv` | Brier / ECE / Platt rows |
| `v2_1_operating_points.csv` | dev-OOF thresholds at fixed specificity |
| `v2_1_challenge_sets.csv` | composition-challenge and other challenge splits |

## What is deliberately not here

Peptide sequences, model weights, scalers and per-sequence predictions stay in the private
Drive bundle. The repo only carries aggregate numbers.
