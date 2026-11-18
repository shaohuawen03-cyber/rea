# Frozen real5 weights × cross-distribution negatives (dualamp-eval-frozen-real5-20261118)
- positives sha256: `7b6ea12136623e85218ba78cfd4b713985a3122b24c61650e536e724b768aa1d`; leaneRA pin `f610dc00b1ae`; fair-esm esm2_t12_35M_UR50D layer 12
- weights release: dualamp-real5-candidate_v1-pilot-weights-20261116; independent sha256 verify: **PASS**
- standard negatives: n=10000; matched: n=10000 (cutoff 0.04177)

| model | negative set | FP rate @ embedded threshold (mean±sd over 5 seeds) | mean score | AUC (pos vs set) | TPR (pos ≥ thr) |
|---|---|---|---|---|---|
| M1 | matched | 0.0071 ± 0.0012 | 0.0403 | 0.9938 ± 0.0009 | 0.9076 |
| M1 | scrambled | 0.6136 ± 0.0275 | 0.7100 | 0.8074 ± 0.0046 | 0.9076 |
| M1 | standard | 0.0059 ± 0.0011 | 0.0542 | 0.9918 ± 0.0011 | 0.9076 |
| M2 | matched | 0.0063 ± 0.0027 | 0.0103 | 0.9982 ± 0.0001 | 0.9709 |
| M2 | scrambled | 0.5560 ± 0.0495 | 0.5909 | 0.8591 ± 0.0084 | 0.9709 |
| M2 | standard | 0.0020 ± 0.0019 | 0.0076 | 0.9974 ± 0.0002 | 0.9709 |
| M3 | matched | 0.0050 ± 0.0020 | 0.0082 | 0.9982 ± 0.0002 | 0.9736 |
| M3 | scrambled | 0.5344 ± 0.0390 | 0.5809 | 0.8565 ± 0.0084 | 0.9736 |
| M3 | standard | 0.0010 ± 0.0005 | 0.0025 | 0.9974 ± 0.0002 | 0.9736 |

*PILOT 口径；scrambled = 评估期 shuffle（seed 20261118），非 D-split challenge 集。
