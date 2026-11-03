#!/usr/bin/env python3
"""Validate calibrated instruction samples and the stack-free m4 assembly leaf."""

import argparse
import csv
import io
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--kernel-dasm", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    log = args.log.read_text()
    if "OK: chacha20 instruction profile" not in log or "HIT GOOD TRAP" not in log:
        raise ValueError("Profile did not finish successfully")
    if re.search(r"ERROR:|HIT BAD TRAP|HIT CRITICAL ERROR", log):
        raise ValueError("Profile contains an error")
    csv_text = "\n".join(line for line in log.splitlines() if line.startswith("PROFILE,"))
    records = []
    grouped = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        record = {key: int(row[key]) for key in ("bytes", "raw", "empty", "body", "vlen_bits")}
        record["impl"] = row["impl"]
        record["cycles"] = None
        if record["body"] != record["raw"] - record["empty"] + 1:
            raise ValueError("Invalid empty-call calibration")
        record["instructions_per_byte"] = record["body"] / record["bytes"]
        bucket = grouped.setdefault(record["bytes"], {})
        if record["impl"] in bucket:
            raise ValueError("Duplicate sample")
        bucket[record["impl"]] = record["body"]
        records.append(record)
    comparisons = []
    for n, samples in sorted(grouped.items()):
        if set(samples) != {"boring", "rvv", "riscv", "riscv_xor", "riscv_m4"}:
            raise ValueError("Missing implementation")
        if n >= 256 and not samples["riscv_m4"] < samples["riscv_xor"] < samples["riscv"]:
            raise ValueError("Instruction regression")
        if n % 256 == 0 and {r["vlen_bits"] for r in records} == {128}:
            if samples["riscv_m4"] != 53 + 395 * (n // 256):
                raise ValueError("Unexpected full-batch instruction count")
        comparisons.append({
            "bytes": n,
            "body_instructions": samples,
            "m4_reduction_vs_original": 1 - samples["riscv_m4"] / samples["riscv"],
            "m4_reduction_vs_rvv": 1 - samples["riscv_m4"] / samples["rvv"],
        })
    kernel = args.kernel_dasm.read_text()
    instructions = []
    for line in kernel.splitlines():
        match = re.match(r"\s*[0-9a-f]+:\s+[0-9a-f]+\s+([a-z0-9.]+)\s*(.*)", line)
        if match:
            instructions.append(match.groups())
    if not instructions or re.search(r"\bsp\b", kernel):
        raise ValueError("Assembly leaf is missing or accesses the stack")
    if any(re.match(r"(?:vs[1248]r|vl[1248]re)", op) for op, _ in instructions):
        raise ValueError("Whole-register spill/reload in m4 leaf")
    vector_configs = sorted({operands for op, operands in instructions if op.startswith("vset")})
    if any("e32,m4" not in c and "e16,m2" not in c for c in vector_configs):
        raise ValueError("Unexpected data/index vector configuration")
    result = {
        "measurement": "NEMU rdinstret, one call per length and implementation",
        "cycles_measured": False,
        "samples": records,
        "comparisons": comparisons,
        "kernel": {
            "symbol": "riscv_chacha20_m4_blocks",
            "static_instructions": len(instructions),
            "stack_bytes": 0,
            "spill_store_bytes": 0,
            "spill_load_bytes": 0,
            "vector_configs": vector_configs,
            "scope": "assembly full-four-block leaf only; C wrapper and tail are excluded",
        },
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(f"PASS samples={len(records)} lengths={len(grouped)} stack-free m4 leaf={len(instructions)} instructions")
    for row in comparisons:
        if row["bytes"] in (256, 65536, 1048576):
            print(row)


if __name__ == "__main__":
    main()
