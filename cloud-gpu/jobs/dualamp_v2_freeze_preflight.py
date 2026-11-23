#!/usr/bin/env python3
"""Private strict-data freeze and training-readiness preflight for DualAMP v2.

All sequence-level inputs/outputs remain under /tmp and are uploaded only to a private
Drive resumable session. The relay receives aggregate gates and hashes only.
"""
from __future__ import annotations
import csv, datetime as dt, hashlib, json, os, shutil, subprocess, sys, tempfile, traceback, zipfile
from collections import Counter, defaultdict
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def fetch(file_id: str, path: Path):
    import gdown
    got = gdown.download(id=file_id, output=str(path), quiet=True)
    if not got or not path.exists() or not path.stat().st_size:
        raise RuntimeError(f"Drive download failed for {path.name}")


def read_fasta(path: Path):
    out, sid, chunks = {}, None, []
    def emit():
        if sid is not None:
            seq = "".join(chunks).replace(" ", "").upper()
            if not seq or sid in out: raise RuntimeError("empty sequence or duplicate FASTA ID")
            out[sid] = seq
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            if line.startswith(">"):
                emit(); sid = line[1:].split()[0]; chunks = []
            else:
                if sid is None: raise RuntimeError("sequence before FASTA header")
                chunks.append(line)
    emit()
    return out


def load_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        if not r.fieldnames: raise RuntimeError(f"missing CSV header: {path.name}")
        rows = list(r)
    return r.fieldnames, rows


def verify_splits(workdir: Path):
    ds = workdir / "run" / "dataset"
    summary = json.loads((ds / "dataset_summary.json").read_text(encoding="utf-8"))
    fields, rows = load_csv(ds / "dataset_all.csv")
    required = {"seq_id", "label", "split", "cluster", "layer"}
    if not required.issubset(fields):
        raise RuntimeError(f"dataset_all missing columns: {sorted(required-set(fields))}")
    cluster_splits = defaultdict(set)
    cluster_labels = defaultdict(set)
    exact_splits = defaultdict(set)
    split_counts, split_pos, split_neg = Counter(), Counter(), Counter()
    for r in rows:
        split = r["split"].strip(); label = int(r["label"]); cluster = r["cluster"].strip()
        if not split or not cluster: raise RuntimeError("blank split or cluster in built dataset")
        split_counts[split] += 1
        (split_pos if label == 1 else split_neg)[split] += 1
        cluster_splits[cluster].add(split)
        cluster_labels[cluster].add(label)
        seq = (r.get("seq") or "").strip().upper()
        if seq: exact_splits[seq].add(split)
    trainable = {"train", "val", "test", "dev"}
    trainable_cluster_leaks = [c for c, ss in cluster_splits.items() if len(ss & trainable) > 1]
    exact_leaks = [s for s, ss in exact_splits.items() if len(ss & trainable) > 1]
    mixed_trainable = [c for c, labs in cluster_labels.items() if len(labs) > 1 and cluster_splits[c] & trainable]
    if trainable_cluster_leaks or exact_leaks or mixed_trainable:
        raise RuntimeError(f"split leakage/conflicting labels: cluster={len(trainable_cluster_leaks)}, exact={len(exact_leaks)}, mixed={len(mixed_trainable)}")
    if summary.get("cluster_method") not in {"mmseqs", "cdhit"}:
        raise RuntimeError(f"homology cluster method not accepted: {summary.get('cluster_method')}")
    return {
        "cluster_method": summary.get("cluster_method"),
        "cluster_identity": summary.get("cluster_identity"),
        "cluster_coverage": summary.get("cluster_coverage"),
        "clusters": summary.get("n_clusters"),
        "records": summary.get("n_sequences"),
        "split_counts": dict(sorted(split_counts.items())),
        "split_positive_counts": dict(sorted(split_pos.items())),
        "split_negative_counts": dict(sorted(split_neg.items())),
        "trainable_cluster_leaks": len(trainable_cluster_leaks),
        "trainable_exact_sequence_leaks": len(exact_leaks),
        "trainable_mixed_label_clusters": len(mixed_trainable),
        "cluster_split_gate": "PASS",
    }


