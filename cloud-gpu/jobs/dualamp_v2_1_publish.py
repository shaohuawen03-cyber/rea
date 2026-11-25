#!/usr/bin/env python3
"""Turn a completed v2.1 training report into a de-identified release candidate.

Hard rules (DualAMP_Lite/V2_RUNBOOK_2026-11-17.md):
  * no peptide sequences, no weights, no per-sequence predictions in git;
  * aggregate metrics only, with the sample size and the negative semantics attached;
  * the release tier is decided by the evidence, never by the wish to ship.

Inputs   cloud-gpu/results/<job-id>/v2_1_training_report.json
Outputs  cloud-gpu/results/dualamp-v2-1-publish-<date>/v2_1_release_summary.json
         cloud-gpu/results/dualamp-v2-1-publish-<date>/v2_1_test_metrics.csv
         cloud-gpu/results/dualamp-v2-1-publish-<date>/v2_1_calibration.csv
         cloud-gpu/results/dualamp-v2-1-publish-<date>/v2_1_operating_points.csv
         cloud-gpu/results/dualamp-v2-1-publish-<date>/README.md
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "cloud-gpu" / "results"

DROP_KEYS = {"seq", "sequence", "sequences", "fasta", "prob", "pred", "prediction"}
KEEP_PER_SEQUENCE_FIELDS = {"sequence_id"}  # ids are opaque hashes, never sequences


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scrub(rows: list[dict]) -> list[dict]:
    """Drop anything sequence-like from aggregate tables."""
    out = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if k.lower() in DROP_KEYS and k not in KEEP_PER_SEQUENCE_FIELDS:
                continue
            if isinstance(v, str) and len(v) > 60 and v.upper().isalpha():
                continue  # a raw peptide accidentally pasted into a text field
            clean[k] = v
        out.append(clean)
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    rows = scrub(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for k in row:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def decide_tier(report: dict, metrics: list[dict], challenge: dict) -> tuple[str, list[str]]:
    """Release tier from evidence only. Returns (tier, blocking reasons)."""
    blockers: list[str] = []
    cohort = report.get("cohort") or {}
    positives = int(cohort.get("positives") or 0)
    test_rows = [m for m in metrics if str(m.get("split", "internal_test")) == "internal_test"]
    n_pos = max((int(float(m.get("n_pos") or 0)) for m in test_rows), default=0)
    seeds = report.get("protocol", {}).get("seeds") or []
    calib = report.get("calibration_metrics") or []

    if report.get("status") != "COMPLETED_V2_1_TRAINING":
        blockers.append("training did not complete")
    if report.get("smoke_mode"):
        blockers.append("smoke configuration (downsampled cohort), not the frozen protocol")
    if positives < 100:
        blockers.append(f"frozen cohort has {positives} positives (<100)")
    if n_pos < 100:
        blockers.append(f"internal test has {n_pos} positives (<100)")
    if len(seeds) < 5:
        blockers.append(f"only {len(seeds)} seeds")
    if not calib:
        blockers.append("no calibration table (Brier/ECE) in the report")
    verdict = (challenge or {}).get("verdict")
    if verdict == "FAIL_COMPOSITION_SHORTCUT":
        blockers.append("composition challenge failed (model accepts composition-matched shuffles)")
    elif verdict != "PASS":
        blockers.append(f"composition challenge verdict = {verdict}")

    if not blockers:
        return "V2_1_VALIDATED", blockers
    if report.get("status") == "COMPLETED_V2_1_TRAINING" and report.get("metrics"):
        return "V2_1_EXPLORATORY", blockers
    return "NOT_RELEASABLE", blockers


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else "dualamp-v2-1-smoke-20261124"
    report_path = RESULTS / run_id / "v2_1_training_report.json"
    if not report_path.exists():
        raise SystemExit(f"no report at {report_path}")
    report = json.loads(report_path.read_text())

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    out = RESULTS / f"dualamp-v2-1-publish-{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    metrics = report.get("metrics") or []
    challenge = report.get("composition_challenge") or {}
    tier, blockers = decide_tier(report, metrics, challenge)

    write_csv(out / "v2_1_test_metrics.csv", metrics)
    seed_rows = report.get("metrics_summary") or []
    if not seed_rows:
        # leanrea did not emit a seed-level summary table in this run; the per-model
        # seed mean/sd still rides on the headline rows, so surface it rather than
        # publishing an empty file.
        seed_rows = [{
            "model": m.get("model"),
            "n_seeds": m.get("n_seeds") or "",
            "test_auroc_seed_mean": m.get("test_auroc_seed_mean"),
            "test_auroc_seed_sd": m.get("test_auroc_seed_sd"),
            "source": "headline metric row (seed-level table absent in the report)",
        } for m in metrics if str(m.get("split", "internal_test")) == "internal_test"]
    write_csv(out / "v2_1_metrics_summary.csv", seed_rows)
    write_csv(out / "v2_1_calibration.csv", report.get("calibration_metrics") or [])
    write_csv(out / "v2_1_operating_points.csv", report.get("fixed_specificity_operating_points") or [])
    write_csv(out / "v2_1_challenge_sets.csv", challenge.get("rows") or [])

    summary = {
        "release_candidate": f"v2.1.0-{tier.lower().replace('v2_1_', '').replace('_', '-')}",
        "tier": tier,
        "release_blockers": blockers,
        "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_report": str(report_path.relative_to(ROOT)),
        "source_report_sha256": sha(report_path),
        "kernel": f"shaohuawen/{run_id}",
        "kernel_url": f"https://www.kaggle.com/code/shaohuawen/{run_id}",
        "code_commit": report.get("code_commit"),
        "leanrea_commit": report.get("leanrea_commit"),
        "dataset": {
            "version": report.get("dataset_version"),
            "rule_version": report.get("rule_version"),
            "cohort": report.get("cohort"),
            "negative_semantics": report.get("negatives"),
            "smoke_mode": report.get("smoke_mode", False),
        },
        "protocol": report.get("protocol", {}),
        "encoder": report.get("encoder_preflight"),
        "calibration": {
            "rows": len(report.get("calibration_metrics") or []),
            "note": "Brier / ECE / Platt(dev OOF) come from leanrea calibration_metrics.csv",
        },
        "composition_challenge": {
            "verdict": challenge.get("verdict"),
            "max_frac_predicted_positive": challenge.get("max_frac_predicted_positive"),
        },
        "fixed_specificity_operating_points": report.get("fixed_specificity_operating_points") or [],
        "caveats": report.get("caveats") or [],
        "steps": report.get("steps"),
        "files": sorted(p.name for p in out.glob("*")),
    }
    (out / "v2_1_release_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # de-identified artefacts only: no sequences, no weights, no per-sequence probabilities
    (out / "README.md").write_text(
        f"""# DualAMP v2.1 release candidate — {tier}

Generated {summary['generated_utc']} from `{summary['source_report']}`.

**Tier: `{tier}`.**

""" + ("## Why this is not a validated release\n\n" + "\n".join(f"- {b}" for b in blockers) + "\n"
       if blockers else "All release gates passed.\n") + f"""
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
""", encoding="utf-8")

    print(json.dumps({"tier": tier, "blockers": blockers,
                      "out": str(out.relative_to(ROOT))}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
