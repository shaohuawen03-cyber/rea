#!/usr/bin/env python3
"""Compare DualAMP-Lite GPU run result directories (real4/real5/real6/real6b/real7...).

Usage:
  python compare_dualamp_runs.py BASE_DIR NEW_DIR [NEW_DIR ...] [--json OUT]

Reports (markdown to stdout):
  * D10 test metrics per model: auroc / auprc / mcc / f1 / sensitivity / specificity /
    threshold / frac_predicted_positive, and delta vs BASE_DIR
  * D9 ablation rows where FDR significance differs between BASE_DIR and NEW_DIR
  * D11 external (scrambled) false-positive rate
  * D15 input sha256: how many artifacts are byte-identical between runs
  * dataset summary: cluster count and split counts consistency
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

METRIC_COLS = ["auroc", "auprc", "mcc", "f1", "precision", "sensitivity", "specificity",
               "threshold", "frac_predicted_positive", "test_auroc_seed_mean", "test_auroc_seed_sd"]

def load_csv(d: Path, name):
    f = d / name
    if not f.exists():
        return []
    delim = "\t" if f.suffix == ".tsv" else ","
    with f.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=delim))

def load_json(d: Path, name):
    f = d / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

def d10(d: Path):
    rows = load_csv(d, "manuscript_D10_test_metrics.csv")
    return {r["model"]: r for r in rows}

def d9(d: Path):
    rows = load_csv(d, "ablation_D9_ablation.csv")
    return {(r["model_a"], r["model_b"], r["metric"]): r for r in rows}

def d11(d: Path):
    rows = load_csv(d, "manuscript_D11_external_validation.csv")
    return rows

def d15(d: Path):
    rows = load_csv(d, "manuscript_D15_input_sha256.tsv")
    return {r["path"]: r["sha256"] for r in rows}

def f3(x):
    try:
        return f"{float(x):.4f}"
    except Exception:
        return str(x)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("news", nargs="+")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    base = Path(args.base)
    base_name = base.name.replace("dualamp-", "")
    B = {"d10": d10(base), "d9": d9(base), "d11": d11(base), "d15": d15(base),
         "summary": load_json(base, "dataset_dataset_summary.json")}
    report = {"base": str(base), "runs": []}
    out = [f"# DualAMP run comparison vs `{base_name}`\n"]
    for n_arg in args.news:
        new = Path(n_arg)
        new_name = new.name.replace("dualamp-", "")
        N = {"d10": d10(new), "d9": d9(new), "d11": d11(new), "d15": d15(new),
             "summary": load_json(new, "dataset_dataset_summary.json")}
        entry = {"dir": str(new)}
        out.append(f"\n## {new_name} vs {base_name}\n")

        # D10 metrics
        out.append("\n### D10 test metrics\n")
        out.append("| model | " + " | ".join(METRIC_COLS) + " |")
        out.append("|---|" + "---|" * len(METRIC_COLS))
        delta_rows = {}
        for m in ("M0_RF", "M1", "M2", "M3"):
            b, n = B["d10"].get(m), N["d10"].get(m)
            if not b or not n:
                out.append(f"| {m} | missing in one run |"); continue
            cells = []
            delta_rows[m] = {}
            for c in METRIC_COLS:
                bv, nv = float(b[c]), float(n[c])
                delta_rows[m][c] = nv - bv
                sign = "+" if nv - bv >= 0 else ""
                cells.append(f"{nv:.4f} ({sign}{nv - bv:.4f})")
            out.append(f"| {m} | " + " | ".join(cells) + " |")
        entry["d10_delta"] = delta_rows

        # D9 FDR flips
        flips = []
        keys = set(B["d9"]) | set(N["d9"])
        for k in sorted(keys):
            b, n = B["d9"].get(k), N["d9"].get(k)
            if not b or not n:
                flips.append((k, "row missing", "")); continue
            bsig, nsig = b.get("significant_fdr") == "True", n.get("significant_fdr") == "True"
            if bsig != nsig:
                flips.append((k, f"q {b.get('q_value')}→{n.get('q_value')}", f"{bsig}→{nsig}"))
        out.append("\n### D9 FDR-significance flips\n")
        if flips:
            for (k, dsc, flip) in flips:
                out.append(f"- {k[0]} vs {k[1]} [{k[2]}]: {dsc} ({flip})")
        else:
            out.append("- none (all FDR verdicts identical)")
        entry["d9_flips"] = flips

        # D9 deltas for key contrasts
        out.append("\n### D9 key contrasts (new run)\n")
        for k in [("M3", "M0_RF", "auroc"), ("M3", "M0_RF", "mcc"), ("M3", "M1", "auroc"), ("M3", "M2", "auroc")]:
            n = N["d9"].get(k)
            if n:
                out.append(f"- {k[0]} vs {k[1]} [{k[2]}]: Δ={float(n['delta']):.5f} CI[{float(n['ci_low']):.4f},{float(n['ci_high']):.4f}] p={n['p_value']} q={n['q_value']} sig={n['significant_fdr']}")
        entry["d9"] = {f"{a}|{b}|{c}": N["d9"][(a, b, c)] for (a, b, c) in keys if (a, b, c) in N["d9"]}

        # D11 scrambled rates
        out.append("\n### D11 external (scrambled) false-positive rate\n")
        for r in N["d11"]:
            out.append(f"- {r.get('split')}/{r.get('model')}: frac_pos={r.get('frac_predicted_positive')} mean_score={float(r.get('mean_score', 0)):.4f}")
        entry["d11"] = N["d11"]

        # D15 byte-identical inputs
        same = [k for k in B["d15"] if B["d15"][k] == N["d15"].get(k)]
        changed = [k for k in B["d15"] if k in N["d15"] and B["d15"][k] != N["d15"][k]]
        out.append(f"\n### D15 input sha256\n- byte-identical: {len(same)}/{len(B['d15'])} files")
        if changed:
            out.append(f"- changed: {', '.join(changed)}")
        entry["d15_identical"] = same
        entry["d15_changed"] = changed

        # dataset summary
        ns, bs = N["summary"], B["summary"]
        if ns and bs:
            same_counts = ns.get("split_counts") == bs.get("split_counts")
            same_clusters = ns.get("n_clusters") == bs.get("n_clusters")
            out.append(f"\n### dataset_summary\n- n_clusters {bs.get('n_clusters')} → {ns.get('n_clusters')} ({'same' if same_clusters else 'DIFFERENT'}); split_counts identical: {same_counts}")
            entry["n_clusters"] = [bs.get("n_clusters"), ns.get("n_clusters")]
            entry["split_counts_identical"] = same_counts
        report["runs"].append(entry)
    text = "\n".join(out) + "\n"
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