def upload(uri: str, path: Path):
    import requests
    size = path.stat().st_size; offset = 0; response = None
    with path.open("rb") as f:
        while offset < size:
            chunk = f.read(8 * 1024 * 1024)
            final = offset + len(chunk) >= size
            headers = {
                "Content-Type": "application/zip", "Content-Length": str(len(chunk)),
                "X-Goog-Upload-Offset": str(offset),
                "X-Goog-Upload-Command": "upload, finalize" if final else "upload",
                "X-Goog-Upload-Total-Size": str(size),
            }
            response = requests.put(uri, headers=headers, data=chunk, timeout=180)
            if response.status_code not in (200, 201):
                raise RuntimeError(f"private Drive upload failed: HTTP {response.status_code}")
            offset += len(chunk)
    return {"bytes": size, "sha256": sha(path), "drive_file": response.json() if response else {}}


def main():
    out = Path("/kaggle/working"); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dualamp-v2-freeze-", dir="/tmp"))
    report = {
        "stage": "strict_data_freeze_and_train_preflight",
        "rule_version": "auto_evidence_v2_1.0",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "training_started": False,
        "privacy_note": "Raw sequences, metadata and row evidence remain in private /tmp and private Drive only.",
    }
    try:
        envs = {"candidate_fasta":"FASTA_ID", "metadata":"METADATA_ID", "evidence":"EVIDENCE_AUDIT_ID"}
        missing = [k for k, v in envs.items() if not (os.environ.get(v) or "").strip()]
        if missing: raise RuntimeError("missing private Drive IDs: " + ", ".join(missing))
        inputs = {k: tmp / {"candidate_fasta":"candidate.fasta", "metadata":"metadata.tsv", "evidence":"evidence.tsv"}[k] for k in envs}
        for k, env in envs.items(): fetch(os.environ[env].strip(), inputs[k])
        repo = tmp / "repo"
        branch = os.environ.get("REA_BRANCH", "arena/cac2205a-rea")
        subprocess.run(["git", "clone", "--depth", "1", "--branch", branch,
                        "https://github.com/shaohuawen03-cyber/rea.git", str(repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        code_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        frozen = tmp / "frozen"
        cmd = [sys.executable, str(repo / "cloud-gpu/scripts/freeze_auto_evidence_v2.py"), "freeze",
               "--fasta", str(inputs["candidate_fasta"]), "--metadata", str(inputs["metadata"]),
               "--evidence", str(inputs["evidence"]), "--outdir", str(frozen),
               "--rule-version", "auto_evidence_v2_1.0"]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        manifest = json.loads((frozen / "dataset_manifest.json").read_text(encoding="utf-8"))
        positives = frozen / "positive_core_v2.fasta"
        pos = read_fasta(positives)
        evidence_rows = manifest["evidence_rows"]
        if manifest["input_candidate_records"] != 3579 or manifest["evidence_rows"] < 3579:
            raise RuntimeError("source universe/evidence coverage not closed to expected full candidate set")
        if manifest["strict_unique_sequences"] != len(pos):
            raise RuntimeError("strict FASTA unique-sequence count does not match frozen manifest")
        if not pos: raise RuntimeError("strict-positive set is empty; training blocked")
        report["freeze"] = {
            "state": manifest["state"], "input_candidate_records": manifest["input_candidate_records"],
            "input_unique_sequences": manifest["input_unique_sequences"], "evidence_rows": evidence_rows,
            "decision_counts": manifest["decision_counts"],
            "strict_positive_records": manifest["strict_confirmed_records"],
            "strict_positive_unique_sequences": manifest["strict_unique_sequences"],
            "strict_positive_fasta_sha256": manifest["output_files"]["positive_core_v2.fasta"]["sha256"],
            "source_sha256": {k: manifest["input_files"][p.name]["sha256"] for k,p in inputs.items()},
            "coverage_gate": "PASS", "id_closure_gate": "PASS",
        }
        work = tmp / "pipeline"; work.mkdir()
        sys.path.insert(0, str(repo / "cloud-gpu/jobs"))
        import dualamp_pilot
        # Use the project's fixed UniProt A snapshot, seed 11, standard weak-negative recipe.
        fa = repo / "uniprot_bacteria_cytoplasm_reviewed.fasta.gz"
        ta = repo / "uniprot_bacteria_cytoplasm_reviewed.tsv.gz"
        if not fa.exists() or not ta.exists(): raise RuntimeError("verified UniProt A snapshot missing")
        negatives = work / "negatives.fasta"
        neg_stats = dualamp_pilot.build_negatives(fa, ta, negatives, set(pos.values()), cap=10000, seed=11, neg_mode="standard")
        if neg_stats.get("negatives_final", 0) < 100: raise RuntimeError("weak-negative gate failed: fewer than 100 usable negatives")
        report["negative_audit"] = {
            **neg_stats,
            "label_semantics": "WEAK_NEGATIVE_BACKGROUND_NOT_EXPERIMENTAL_INACTIVITY",
            "source": "UniProt reviewed bacterial cytoplasm Release 2026_04 snapshot, AMP-keyword screened, seed=11",
            "gate": "PASS_WITH_WEAK_LABEL_CAVEAT",
        }
        manifest_path = work / "sources.yaml"
        manifest_path.write_text(f"""sources:\n  - id: positive_core_v2_strict\n    path: {positives}\n    format: fasta\n    layer: general_amp\n    label: 1\n    min_len: 8\n    max_len: 50\n  - id: uniprot_a_weak_negative_background\n    path: {negatives}\n    format: fasta\n    layer: negative_background\n    label: 0\n    min_len: 8\n    max_len: 50\n""", encoding="utf-8")
        # Reproduce the project's tested MMseqs2 compatibility shim and build only; no model training.
        dualamp_pilot._ensure_clustering()
        leanrea = tmp / "leanrea"
        rev = os.environ.get("LEANREA_REV", "f610dc00b1aec3ab948a5b77574af0e644259a13")
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "arena/4ecb70f1-leanrea",
                        "https://github.com/hukeying85-ux/leanrea.git", str(leanrea)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(leanrea), "fetch", "--depth", "1", "origin", rev], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(leanrea), "checkout", "--detach", rev], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pkg = leanrea / "dualamp-lite"
        env = {**os.environ, "PYTHONPATH": str(pkg)}
        subprocess.run([sys.executable, "-m", "dualamp_lite", "build", "--manifest", str(manifest_path),
                        "--workdir", str(work / "run"), "--cluster-method", "auto"],
                       cwd=str(pkg), env=env, check=True)
        split = verify_splits(work)
        split["leanrea_commit"] = rev
        report["split_audit"] = split
        report["low_sample_warning"] = {
            "strict_positive_unique_sequences": manifest["strict_unique_sequences"],
            "test_positive_n": split["split_positive_counts"].get("test", 0),
            "warning_threshold_test_positive_n": 100,
            "status": "UNDERPOWERED_WARNING" if split["split_positive_counts"].get("test", 0) < 100 else "ADEQUATE_BY_PROJECT_WARNING_THRESHOLD",
        }
        report["training_readiness"] = "PREFLIGHT_PASS_NOT_TRAINED"
        report["frozen_manifest_sha256"] = sha(frozen / "dataset_manifest.json")
        report["dataset_summary_sha256"] = sha(work / "run/dataset/dataset_summary.json")
        # Keep all row-level materials confined to this archive, uploaded to private Drive.
        bundle = tmp / "dualamp_v2_pretrain_gate_bundle_r3.zip"
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for root in (frozen, work / "run"):
                for p in root.rglob("*"):
                    if p.is_file(): z.write(p, p.relative_to(tmp))
            z.write(manifest_path, manifest_path.relative_to(tmp))
            z.write(negatives, negatives.relative_to(tmp))
        uri = (os.environ.get("EVIDENCE_LEDGER_UPLOAD_URI") or "").strip()
        if not uri: raise RuntimeError("missing private Drive upload session URI")
        report["private_bundle_upload"] = upload(uri, bundle)
        report["status"] = "PREFLIGHT_PASS_NOT_TRAINED"
        report["finished_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        (out / "v2_pretrain_gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    except Exception as e:
        report.update({"status":"FAILED_OR_BLOCKED_NOT_TRAINED", "error":str(e),
                       "traceback":traceback.format_exc(limit=5), "finished_utc":dt.datetime.now(dt.timezone.utc).isoformat()})
        (out / "v2_pretrain_gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr, flush=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__": main()
