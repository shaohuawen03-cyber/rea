#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 1：sORF 标准化 + 精确去重（>1 亿条也可流式处理）。
输入：FASTA（蛋白）。输出：
  <out_dir>/all_sORF_master.tsv     seq<TAB>length<TAB>count<TAB>first_id
  <out_dir>/unique.faa              唯一序列（mmseqs/macrel/ESM 输入）
  <out_dir>/qc_standardize.json     逐环节计数（漏斗总账）
过滤：长度 5–50 aa；仅标准 20 字母表；单一残基占比 > 80% 剔除（低复杂度）；含 U/O/B/Z/J/X/* 默认剔除。
复现：seed 无关（纯计数）；sqlite 落盘，断点可重跑（--resume 会重建而非追加，避免双写）。
"""
import argparse, json, os, re, sqlite3, sys, hashlib

AA = set("ACDEFGHIKLMNPQRSTVWY")

def iter_fasta(path):
    name, buf = None, []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(buf)
                name = line[1:].split()[0]
                buf = []
            else:
                buf.append(line.strip().upper())
        if name is not None:
            yield name, "".join(buf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="输入 sORF FASTA（蛋白）")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--min-len", type=int, default=5)
    ap.add_argument("--max-len", type=int, default=50)
    ap.add_argument("--max-single-frac", type=float, default=0.8)
    ap.add_argument("--limit", type=int, default=0, help=">0 仅处理前 N 条（pilot 外推用）")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    db = os.path.join(args.out_dir, "master.sqlite")
    if os.path.exists(db):
        os.remove(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE master(seq TEXT PRIMARY KEY, count INTEGER NOT NULL, first_id TEXT NOT NULL)")

    n_in = n_len = n_aa = n_cx = 0
    batch = []
    for i, (sid, seq) in enumerate(iter_fasta(args.input)):
        if args.limit and i >= args.limit:
            n_in = i
            break
        n_in = i + 1
        if not (args.min_len <= len(seq) <= args.max_len):
            continue
        n_len += 1
        if any(c not in AA for c in seq):
            continue
        n_aa += 1
        # 低复杂度：单一残基占比
        from collections import Counter
        if Counter(seq).most_common(1)[0][1] / len(seq) > args.max_single_frac:
            continue
        n_cx += 1
        batch.append((seq, sid))
        if len(batch) >= 50000:
            conn.executemany("INSERT INTO master(seq,count,first_id) VALUES(?,1,?) "
                             "ON CONFLICT(seq) DO UPDATE SET count=count+1", batch)
            conn.commit()
            batch.clear()
    if batch:
        conn.executemany("INSERT INTO master(seq,count,first_id) VALUES(?,1,?) "
                         "ON CONFLICT(seq) DO UPDATE SET count=count+1", batch)
        conn.commit()

    n_unique = conn.execute("SELECT COUNT(*) FROM master").fetchone()[0]
    with open(os.path.join(args.out_dir, "unique.faa"), "w") as out, \
         open(os.path.join(args.out_dir, "all_sORF_master.tsv"), "w", newline="") as m:
        m.write("seq\tlength\tcount\tfirst_id\n")
        for seq, count, first_id in conn.execute("SELECT seq,count,first_id FROM master ORDER BY rowid"):
            out.write(f">{first_id}\n{seq}\n")
            m.write(f"{seq}\t{len(seq)}\t{count}\t{first_id}\n")
    qc = {"input_records": n_in, "after_length": n_len, "after_alphabet": n_aa,
          "after_complexity": n_cx, "unique_sequences": n_unique,
          "duplicates_removed": n_cx - n_unique,
          "dup_reduction_ratio": round(1 - n_unique / max(n_cx, 1), 4),
          "min_len": args.min_len, "max_len": args.max_len}
    with open(os.path.join(args.out_dir, "qc_standardize.json"), "w") as fh:
        json.dump(qc, fh, indent=2, ensure_ascii=False)
    print(json.dumps(qc, indent=2, ensure_ascii=False))
    print("QC 门槛：dup_reduction_ratio 在 0.2–0.95 之间属正常；>>0.95 说明输入高度同源（检查是否误重复送入）；≈0 说明数据近乎全唯一（检查是否为已去重数据）")

if __name__ == "__main__":
    main()
