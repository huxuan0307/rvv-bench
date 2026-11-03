#!/usr/bin/env python3
"""Generate explicit RV64 RVV P1 workloads and an auditable manifest."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asm", required=True)
    ap.add_argument("--header", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--loops", type=int, default=64, choices=(16, 64, 256))
    ap.add_argument("--suite", default="all", choices=("all", "alu", "fma", "fmavv", "load", "indexed", "mask"))
    ap.add_argument("--samples", type=int, default=3)
    args = ap.parse_args()
    for name in (args.asm, args.header, args.manifest):
        Path(name).parent.mkdir(parents=True, exist_ok=True)
    asm = [".text", ".align 2"]
    cases = []

    def add(cid, kind, opcode, sew, lmul, avl, active, target_per_loop,
            setup, warm, timed, reset, check, *, index_eew=0, index_lmul="",
            pattern="none", chains=0, notes=""):
        suite_kind = {"alu_vl1": "alu", "fma_chains": "fma",
                      "load_emul_control": "load", "indexed_load": "indexed",
                      "indexed_store": "indexed", "sparse_mask": "mask"}[kind]
        if kind == 'fma_chains' and opcode == 'vfmadd.vv':
            suite_kind = 'fmavv'
        if args.suite not in ("all", suite_kind):
            return
        sym = f"p1_{cid}"
        asm.extend([f".global {sym}", f"{sym}:", *setup,
                    "  csrr t2, vl", "  la t3, p1_actual_vl", "  sd t2, (t3)",
                    f"  li t4, {args.loops}", "1:"])
        asm.extend(warm)
        asm.extend(["  addi t4, t4, -1", "  bnez t4, 1b", *reset,
                    "  csrr t5, cycle", f"  li t4, {args.loops}", "2:"])
        asm.extend(timed)
        asm.extend(["  addi t4, t4, -1", "  bnez t4, 2b"])
        if kind == "indexed_store":
            asm.append("  fence rw,rw")
        asm.extend(["  csrr t6, cycle", "  sub a0, t6, t5", "  ret",
                    f".global p1_check_{cid}", f"p1_check_{cid}:", *check, "  ret"])
        cases.append({"id": cid, "kind": kind, "opcode": opcode, "sew": sew,
                      "samples": args.samples, "data_eew": 8 if kind == "load_emul_control" else sew,
                      "lmul": lmul, "avl": avl, "index_eew": index_eew,
                      "index_lmul": index_lmul, "mask_pattern": pattern,
                      "chains": chains, "warmup_loops": args.loops, "timed_loops": args.loops,
                      "target_insns": target_per_loop * args.loops,
                      "active_expected": active, "asm": sym,
                      "check": f"p1_check_{cid}", "status": "pending",
                      "skip_reason": "", "timing_notes": notes})

    # VL=1 e32 LMUL scan. Sources are fixed, non-overlapping groups.
    for lmul, vtype, dst, left, right in (
        ("m1", "e32,m1", "v8", "v16", "v24"),
        ("m2", "e32,m2", "v8", "v12", "v16"),
        ("m4", "e32,m4", "v8", "v16", "v24"),
        ("m8", "e32,m8", "v8", "v16", "v24"),
    ):
        for op, expected in (("vadd.vv", 3), ("vmul.vv", 2)):
            cid = f"{op.split('.')[0]}_e32{lmul}_vl1"
            setup = [f"  vsetivli zero,1,{vtype},ta,ma", f"  vmv.v.i {left},1",
                     f"  vmv.v.i {right},2", f"  vmv.v.i {dst},0"]
            body = [f"  {op} {dst},{left},{right}"] * 32
            check = [f"  vsetivli zero,1,{vtype},ta,ma", f"  vmv.x.s t0,{dst}",
                     f"  li t1,{expected}", "  bne t0,t1,9f", "  li a0,0",
                     "  j 10f", "9:", "  li a0,1", "10:"]
            add(cid, "alu_vl1", op, 32, lmul, 1, 1, 32, setup, body, body,
                [f"  vmv.v.i {dst},0"], check)

    # FMA: all rows execute 96 target instructions/loop, with independent vd chains.
    for operation, chains in ((op, c) for op in ('vfmacc', 'vfmadd') for c in (1, 8, 16, 24)):
        cid = f"{operation}_e64m1_c{chains}"
        regs = [8 + i for i in range(chains)]
        init = ["  li t0,0x3ff0000000000000", "  vmv.v.x v1,t0"]
        init += [f"  vmv.v.x v{reg},t0" for reg in regs]
        setup = ["  vsetivli zero,2,e64,m1,ta,ma", *init,
                 "  li t0,0x3ff0000000000000", "  fmv.d.x ft0,t0"]
        body = [f"  {operation}.vf v{reg},ft0,v1" for reg in regs] * (96 // chains)
        check = ["  vsetivli zero,2,e64,m1,ta,ma"]
        import struct
        expected_bits = struct.unpack("<Q", struct.pack("<d", 1.0 + (96 * args.loops) // chains))[0]
        for reg in regs:
            check += [f"  vmv.x.s t0,v{reg}", f"  li t1,0x{expected_bits:016x}",
                      "  bne t0,t1,9f", f"  vslidedown.vi v2,v{reg},1",
                      "  vmv.x.s t0,v2", "  bne t0,t1,9f"]
        check += ["  li a0,0", "  j 10f", "9:", "  li a0,1", "10:"]
        add(cid, "fma_chains", f"{operation}.vf", 64, "m1", 2, 2, 96,
            setup, body, body, init, check, chains=chains,
            notes="96 target instructions/loop; finite binary64 1.0; independent accumulators")

    # Direct .vv control: vd = vd * v1 + v3, both sources immutable 1.0.
    for chains in (1, 8, 16, 24):
        regs = list(range(8, 8 + chains))
        cid = f'vfmadd_vv_e64m1_c{chains}'
        init = ['  li t0,0x3ff0000000000000', '  vmv.v.x v1,t0', '  vmv.v.x v3,t0']
        init += [f'  vmv.v.x v{reg},t0' for reg in regs]
        setup = ['  vsetivli zero,2,e64,m1,ta,ma', '  fsflags zero', *init]
        body = [f'  vfmadd.vv v{reg},v1,v3' for reg in regs] * (96 // chains)
        import struct
        bits = struct.unpack('<Q', struct.pack('<d', 1.0 + 96 * args.loops // chains))[0]
        check = ['  vsetivli zero,2,e64,m1,ta,ma', '  frflags t0', '  bnez t0,9f']
        for reg in regs:
            check += [f'  vmv.x.s t0,v{reg}', f'  li t1,0x{bits:016x}', '  bne t0,t1,9f',
                      f'  vslidedown.vi v2,v{reg},1', '  vmv.x.s t0,v2', '  bne t0,t1,9f']
        check += ['  li a0,0', '  j 10f', '9:', '  li a0,1', '10:']
        add(cid, 'fma_chains', 'vfmadd.vv', 64, 'm1', 2, 2, 96,
            setup, body, body, init, check, chains=chains,
            notes='vd=vd*1+1; immutable v1/v3; all chains and both lanes checked; fflags=0; 96 targets/loop')

    # EMUL(data)=1 load controls; fixed and rotating both issue 8 loads per loop.
    for sew, lmul, vtype in ((8, "m1", "e8,m1"), (64, "m8", "e64,m8")):
        for rotating in (False, True):
            cid = f"vle8_e{sew}{lmul}_{'rotate' if rotating else 'fixed'}"
            dsts = [f"v{8+i}" for i in range(8)] if rotating else ["v8"] * 8
            setup = [f"  vsetivli zero,2,{vtype},tu,ma", "  la t0,p1_load_mem",
                     "  vsetivli zero,16,e8,m1,ta,ma", "  vmv.v.i v8,-1", "  vmv.v.i v9,-1",
                     "  vmv.v.i v10,-1", "  vmv.v.i v11,-1", "  vmv.v.i v12,-1",
                     "  vmv.v.i v13,-1", "  vmv.v.i v14,-1", "  vmv.v.i v15,-1",
                     f"  vsetivli zero,2,{vtype},tu,ma"]
            loads = [f"  vle8.v {r},(t0)" for r in dsts]
            check = ["  vsetivli zero,16,e8,m1,ta,ma", "  li t0,-1", "  vmv.v.x v16,t0",
                     "  vsetivli zero,2,e8,m1,tu,ma", "  li t0,33", "  vmv.v.x v16,t0",
                     "  vsetivli zero,16,e8,m1,ta,ma"]
            for reg in sorted(set(dsts)):
                check += [f"  vmsne.vv v0,{reg},v16", "  vcpop.m t0,v0", "  bnez t0,9f"]
            check += ["  li a0,0", "  j 10f", "9:", "  li a0,1", "10:"]
            add(cid, "load_emul_control", "vle8.v", sew, lmul, 2, 2, 8,
                setup, loads, loads, setup, check, notes="EMUL=1; eight loads/loop; fixed-vd vs v8..v15 rotation")

    # Indexed families: e32,m1 data; index vector EEW/EMUL matches encoded EEW.
    index_lmul = {8: "mf4", 16: "mf2", 32: "m1", 64: "m2"}
    for family in ("vluxei", "vloxei", "vsuxei", "vsoxei"):
        is_store = family.startswith("vs")
        for ieew in (8, 16, 32, 64):
            cid = f"{family}{ieew}_e32m1_permuted"
            ilmul = index_lmul[ieew]
            setup = ["  vsetivli zero,4,e32,m1,ta,ma", "  la t0,p1_store_mem" if is_store else "  la t0,p1_mem",
                     "  li t1,7", "  vmv.v.x v16,t1",
                     f"  vsetivli zero,4,e{ieew},{ilmul},ta,ma",
                     f"  la t1,p1_index_{ieew}", f"  vle{ieew}.v v24,(t1)",
                     "  vsetivli zero,4,e32,m1,ta,ma"]
            if is_store:
                setup += ["  la t1,p1_store_mem", "  li t2,0x5a5a5a5a",
                          f"  li t3,{args.loops * 8 + 1}", "3:", "  sw t2,(t1)",
                          "  addi t1,t1,4", "  addi t3,t3,-1", "  bnez t3,3b",
                          "  vid.v v16", "  vadd.vi v16,v16,7"]
                op = f"  {family}{ieew}.v v16,(t0),v24"
                timed = [op, "  addi t0,t0,32"]
                reset = ["  la t0,p1_store_mem", "  li t1,0x5a5a5a5a",
                         f"  li t3,{args.loops * 8 + 1}", "5:", "  sw t1,(t0)",
                         "  addi t0,t0,4", "  addi t3,t3,-1", "  bnez t3,5b",
                         "  la t0,p1_store_mem"]
                warm = [op, "  addi t0,t0,32"]
                check = ["  la t0,p1_store_mem", f"  li t4,{args.loops}", "4:"]
                # Check each 32-byte region, including every untouched hole.
                for word, value in enumerate((8, 0x5a5a5a5a, 10, 7,
                                               0x5a5a5a5a, 0x5a5a5a5a, 0x5a5a5a5a, 9)):
                    check += [f"  lw t1,{word * 4}(t0)", f"  li t2,{value}", "  bne t1,t2,9f"]
                check += ["  addi t0,t0,32", "  addi t4,t4,-1", "  bnez t4,4b",
                          "  lw t1,0(t0)", "  li t2,0x5a5a5a5a", "  bne t1,t2,9f",
                          "  li a0,0", "  j 10f", "9:", "  li a0,1", "10:"]
                kind = "indexed_store"
            else:
                op = f"  {family}{ieew}.v v16,(t0),v24"
                timed = [op]
                warm = [op]
                reset = ["  vmv.v.i v16,0"]
                # Independent expected values for byte offsets 12,0,28,8.
                check = ["  vsetivli zero,4,e32,m1,ta,ma"]
                for lane, value in enumerate((10, 7, 14, 9)):
                    if lane:
                        check += [f"  vslidedown.vi v2,v16,{lane}", "  vmv.x.s t0,v2"]
                    else:
                        check += ["  vmv.x.s t0,v16"]
                    check += [f"  li t1,{value}", "  bne t0,t1,9f"]
                check += ["  li a0,0", "  j 10f", "9:", "  li a0,1", "10:"]
                kind = "indexed_load"
            add(cid, kind, f"{family}{ieew}.v", 32, "m1", 4, 4, 1,
                setup, warm, timed, reset, check, index_eew=ieew,
                index_lmul=ilmul,
                notes="permuted byte offsets 12,0,28,8; unique lane data; store advances base 32 bytes/iteration; all holes and post guard checked; store fence included in cycles")

    # Sparse-mask vadd e8,m1,VLMAX=16. Destination inactive lanes retain 99.
    patterns = {
        "all0": (["  vmv.v.i v0,0"], 0), "all1": (["  vmv.v.i v0,-1"], 16),
        "single": (["  vid.v v24", "  vmseq.vi v0,v24,0"], 1),
        "every8": (["  vid.v v24", "  vand.vi v24,v24,7", "  vmseq.vi v0,v24,0"], 2),
        "alternating": (["  vid.v v24", "  vand.vi v24,v24,1", "  vmseq.vi v0,v24,0"], 8),
    }
    for pattern, (mask, active) in patterns.items():
        cid = f"vadd_e8m1_mask_{pattern}"
        setup = ["  vsetivli zero,16,e8,m1,ta,mu", "  li t0,99", "  vmv.v.x v8,t0",
                 "  vmv.v.i v16,1", "  vmv.v.i v17,2", *mask]
        body = ["  vadd.vv v8,v16,v17,v0.t"] * 32
        check = ["  vsetivli zero,16,e8,m1,ta,mu", "  vcpop.m t0,v0",
                 "  la t1,p1_active", "  sd t0,(t1)", "  li t1," + str(active),
                 "  bne t0,t1,9f", "  li t0,99", "  vmv.v.x v16,t0",
                 f"  la t0,p1_expected_{pattern}", "  vle8.v v16,(t0)", "  vmsne.vv v0,v8,v16",
                 "  vcpop.m t0,v0", "  seqz a0,t0", "  xori a0,a0,1", "  j 10f",
                 "9:", "  li a0,1", "10:"]
        add(cid, "sparse_mask", "vadd.vv.masked", 8, "m1", 16, active, 32,
            setup, body, body, setup, check, pattern=pattern,
            notes="mask setup/check outside timing; checker verifies active=3 and inactive sentinel=99")

    # m8 mask controls distinguish nonempty chunks from entirely empty chunks.
    mask_data = []
    for pattern, enabled in (
        ('all0', []), ('all1', list(range(128))), ('single', [0]),
        ('every8', list(range(0, 128, 8))),
        ('one_per_chunk', list(range(0, 128, 16))),
        ('alternating_chunks', list(range(0, 16)) + list(range(32, 48)) + list(range(64, 80)) + list(range(96, 112))),
    ):
        cid = f'vadd_e8m8_mask_{pattern}'
        active = len(enabled)
        bitset = sum(1 << i for i in enabled)
        mask_bytes = [(bitset >> (8 * i)) & 255 for i in range(16)]
        expected_bytes = [3 if i in enabled else 99 for i in range(128)]
        setup = ['  li t0,128', '  vsetvli zero,t0,e8,m8,tu,mu',
                 '  li t0,99', '  vmv.v.x v8,t0', '  vmv.v.i v16,1', '  vmv.v.i v24,2',
                 f'  la t0,p1_mask_{pattern}', '  vlm.v v0,(t0)']
        body = ['  vadd.vv v8,v16,v24,v0.t'] * 32
        check = ['  li t0,128', '  vsetvli zero,t0,e8,m8,tu,mu',
                 '  vcpop.m t0,v0', '  la t1,p1_active', '  sd t0,(t1)',
                 f'  li t1,{active}', '  bne t0,t1,9f',
                 f'  la t0,p1_expected_{pattern}', '  vle8.v v16,(t0)',
                 '  vmsne.vv v0,v8,v16', '  vcpop.m a0,v0', '  j 10f',
                 '9:', '  li a0,1', '10:']
        add(cid, 'sparse_mask', 'vadd.vv.masked', 8, 'm8', 128, active, 32,
            setup, body, body, setup, check, pattern=pattern,
            notes='tu/mu; eight 16-byte chunks; mask setup and full 128-byte check outside timing')
        mask_data += [f'p1_mask_{pattern}:', '.byte ' + ','.join(map(str, mask_bytes)),
                      f'p1_expected_{pattern}:', '.byte ' + ','.join(map(str, expected_bytes))]

    mask_data += ['p1_expected_alternating:', '.byte ' + ','.join(str(3 if i % 2 == 0 else 99) for i in range(16))]
    index_data = []
    for ieew, directive in ((8, '.byte'), (16, '.half'), (32, '.word'), (64, '.dword')):
        index_data += ['.align 3', f'p1_index_{ieew}:', f'{directive} 12,0,28,8']
    asm.extend(['.data', '.align 4', *mask_data, *index_data])
    # Static test data, 256 32-bit words; indexed offsets 0,4,8,12 address words 7..10.
    asm.extend([".data", ".align 6", ".global p1_mem", "p1_mem:",
                ".word 7,8,9,10,11,12,13,14", ".space 64496", ".align 6",
                ".global p1_store_mem", "p1_store_mem:", ".space 65536", ".align 6",
                ".global p1_load_mem", "p1_load_mem:", ".byte 33,33,33,33", ".space 65532",
                ".align 3", ".global p1_actual_vl", "p1_actual_vl:", ".dword 0",
                ".global p1_active", "p1_active:", ".dword 0"])
    Path(args.asm).write_text("\n".join(asm) + "\n")

    hdr = ["/* generated by p1_gen.py */",
           "struct p1_case { const char *id,*kind,*opcode,*lmul,*pattern,*status,*skip; unsigned sew,data_eew,avl,index_eew,chains,target_insns,active,samples; unsigned long (*run)(void); unsigned (*check)(void); };" ]
    for c in cases:
        hdr.append(f"unsigned long {c['asm']}(void); unsigned p1_check_{c['id']}(void);")
    hdr.append("#define P1_CASES \\")
    for i, c in enumerate(cases):
        line = (f'{{"{c["id"]}","{c["kind"]}","{c["opcode"]}","{c["lmul"]}",'
                f'"{c["mask_pattern"]}","{c["status"]}","{c["skip_reason"]}",'
                f'{c["sew"]},{c["data_eew"]},{c["avl"]},{c["index_eew"]},{c["chains"]},'
                f'{c["target_insns"]},{c["active_expected"]},{c["samples"]},p1_{c["id"]},p1_check_{c["id"]}}}')
        hdr.append("  " + line + (", \\" if i + 1 < len(cases) else ""))
    Path(args.header).write_text("\n".join(hdr) + "\n")
    manifest = {"schema_version": 1, "suite": "p1-explicit-workloads",
                "target": "RV64 baremetal RVV v1.0, VLEN=128 profile",
                "samples": args.samples,
                "timing": {"warmup_loops": args.loops, "timed_loops": args.loops,
                           "store_fence_in_cycles": True},
                "suite_filter": args.suite,
                "cases": cases,
                "coverage_limits": ["indexed representatives use data e32,m1 with each legal index EEW and matching index EMUL",
                                    "VL1 ALU LMUL scan is m1/m2/m4/m8; fractional LMUL deferred",
                                    "mask covers e8 m1/m8; m8 distinguishes one_per_chunk and alternating_chunks; other SEW and policies deferred"]}
    Path(args.manifest).write_text(json.dumps(manifest, indent=2) + "\n")

if __name__ == "__main__":
    main()
