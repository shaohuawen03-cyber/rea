#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
0.5 阶段：按样本分层抽样，合成 pilot FASTA（不是取前 N 条）。
输入：--manifest TSV（file_path<TAB>sample_id[<TAB>assembly_id]）
分配：按各文件记录数比例分配，小样本保底 --min-per-file 条，总量尽量贴近 --target。
抽样：每文件一次标准 reservoir sampling（--seed 固定），O(k) 内存、与文件顺序无关。
输出：pilot.faa（header 重写为 >{sample_id}|{orig_header}）+ pilot_sources.tsv + pilot.faa.qc.json
用法：00b_make_pilot.py --manifest input_manifest.tsv --target 100000 --out work/pilot_100k/pilot.faa [--seed 7]
"""
import argparse, json, os, random, sys


def iter_fasta(path):
    name, buf = None, []
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(buf).upper()
                name, buf, = line[1:].strip(), []
            else:
                buf.append(line.strip())
        if name is not None:
            yield name, "".join(buf).upper()


def count_records(path):
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            n += chunk.count(b"\n>")
        fh.seek(0)
        if fh.read(1) == b">":
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--target", type=int, default=100_000)
    ap.add_argument("--min-per-file", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    files = []
    with open(args.manifest) as fh:
        first = fh.readline()
        if not first.lower().startswith(("file_path", "path")):
            fh.seek(0)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                files.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    assert files, "empty manifest"

    counts = []
    for f, sid, asm in files:
        c = count_records(f)
        if c == 0:
            print(f"warning: {f} 空文件，跳过", file=sys.stderr)
        counts.append([f, sid, asm, c])
    total = sum(c[3] for c in counts)
    assert total > 0, "所有输入文件为空"

    alloc = {}
    for f, sid, asm, c in counts:
        alloc[f] = max(args.min_per_file if c else 0, int(args.target * c / total))
    over = sum(alloc.values()) - args.target
    if over > 0:
        scale = args.target / sum(alloc.values())
        alloc = {k: max(1, int(v * scale)) for k, v in alloc.items()}

    rng = random.Random(args.seed)
    n_written = 0
    sources = args.out + ".sources.tsv"
    with open(args.out, "w") as out, open(sources, "w") as src:
        src.write("sample_id\torig_header\tnew_header\n")
        for f, sid, asm, c in counts:
            k = alloc[f]
            if k <= 0:
                continue
            reservoir = []
            for i, (header, seq) in enumerate(iter_fasta(f)):
                if len(reservoir) < k:
                    reservoir.append((header, seq))
                else:
                    j = rng.randrange(i + 1)
                    if j < k:
                        reservoir[j] = (header, seq)
            print(f"{sid}: {c} records -> {len(reservoir)} sampled", file=sys.stderr)
            for header, seq in reservoir:
                new = f"{sid}|{header}"
                out.write(f">{new}\n{seq}\n")
                src.write(f"{sid}\t{header}\t{new}\n")
                n_written += 1

    with open(args.out + ".qc.json", "w") as fh:
        json.dump({"target": args.target, "picked": n_written, "seed": args.seed,
                   "files": [{"file": f, "sample_id": sid, "assembly_id": asm,
                              "records": c, "allocated": alloc[f]}
                             for f, sid, asm, c in counts]},
                  fh, indent=2, ensure_ascii=False)
    print(f"pilot written: {args.out} ({n_written} records); sources: {sources}")

if __name__ == "__main__":
    main()
