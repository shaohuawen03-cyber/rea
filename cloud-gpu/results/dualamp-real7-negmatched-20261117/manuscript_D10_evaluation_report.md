# DualAMP-Lite: independent-test evaluation report

Generated 2026-11-17T13:06:30 · workdir `/tmp/dualamp-pilot/run`

Methodology follows the V3 plan: ESM-2 (layer 12) frozen features, masked mean pooling, multi-scale 1D-CNN, biophysical priors, Concat fusion (M3), cluster-based group split, DeLong/bootstrap statistics.

## 0. Environment

- python 3.13.16 · Linux-6.18.55+-x86_64-with-glibc2.39
- CUDA: True · GPU: Tesla T4
- packages: {'numpy': '2.1.3', 'pandas': '2.3.3', 'sklearn': '1.6.1', 'scipy': '1.16.3', 'torch': '2.11.0+cu130', 'Bio': '1.88', 'transformers': '5.20.2'}

## 1. Dataset (D2/D3)

- unique sequences: **14079**
- homology clusters: **10507** (tool: `cdhit`, identity 0.4, coverage 0.8)
- length window: 5-50 aa

| layer | sequences |
|---|---|
| negative_background | 10000 |
| general_amp | 3579 |
| scrambled | 500 |

| split | sequences | positives | negatives |
|---|---|---|---|
| train | 9503 | 2504 | 6999 |
| val | 2037 | 537 | 1500 |
| dev | 11540 | 0 | 0 |
| test | 2037 | 537 | 1500 |
| external | 0 | 0 | 0 |
| external_homolog | 0 | 0 | 0 |
| scrambled | 500 | 0 | 500 |
| review | 2 | 1 | 1 |

## 2. Features (D4)

- encoder: **fair-esm** (`esm2_t12_35M_UR50D`, layer 12, 480-d)
- pooling: masked mean over residues (no BOS/CLS/EOS/padding)
- biophysical block: 35 features (scaler fitted on train only; 3 feature pairs with |r| >= 0.85)
- train: 9503 peptides x 50 residues (float16, 15.7 s)
- val: 2037 peptides x 50 residues (float16, 3.3 s)
- test: 2037 peptides x 50 residues (float16, 3.2 s)
- scrambled: 500 peptides x 50 residues (float16, 0.7 s)

See `features/biophys_correlation.csv` for the redundancy audit.

## 3. Independent test set (D10)

Point estimates on the frozen test split; thresholds were tuned on dev OOF predictions only. 95% CIs resample **homology clusters**, not peptides.

| model | AUROC | AUROC 95% CI | AUPRC | MCC | sensitivity | specificity | F1 | AUROC across seeds |
|---|---|---|---|---|---|---|---|---|
| M2 | 0.995 [0.99, 1.00] | 0.99-1.00 | 0.989 | 0.917 | 0.909 | 0.989 | 0.938 | 0.994 ± 0.001 |
| M3 | 0.995 [0.99, 1.00] | 0.99-1.00 | 0.989 | 0.923 | 0.909 | 0.993 | 0.942 | 0.994 ± 0.000 |
| M1 | 0.993 [0.99, 1.00] | 0.99-1.00 | 0.985 | 0.924 | 0.935 | 0.983 | 0.944 | 0.993 ± 0.000 |
| M0_RF | 0.989 [0.99, 0.99] | 0.99-0.99 | 0.974 | 0.875 | 0.894 | 0.973 | 0.907 | 0.989 ± 0.000 |

AUROC CIs in the dedicated column use the DeLong variance (plan §5: DeLong is used for AUROC only).

## 4. Ablation / paired comparisons (D9)

AUROC: DeLong paired test. MCC/AUPRC: paired cluster bootstrap. q-values: Benjamini-Hochberg across all rows. `within_margin` uses the pre-set MCC equivalence margin - a non-significant p-value is **not** evidence of equivalence.

| pair | metric | delta (A-B) | 95% CI | p | q | FDR-significant | within margin | conclusion |
|---|---|---|---|---|---|---|---|---|
| M3 vs M0_RF | auroc | 0.006 | [0.003, 0.009] | 0.000 | 0.000 | True | nan |  |
| M3 vs M0_RF | mcc | 0.048 | [0.025, 0.074] | 0.000 | 0.000 | True | False | a > b (FDR-adjusted) |
| M3 vs M0_RF | auprc | 0.015 | [0.009, 0.022] | 0.000 | 0.000 | True | nan |  |
| M3 vs M1 | auroc | 0.002 | [0.000, 0.004] | 0.018 | 0.032 | True | nan |  |
| M3 vs M1 | mcc | -0.000 | [-0.015, 0.014] | 0.982 | 0.982 | False | True | no meaningful difference (pre-set margin) |
| M3 vs M1 | auprc | 0.003 | [0.001, 0.006] | 0.003 | 0.007 | True | nan |  |
| M3 vs M2 | auroc | -0.000 | [-0.001, 0.000] | 0.702 | 0.861 | False | nan |  |
| M3 vs M2 | mcc | 0.006 | [-0.002, 0.016] | 0.131 | 0.197 | False | True | no meaningful difference (pre-set margin) |
| M3 vs M2 | auprc | 0.000 | [-0.001, 0.001] | 0.765 | 0.861 | False | nan |  |

## 5. External / challenge sets (D11)

External data never participated in model selection or threshold tuning. The `scrambled` split is composition-matched noise: a model that calls it positive is fitting composition only, not sequence order.

| split | model | n | mean score | fraction predicted positive | AUROC | MCC |
|---|---|---|---|---|---|---|
| scrambled | M1 | 500 | 0.772 | 0.764 | n/a | n/a |
| scrambled | M2 | 500 | 0.694 | 0.656 | n/a | n/a |
| scrambled | M3 | 500 | 0.683 | 0.640 | n/a | n/a |

## 6. Mandatory caveats

- M3 vs M2 (auroc): the paired 95% CI includes zero - report as 'no measurable difference on this test set'.
- M3 vs M2 (mcc): |delta| = 0.006 is inside the pre-set margin 0.020. **Do not claim** the biophysical branch or the residue-level CNN provides a meaningful gain.
- All metrics here concern peptide-level AMP classification. They do not demonstrate abundance differences between AD patients and controls; that requires the read-mapping and DESeq2 chain of plan §4.3 (D13/D14).
- The AMPSphere external check must use the 100 experimentally tested peptides (79 active + 21 inactive); the 863,498 computationally discovered candidates are candidates, not labels.

## 7. Deliverable checklist (plan §6)

| id | artifact | status | note |
|---|---|---|---|
| D1 sample metadata table | `dataset/D1_sample_metadata.csv` | ok | template only - manual curation required |
| D2 clean dataset | `dataset/dataset_all.csv` | ok |  |
| D3 train/val/test splits | `dataset/splits/test.csv` | ok |  |
| D4 features | `features/train_feat.npz` | ok |  |
| D5 baselines | `model/M0_RF_results.json` | ok | RF / HGB / CNN-BiGRU |
| D6 M1 weights | `model/M1_seed1.pt` | ok |  |
| D8 M3 weights | `model/M3_seed1.pt` | ok |  |
| D9 ablation | `ablation/D9_ablation.csv` | ok |  |
| D10 test metrics | `manuscript/D10_test_metrics.csv` | ok |  |
| D11 external validation | `manuscript/D11_external_validation.csv` | ok |  |
| D15 file manifest | `manuscript/D15_input_sha256.tsv` | ok |  |

## 8. Figures

- `figures/D10_roc_curve.png`
- `figures/D10_pr_curve.png`

