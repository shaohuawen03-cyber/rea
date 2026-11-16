#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 1：sORF 标准化 + 精确去重（>1 亿条流式处理；来源映射全保留）。
输入：一个或多个 FASTA，或 --manifest TSV（列：file_path, sample_id[, assembly_id]）。
输出（out-dir 下）：
  all_sORF_master.tsv       seq_sha256  seq  length  count  first_id  low_complexity
  unique.faa                全部唯一序列（5–100 aa）
  unique_dualamp_5_50.faa   供 DualAMP 的划分
  unique_macrel_10_100.faa  供 Macrel 的划分
  source_map/sORF_source_map.bucket-XX.tsv   来源映射（按 seq_sha256 前 2 位 hex 分桶，64 桶）：
                                seq_sha256 sample_id assembly_id contig_id orf_id source_file original_header length
  sORF_source_map_index.json      分桶行数合计 "source_rows_total"（= 入库记录数，必须闭合）
  qc_standardize.json             逐环节计数
  master.sqlite                   去重中间结果（可删）
规则：长度 5–100；仅标准 20 AA；低复杂度（单残基>80%）仅标记不删除；含 U/O/B/Z/J/X/* 直接拒收。
来源解析：header 形如 >contig_123|orf_45 时，contig_id=contig_123；否则 orf_id=首 token。
注意：--limit 仅用于调试，不是抽样；pilot 抽样用 00b_make_pilot.py。
"""
import argparse, hashlib, json, os, sqlite3, sys
from collections import Counter
from pathlib import Path

AA = set("ACDEFGHIKLMNPQRSTVWY")


def iter_fasta(path, sample_id, assembly_id):
    """yield (sample_id, assembly_id, contig_id, orf_id, original_header, seq)——行迭代，内存与文件大小无关"""
    name, buf = None, []

    def row(name, buf):
        header = name
        parts = header.split("|")
        if len(parts) > 1:
            contig_id = parts[0]
            orf_raw = parts[-1].split()[0]
        else:
            contig_id = ""
            orf_raw = header.split()[0]
        return sample_id, assembly_id, contig_id, (orf_raw or header.split()[0]), header, "".join(buf).upper()

    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    yield row(name, buf)
                name = line[1:].strip()
                buf = []
            else:
                buf.append(line.strip())
        if name is not None:
            yield row(name, buf)


def sha256_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", nargs="*", default=[], help="一个或多个 FASTA（与 --manifest 二选一或并用）")
    ap.add_argument("--manifest", help="TSV：file_path<TAB>sample_id[<TAB>assembly_id]")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-len", type=int, default=5)
    ap.add_argument("--max-len", type=int, default=100)
    ap.add_argument("--max-single-frac", type=float, default=0.8)
    ap.add_argument("--bucket-count", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0, help="调试用：只读前 N 条（不是抽样）")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    map_dir = os.path.join(args.out_dir, "source_map")
    os.makedirs(map_dir, exist_ok=True)

    files = []  # (path, sample_id, assembly_id)
    for f in args.input:
        files.append((f, Path(f).stem, ""))
    if args.manifest:
        with open(args.manifest) as fh:
            header = fh.readline()
            if not header.lower().startswith(("file_path", "path")):
                fh.seek(0)
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                files.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    if not files:
        ap.error("no input files (use --input or --manifest)")

    db = os.path.join(args.out_dir, "master.sqlite")
    if os.path.exists(db):
        os.remove(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA journal_mode=MEMORY")
    conn.execute("CREATE TABLE master(seq TEXT PRIMARY KEY, sha TEXT, count INTEGER NOT NULL, first_id TEXT NOT NULL, low_complexity INTEGER NOT NULL DEFAULT 0)")

    n_in = n_len = n_aa = 0
    per_file = []
    bucket_files = {}
    bucket_counts = [0] * args.bucket_count

    def bucket(i):
        if i not in bucket_files:
            bucket_files[i] = open(os.path.join(map_dir, f"sORF_source_map.bucket-{i:02x}.tsv"), "a", buffering=1 << 20)
        return bucket_files[i]

    for path, sample_id, assembly_id in files:
        f_in = 0
        batch = []
        for row in iter_fasta(path, sample_id, assembly_id):
            if args.limit and n_in >= args.limit:
                break
            sample_id_, assembly_id_, contig_id, orf_id, header, seq = row
            n_in += 1
            f_in += 1
            sha = sha256_text(seq)
            if not (args.min_len <= len(seq) <= args.max_len):
                continue
            n_len += 1
            if any(c not in AA for c in seq):
                continue
            n_aa += 1
            low_complexity = int(Counter(seq).most_common(1)[0][1] / len(seq) > args.max_single_frac)
            b = int(sha[:2], 16) % args.bucket_count
            lines = f"{sha}\t{sample_id_}\t{assembly_id_}\t{contig_id}\t{orf_id}\t{os.path.basename(path)}\t{header}\t{len(seq)}\n"
            bf = bucket(b)
            bf.write(lines)
            bucket_counts[b] += 1
            batch.append((seq, sha, header, low_complexity))
            if len(batch) >= 50000:
                conn.executemany("INSERT INTO master(seq,sha,count,first_id,low_complexity) VALUES(?,?,1,?,?) "
                                 "ON CONFLICT(seq) DO UPDATE SET count=count+1", batch)
                conn.commit()
                batch.clear()
        if batch:
            conn.executemany("INSERT INTO master(seq,sha,count,first_id,low_complexity) VALUES(?,?,1,?,?) "
                             "ON CONFLICT(seq) DO UPDATE SET count=count+1", batch)
            conn.commit()
        per_file.append({"file": path, "sample_id": sample_id, "assembly_id": assembly_id, "records": f_in})
        if args.limit and n_in >= args.limit:
            break
    for bf in bucket_files.values():
        bf.close()

    if n_in == 0:
        conn.close()
        raise SystemExit("ERROR: 所有输入文件合计 0 条记录——检查 --input/--manifest 路径与 FASTA 头部是否以 '>' 开头")
    n_unique = conn.execute("SELECT COUNT(*) FROM master").fetchone()[0]
    n_lowcx = conn.execute("SELECT COUNT(*) FROM master WHERE low_complexity=1").fetchone()[0]

    with open(os.path.join(args.out_dir, "all_sORF_master.tsv"), "w") as m, \
         open(os.path.join(args.out_dir, "unique.faa"), "w") as u, \
         open(os.path.join(args.out_dir, "unique_dualamp_5_50.faa"), "w") as u5, \
         open(os.path.join(args.out_dir, "unique_macrel_10_100.faa"), "w") as u10:
        m.write("seq_sha256\tseq\tlength\tcount\tfirst_id\tlow_complexity\n")
        for sha, seq, count, first_id, low_complexity in conn.execute(
                "SELECT sha,seq,count,first_id,low_complexity FROM master ORDER BY rowid"):
            m.write(f"{sha}\t{seq}\t{len(seq)}\t{count}\t{first_id}\t{low_complexity}\n")
            u.write(f">{first_id}\n{seq}\n")
            if 5 <= len(seq) <= 50:
                u5.write(f">{first_id}\n{seq}\n")
            if 10 <= len(seq) <= 100:
                u10.write(f">{first_id}\n{seq}\n")

    source_total = sum(bucket_counts)
    qc = {"files": per_file, "input_records": n_in,
          "after_length": n_len, "after_alphabet": n_aa,
          "unique_sequences": n_unique,
          "duplicates_removed": n_aa - n_unique,
          "low_complexity_unique": n_lowcx,
          "source_map_rows": source_total,
          "source_map_buckets": {f"bucket-{i:02x}": c for i, c in enumerate(bucket_counts) if c},
          "min_len": args.min_len, "max_len": args.max_len}
    with open(os.path.join(map_dir, "..", "sORF_source_map_index.json"), "w") as fh:
        json.dump({"source_map_rows_total": source_total, "buckets": bucket_counts}, fh, indent=2)
    with open(os.path.join(args.out_dir, "qc_standardize.json"), "w") as fh:
        json.dump(qc, fh, indent=2, ensure_ascii=False)
    print(json.dumps(qc, indent=2, ensure_ascii=False))
    print("QC：source_map_rows 必须 == after_alphabet（每条标准化记录在映射表里恰好一行）；不一致即中断")
    if source_total != n_aa:
        raise SystemExit("ERROR: source map rows != admitted records（闭合失败）")


if __name__ == "__main__":
    main()
