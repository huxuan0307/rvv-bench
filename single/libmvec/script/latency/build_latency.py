#!/usr/bin/env python3
"""Build one baremetal libmvec function-latency binary.

Legacy LAT builds use closed-loop map probes. Paired builds use exact corpus
inputs for dependent latency and independent throughput windows. Both record
source, data, archive and tool identities and audit the compiled probe shape;
a successful build does not establish runtime correctness or performance.

Typical use::

    python3 -B single/libmvec/script/latency/build_latency.py \\
        --prefix /path/to/prefix --output /new/dir --tag calib --groups calib

All build products and diagnostics go under the new ``--output`` directory.
This command does not execute a target binary.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIB = HERE.parents[1]
REPO = HERE.parents[3]
LATENCY = LIB / "latency"

RUNTIME_SRC = REPO / "single" / "common" / "am_runtime.c"
TLS_LD = REPO / "autovec" / "tls.ld"
PAIR_TLS_LD = LIB / "runtime" / "tls-safe.ld"
PAIR_TLS_ANCHOR = LIB / "runtime" / "tls_anchor.c"
PAIR_ENTRY_RNE = LIB / "runtime" / "entry_rne.c"
LINK_LD = REPO / "thirdparty" / "link.ld"
AM_A = REPO / "thirdparty" / "am-riscv64-xs.a"
KLIB_A = REPO / "thirdparty" / "klib-riscv64-xs.a"

RUNTIME_SOURCES = [RUNTIME_SRC, REPO / "autovec" / "av_runtime.c",
                   REPO / "autovec" / "av_runtime.h", TLS_LD, LINK_LD]
SOURCES = [LATENCY / name for name in ("latency_driver.c", "latency_main.c",
                                      "latency_noop.c", "latency_map.h",
                                      "latency_probe.h")] + RUNTIME_SOURCES

# group -> (driver TU tag, frozen header, scalar libm oracle, entries)
#
# The five new families share one shape: 8 ABI variants (fixed/VLMAX x
# unmasked/masked, m1/m2) in latency mode, the same 8 in independent-call
# throughput mode, plus one scalar libm control chain.  exp/log/sin/cos/pow
# check against the scalar libm of the same archive, so they need no frozen
# MPFR header (atan/acos/atan2/log10 still use theirs).
FAMILIES = {
    "exp": dict(scalar="exp", binary=False, vfp_lat=3),
    "log": dict(scalar="log", binary=False, vfp_lat=2),
    "sin": dict(scalar="sin", binary=False, vfp_lat=0),
    "cos": dict(scalar="cos", binary=False, vfp_lat=0),
    "pow": dict(scalar="pow", binary=True, vfp_lat=3),
}

# ABI variant suffix -> mangled symbol template.  {mid} is 'v' for the binary
# (pow) entries and empty otherwise; {fn} is the function name.  Fixed-size and
# VLMAX unmasked variants both take one vector; masked variants take the
# all-active predicate first (m1: vbool64_t, m2: vbool32_t).
ABI_VARIANTS = [
    ("m1_N2", "_ZGV1N2v{mid}_{fn}"),
    ("m1_Nx", "_ZGV1Nxv{mid}_{fn}"),
    ("m1_M2", "_ZGV1M2v{mid}_{fn}"),
    ("m1_Mx", "_ZGV1Mxv{mid}_{fn}"),
    ("m2_N4", "_ZGV2N4v{mid}_{fn}"),
    ("m2_Nx", "_ZGV2Nxv{mid}_{fn}"),
    ("m2_M4", "_ZGV2M4v{mid}_{fn}"),
    ("m2_Mx", "_ZGV2Mxv{mid}_{fn}"),
]


def family_entries(fn):
    """8 latency variants + the same 8 in throughput mode + scalar control."""
    entries = []
    for suffix, _ in ABI_VARIANTS:
        entries.append(f"{fn}_{suffix}")
        entries.append(f"{fn}_{suffix}_tput")
    entries.append(f"{fn}_scalar")
    return entries


GROUPS = {
    "calib": dict(header=None, entries=[
        "nop_addi", "nop_addi64", "noop_m1", "noop_m2", "noop_map_m1",
        "noop_map_m2", "noop_mask_map_m2", "noop_mask_map_m4", "map_m1",
        "map_m2",
    ]),
    "atan": dict(header="atan.h", entries=["atan_m1_Nx", "atan_m2_Nx"]),
    "acos_nx": dict(header="acos.h", entries=["acos_m1_Nx", "acos_m2_Nx"]),
    "acos_masked": dict(header="acos.h", entries=["acos_m1_M2", "acos_m2_M4"]),
}
for _fn, _meta in FAMILIES.items():
    GROUPS[_fn] = dict(header=None, scalar=_meta["scalar"],
                       binary=_meta["binary"],
                       entries=family_entries(_fn))

# Extra scalar control: the historical exp map driven to its saturated
# operating point, re-measured with the branchless clamp.  It is a control,
# not a replacement for exp_scalar: same orbit as the old probe, but a valid
# data path.  The build's dependency audit treats it like any scalar entry.
GROUPS["exp"]["entries"].append("exp_scalar_sat")


# probe table symbol -> probe function symbol (as emitted by latency_driver.c)
PROBE_FUNCS = {
    "nop_addi": "probe_nop_addi",
    "nop_addi64": "probe_nop_addi64",
    "noop_m1": "probe_noop_m1",
    "noop_m2": "probe_noop_m2",
    "noop_map_m1": "probe_noop_map_m1",
    "noop_map_m2": "probe_noop_map_m2",
    "noop_mask_map_m2": "probe_noop_mask_map_m2",
    "noop_mask_map_m4": "probe_noop_mask_map_m4",
    "map_m1": "probe_map_m1",
    "map_m2": "probe_map_m2",
    "atan_m1_Nx": "probe_atan_m1_Nx",
    "atan_m2_Nx": "probe_atan_m2_Nx",
    "acos_m1_Nx": "probe_acos_m1_Nx",
    "acos_m2_Nx": "probe_acos_m2_Nx",
    "acos_m1_M2": "probe_acos_m1_M2",
    "acos_m2_M4": "probe_acos_m2_M4",
}
for _fn in FAMILIES:
    for _entry in GROUPS[_fn]["entries"]:
        PROBE_FUNCS[_entry] = "probe_" + _entry

# Audit expectation per entry: expected callee (None = no call), minimum number
# of vector FP arithmetic instructions in the window, loop back-edge required.
AUDIT = {
    "nop_addi": dict(callee=None, vfp_min=0, addi_min=32, loop=False,
                     note="unrolled dependency chain, 1 addi per step (K>=4 reuses the K=32 tail)"),
    "nop_addi64": dict(callee=None, vfp_min=0, addi_min=2048, loop=False,
                       note="unrolled dependency chain, 64 addi per step (K>=4 reuses the K=32 tail)"),
    "noop_m1": dict(callee="latency_noop_m1", vfp_min=0, loop=True,
                    note="empty ABI call chain, no map"),
    "noop_m2": dict(callee="latency_noop_m2", vfp_min=0, loop=True,
                    note="empty ABI call chain, no map"),
    "noop_map_m1": dict(callee="latency_noop_m1", vfp_min=2, loop=True,
                        note="empty ABI call + 2-insn map"),
    "noop_map_m2": dict(callee="latency_noop_m2", vfp_min=2, loop=True,
                        note="empty ABI call + 2-insn map"),
    "noop_mask_map_m2": dict(callee="latency_noop_mask_m2", vfp_min=2, loop=True,
                             note="masked empty ABI call + 2-insn map"),
    "noop_mask_map_m4": dict(callee="latency_noop_mask_m4", vfp_min=2, loop=True,
                             note="masked empty ABI call + 2-insn map"),
    "map_m1": dict(callee=None, vfp_min=2, loop=True, note="map only, no call"),
    "map_m2": dict(callee=None, vfp_min=2, loop=True, note="map only, no call"),
    "atan_m1_Nx": dict(callee="_ZGV1Nxv_atan", vfp_min=2, loop=True),
    "atan_m2_Nx": dict(callee="_ZGV2Nxv_atan", vfp_min=2, loop=True),
    "acos_m1_Nx": dict(callee="_ZGV1Nxv_acos", vfp_min=2, loop=True),
    "acos_m2_Nx": dict(callee="_ZGV2Nxv_acos", vfp_min=2, loop=True),
    "acos_m1_M2": dict(callee="_ZGV1M2v_acos", vfp_min=2, loop=True),
    "acos_m2_M4": dict(callee="_ZGV2M4v_acos", vfp_min=2, loop=True),
}
# Scalar entries must also call the scalar libm symbol and must keep the
# next input on the FP data path: no conditional branch may skip the map, and
# the registers the call consumes must be tainted by the previous return.
SCALAR_MAP_MIN = {"exp": 3, "log": 4, "sin": 0, "cos": 0, "pow": 4}
for _fn, _meta in FAMILIES.items():
    _mid = "v" if _meta["binary"] else ""
    for _suffix, _tmpl in ABI_VARIANTS:
        _sym = _tmpl.format(mid=_mid, fn=_fn)
        _lat = f"{_fn}_{_suffix}"
        AUDIT[_lat] = dict(callee=_sym, vfp_min=_meta["vfp_lat"], loop=True)
        AUDIT[_lat + "_tput"] = dict(
            callee=_sym, vfp_min=0, loop=True,
            note="4 independent loop-invariant calls per step, no data dependency")
    AUDIT[f"{_fn}_scalar"] = dict(
        callee=_fn, vfp_min=0, loop=True,
        scalar_map_min=SCALAR_MAP_MIN[_fn],
        scalar_ret_inputs=2 if _meta["binary"] else 1,
        note="scalar libm control chain, branchless map")
AUDIT["exp_scalar_sat"] = dict(
    callee="exp", vfp_min=0, loop=True, scalar_map_min=3,
    scalar_ret_inputs=1,
    note="scalar libm control chain, saturated operating point")

VFP_MNEMONICS = ("vfmul", "vfadd", "vfsub", "vfmacc", "vfmadd", "vfnmacc",
                 "vfnmadd", "vfmsac", "vfmsub", "vfrsub", "vfdiv", "vfsqrt",
                 "vfmin", "vfmax", "vfmerge")

# scalar FP helpers for the audit of the libm control chains
SCALAR_FP_ARITH = ("fmul.d", "fadd.d", "fsub.d", "fdiv.d", "fsqrt.d",
                   "fmax.d", "fmin.d", "fmadd.d", "fmsub.d", "fnmadd.d",
                   "fnmsub.d", "fmv.d", "fneg.d", "fabs.d")
SCALAR_FP_LOAD = ("fld", "flw", "flh", "flq")
SCALAR_FP_STORE = ("fsd", "fsw", "fsh", "fsq")
SCALAR_FP_WRITE = SCALAR_FP_ARITH + SCALAR_FP_LOAD + ("fli.d",)
MEM_OPERAND_RE = re.compile(r"^(-?(?:0x[0-9a-f]+|\d+))\(([a-z0-9]+)\)$")
COND_BRANCH_RE = re.compile(
    r"^(?:c\.)?(?:beq|bne|blt|bge|bltu|bgeu|bgt|ble|bgtu|bleu|beqz|bnez|"
    r"bltz|bgez|blez|bgtz)\b")


def mem_operand(op):
    """('offset', 'base') for `8(sp)`-style operands, else None."""
    m = MEM_OPERAND_RE.match(op.strip())
    if not m:
        return None
    return int(m.group(1), 0), m.group(2)

NOOP_SYMBOLS = ["latency_noop_m1", "latency_noop_m2",
                "latency_noop_mask_m2", "latency_noop_mask_m4"]

COMMON = [
    "-O3", "-g", "-march=rva23u64", "-mabi=lp64d", "-mcmodel=medany",
    "-fno-pie", "-fno-stack-protector", "-fno-lto", "-ffp-contract=off",
    "-mrvv-vector-bits=scalable", "--param=gpr2vr-cost=0",
    "--param=fpr2vr-cost=0", "-Wall", "-Wextra", "-Wno-unused-parameter",
    "-I", str(REPO / "autovec"),
    "-Wno-unused-variable", "-Wno-unused-const-variable",
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(label, argv, outdir, cwd=None):
    """Run one build step, always keeping stdout/stderr in separate files."""
    logdir = outdir / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    stdout = logdir / f"{label}.stdout.log"
    stderr = logdir / f"{label}.stderr.log"
    with open(stdout, "wb") as so, open(stderr, "wb") as se:
        proc = subprocess.run(argv, cwd=str(cwd or os.getcwd()),
                              stdout=so, stderr=se)
    return proc.returncode, stdout, stderr


def must(label, argv, outdir, cwd=None):
    rc, stdout, stderr = run(label, argv, outdir, cwd)
    if rc != 0:
        sys.stderr.write(f"FAILED step={label} rc={rc}\n")
        sys.stderr.write(f"  stdout: {stdout}\n  stderr: {stderr}\n")
        tail = stderr.read_text(errors="replace").splitlines()[-30:]
        for line in tail:
            sys.stderr.write(f"  | {line}\n")
        raise SystemExit(rc)
    return stdout, stderr


def find_prefix_tools(args):
    prefix = Path(args.prefix).expanduser().resolve()
    triplet = args.triplet
    cc = Path(args.cc) if args.cc else prefix / "bin" / f"{triplet}-gcc"
    objdump = (Path(args.objdump) if args.objdump
               else prefix / "bin" / f"{triplet}-objdump")
    nm = Path(args.nm) if args.nm else prefix / "bin" / f"{triplet}-nm"
    objcopy = (Path(args.objcopy) if args.objcopy
               else prefix / "bin" / f"{triplet}-objcopy")
    missing = [str(p) for p in (cc, objdump, nm, objcopy) if not p.exists()]
    if missing:
        raise SystemExit("missing tool(s): " + ", ".join(missing))
    return prefix, cc, objdump, nm, objcopy


def find_lib(cc, name, prefix):
    """Resolve a library the way the compiler would; refuse a bare filename."""
    out = subprocess.run([str(cc), "-march=rva23u64", "-mabi=lp64d",
                          f"-print-file-name={name}"],
                         capture_output=True, text=True, check=True)
    path = out.stdout.strip()
    if not path or path == name:
        raise SystemExit(f"{name} not found in prefix {prefix}")
    return Path(path).resolve()


def manifest_lib(cc, prefix, name):
    path = find_lib(cc, name, prefix)
    return {"path": str(path), "sha256": sha256(path),
            "bytes": path.stat().st_size}


def version_of(exe):
    out = subprocess.run([str(exe), "--version"], capture_output=True,
                         text=True)
    return out.stdout.splitlines()[0] if out.stdout else "unknown"


def parse_data_header(path):
    """Pull the oracle constants out of a frozen generated header."""
    text = path.read_text()
    info = {}
    for line in text.splitlines():
        line = line.strip()
        for key in ("ORACLE_LIMIT", "DATA_COUNT"):
            if line.startswith(f"#define {key} "):
                info[key] = int(line.split()[-1])
    for key in ("input_bits", "reference_bits"):
        if f"{key}[" not in text:
            raise SystemExit(f"{path}: missing {key}[]")
        info[key] = True
    if "ORACLE_LIMIT" not in info or "DATA_COUNT" not in info:
        raise SystemExit(f"{path}: missing ORACLE_LIMIT/DATA_COUNT")
    return info


# --------------------------------------------------------------------------
# object-code audit
# --------------------------------------------------------------------------
def disassemble(objdump, path, outdir, label):
    """objdump -dr with relocations; result is parsed, not just stored."""
    stdout, _ = must(label, [str(objdump), "-d", "-r", "--no-show-raw-insn",
                             str(path)], outdir)
    text = stdout.read_text(errors="replace")
    (outdir / "disasm").mkdir(parents=True, exist_ok=True)
    (outdir / "disasm" / f"{label}.disasm.txt").write_text(text)
    return text


def parse_section_items(text, prefix=".text"):
    """Flatten objdump -dr output into (addr, kind, payload) items.

    Only the requested section is kept: in an object file .text and .rodata both
    start at address 0, so a bare address filter would mix them.
    """
    import re
    section_re = re.compile(r"^Disassembly of section ([^:]+):$")
    insn_re = re.compile(r"^\s*([0-9a-f]+):\t(.*)$")
    reloc_re = re.compile(r"^\s*([0-9a-f]+):[ \t]+(R_RISCV_\S+)[ \t]+(.*)$")
    items = []
    section = None
    for line in text.splitlines():
        m = section_re.match(line)
        if m:
            section = m.group(1)
            continue
        if section is None or not section.startswith(prefix):
            continue
        r = reloc_re.match(line)
        if r:
            items.append((int(r.group(1), 16), "reloc",
                          (r.group(2), r.group(3).strip())))
            continue
        i = insn_re.match(line)
        if i:
            items.append((int(i.group(1), 16), "insn", i.group(2).strip()))
    return items


def symbol_ranges(nm, path, outdir, label):
    """{symbol: (start, end)} for every defined text symbol, via nm -S."""
    stdout, stderr = must(label, [str(nm), "-S", "--defined-only", str(path)],
                          outdir)
    ranges = {}
    for line in stdout.read_text(errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            addr = int(parts[0], 16)
            size = int(parts[1], 16)
        except ValueError:
            continue
        if parts[2] not in ("t", "T", "w", "W"):
            continue
        ranges[parts[3]] = (addr, addr + max(size, 1))
    return ranges


# Scalar audit: map ABI register names to the f0..f31 canonical numbers so the
# taint propagation below can match `fa0 = fa0 + fs0` across aliases.
ABI_FREG = {}
for _i in range(8):
    ABI_FREG[f"ft{_i}"] = _i
    ABI_FREG[f"fa{_i}"] = 10 + _i
ABI_FREG["fs0"] = 8
ABI_FREG["fs1"] = 9
for _i in range(2, 12):
    ABI_FREG[f"fs{_i}"] = 18 + (_i - 2)
for _i in range(8, 12):
    ABI_FREG[f"ft{_i}"] = 28 + (_i - 8)


def fcanon(name):
    """Canonical f-register number for an ABI name, or None."""
    n = name.strip()
    if not n:
        return None
    if n[0] == "f" and n[1:].isdigit():
        return int(n[1:])
    return ABI_FREG.get(n)


def find_k_loop(insns):
    """Locate the dependency-chain K loop: the last backward branch.

    GCC ends the timed loop with a conditional branch but may follow it with
    an unconditional ``j`` to an epilogue placed earlier in the function; that
    jump is backward by address but is not the loop back-edge.  Prefer the
    last conditional backward branch, fall back to any backward branch for a
    bottom-tested loop.  Returns (loop_start, loop_end) or None.
    """
    cands = []
    for addr, insn in insns:
        if not insn:
            continue
        mnem = insn.split()[0]
        if not mnem.startswith(("j", "b", "c.j", "c.b")):
            continue
        m = re.search(r"\b([0-9a-f]+) <", insn)
        if not m:
            continue
        target = int(m.group(1), 16)
        if target < addr:
            cands.append((addr, target, bool(COND_BRANCH_RE.match(mnem))))
    if not cands:
        return None
    cond = [c for c in cands if c[2]]
    addr, target, _ = max(cond or cands)
    return target, addr


def audit_scalar_loop(entry, spec, insns, relocs):
    """Prove the scalar K loop is a real, branchless dependency chain.

    Checks, inside the last (K) loop only:
      * exactly one call relocation to the scalar libm symbol;
      * no conditional branch other than the loop back-edge, so no compare can
        select a constant in place of the mapped return value;
      * the map keeps at least ``scalar_map_min`` FP arithmetic instructions;
      * fa0 (and fa1 for pow) at the next call are tainted by the previous
        call's return value, with loads/constants clearing taint.
    """
    problems = []
    notes = []
    loop = find_k_loop(insns)
    if loop is None:
        return ["no backward branch (K loop missing)"], notes
    loop_start, loop_end = loop
    body = sorted((a, insn) for a, insn in insns
                  if loop_start <= a <= loop_end)
    calls = [a for a, rtype, target in relocs
             if loop_start <= a <= loop_end
             and (rtype.startswith("R_RISCV_CALL") or rtype == "R_RISCV_JAL")
             and target == spec["callee"]]
    if len(calls) != 1:
        problems.append(
            f"{len(calls)} call sites to {spec['callee']} in the K loop "
            f"(need exactly 1)")
        return problems, notes
    call_site = calls[0]
    notes.append(f"K loop {hex(loop_start)}..{hex(loop_end)}, "
                 f"real call at {hex(call_site)}")
    for a, insn in body:
        if not insn:
            continue
        if COND_BRANCH_RE.match(insn.split()[0]) and a != loop_end:
            problems.append(
                f"conditional branch inside the K loop at {hex(a)}: {insn}")
    arith = [insn for a, insn in body
             if insn and insn.split()[0] in SCALAR_FP_ARITH
             and insn.split()[0] != "fmv.d"]
    if len(arith) < spec["scalar_map_min"]:
        problems.append(
            f"only {len(arith)} scalar FP map insn(s), "
            f"need {spec['scalar_map_min']}")
    else:
        notes.append("scalar map: " + "; ".join(arith))
    # scan one iteration starting after the call and wrapping to the loop head
    order = [(a, insn) for a, insn in body if a > call_site] + \
            [(a, insn) for a, insn in body if a <= call_site]
    tainted = {10}  # fa0 carries the call's return value
    slots = set()   # (offset, base) of spilled tainted doubles
    required = [10] + ([11] if spec.get("scalar_ret_inputs", 1) == 2 else [])
    for _a, insn in order:
        if not insn:
            continue
        parts = insn.replace("\t", " ").split(None, 1)
        mnem = parts[0]
        if len(parts) < 2:
            continue
        ops = [op.strip() for op in parts[1].split(",")]
        if mnem in SCALAR_FP_STORE:
            src = fcanon(ops[0].split()[0])
            key = mem_operand(ops[1]) if len(ops) > 1 else None
            if key is not None:
                if src in tainted:
                    slots.add(key)
                else:
                    slots.discard(key)
            continue
        if mnem not in SCALAR_FP_WRITE:
            continue
        dest = fcanon(ops[0].split()[0])
        if dest is None:
            continue
        if mnem in SCALAR_FP_LOAD:
            key = mem_operand(ops[1]) if len(ops) > 1 else None
            if key is not None and key in slots:
                tainted.add(dest)
            else:
                tainted.discard(dest)
            continue
        srcs = [fcanon(op.split()[0]) for op in ops[1:]]
        srcs = [s for s in srcs if s is not None]
        if any(s in tainted for s in srcs):
            tainted.add(dest)
        else:
            tainted.discard(dest)
    missing = [f"f{r}" for r in required if r not in tainted]
    if missing:
        problems.append(
            "next call reads untainted " + ", ".join(missing)
            + " (return value does not reach the call input)")
    else:
        notes.append("tainted call inputs: " + ", ".join(f"f{r}" for r in required))
    return problems, notes


def audit_probe(entry, items, start, end, outdir):
    """Verify one probe body keeps the dependency chain the harness claims."""
    spec = AUDIT[entry]
    problems = []
    notes = []
    call_sites = []
    relocs = []
    insns = []
    for addr, kind, payload in items:
        if not start <= addr < end:
            continue
        if kind == "reloc":
            relocs.append((addr, payload[0], payload[1]))
        else:
            insns.append((addr, payload))
    # A call site is the relocation that names the callee; the auipc/jal pair it
    # annotates sits at the same address in a -mcmodel=medany -fno-pie build.
    for addr, rtype, target in relocs:
        if rtype.startswith("R_RISCV_CALL") or rtype == "R_RISCV_JAL":
            if target == spec["callee"]:
                call_sites.append(addr)
            elif spec["callee"] is not None and target.startswith("_ZGV"):
                problems.append(f"unexpected libmvec callee {target}")
    if spec["callee"] is None:
        stray = [t for _, _, t in relocs
                 if t.startswith("_ZGV") or t.startswith("latency_noop")]
        if stray:
            problems.append(f"unexpected calls: {sorted(set(stray))}")
        notes.append("no call site (expected)")
    elif not call_sites:
        problems.append(f"no call site to {spec['callee']}")
        found = sorted({t for _, k, t in relocs if k.startswith("R_RISCV_CALL")})
        if found:
            notes.append("call relocations found: " + ", ".join(found))
    else:
        notes.append(f"call sites: {', '.join(hex(a) for a in call_sites)}")

    vfp = [(a, insn) for a, insn in insns
           if insn and any(mn in insn.split()[0] for mn in VFP_MNEMONICS)]
    if len(vfp) < spec["vfp_min"]:
        problems.append(
            f"only {len(vfp)} vector FP insn(s), need {spec['vfp_min']}")
    else:
        notes.append(f"vector FP insns: {len(vfp)}")
        notes.append("map: " + "; ".join(f"{insn}" for _, insn in vfp[:2]))
    if spec["callee"] is not None and call_sites and spec["vfp_min"] > 0:
        first_call = min(call_sites)
        following = [a for a, _ in vfp if a > first_call]
        if not following:
            problems.append("map instructions do not follow the call site")
        else:
            notes.append("map first appears at " + hex(min(following)))

    if "addi_min" in spec:
        # a real dependency chain: rd == rs1, immediate 1 (addi a5,a5,1)
        dependent = [insn for _, insn in insns
                     if re.match(r"addi\s+([a-z0-9]+),\s*\1,\s*1$", insn)]
        if len(dependent) < spec["addi_min"]:
            problems.append(
                f"only {len(dependent)} dependent addi, need {spec['addi_min']}")
        else:
            notes.append(f"dependent addi (rd==rs1,imm=1): {len(dependent)}")

    # The callee and the map must sit inside the same back-edge loop.
    loop = find_k_loop(insns)
    if spec.get("loop", True):
        if loop is None:
            problems.append("no backward branch (K loop missing)")
        else:
            loop_start, loop_end = loop
            in_loop = [a for a in call_sites
                       if loop_start <= a <= loop_end]
            if spec["callee"] is not None and not in_loop:
                problems.append("no call site inside the K loop")
            notes.append(f"K loop back-edge at {hex(loop_end)}")
    if "scalar_map_min" in spec:
        scalar_problems, scalar_notes = audit_scalar_loop(
            entry, spec, insns, relocs)
        problems.extend(scalar_problems)
        notes.extend(scalar_notes)
    return problems, notes


def audit_noop(objdump, nm, path, outdir, label):
    """The empty ABI targets must be `ret` only (no vector bookkeeping)."""
    text = disassemble(objdump, path, outdir, label)
    items = parse_section_items(text)
    ranges = symbol_ranges(nm, path, outdir, label + "-nm")
    problems = []
    for sym in NOOP_SYMBOLS:
        if sym not in ranges:
            problems.append(f"{sym}: not found in {label}.o")
            continue
        start, end = ranges[sym]
        body = [payload for addr, kind, payload in items
                if kind == "insn" and start <= addr < end]
        real = [insn for insn in body
                if insn.split()[0] not in ("ret", "nop", "c.nop")]
        if real:
            problems.append(f"{sym}: non-empty body {real}")
    return problems


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

# ==========================================================================
# paired exact-input protocol (--protocol paired)
# ==========================================================================
PAIRED_FUNCTIONS = ("atan", "atan2", "acos", "log10")
PAIRED_UNARY_DEFAULT_N = "128,256"
PAIRED_ATAN2_DEFAULT_N = "256,512"
PAIRED_DEFAULT_K = "0,1,2,4,8"
PAIRED_DEFAULT_STREAMS = "1,2,4"


def parse_timing_repeat(text):
    """Validate a --timing-repeat value: the string ``auto`` or an int in [1,64].

    The guest resolves ``auto`` per measurement point from a one-window cost
    sample (R = clamp(ceil(W_target / unit_cycles), 1, 64)); an explicit value is
    used verbatim.  Legacy builds omit the knob, which the guest defaults to 1
    (and the parser marks such logs legacy).  A trace build has no timed window
    to extend, so it only accepts 1.
    """
    spec = str(text).strip()
    if spec.lower() == "auto":
        return "auto"
    try:
        value = int(spec, 10)
    except ValueError:
        raise ValueError(
            f"--timing-repeat must be 'auto' or an integer in [1,64]: {text!r}")
    if not 1 <= value <= 64:
        raise ValueError(
            f"--timing-repeat must be 'auto' or an integer in [1,64]: {text!r}")
    return value
PAIRED_CASE_DIMS = (("m1", "fixed", False), ("m1", "scalable", False),
                    ("m1", "fixed", True), ("m1", "scalable", True),
                    ("m2", "fixed", False), ("m2", "scalable", False),
                    ("m2", "fixed", True), ("m2", "scalable", True))


def pair_callee(fn, lmul, layout, masked):
    """Mangled _ZGV symbol of one paired case, with the LMUL-mandated prefix.

    The paired protocol never lets an m1 case call a _ZGV2 symbol or an m2 case
    call a _ZGV1 symbol; the prefix is derived from the case, not from a list.
    """
    num = "1" if lmul == "m1" else "2"
    if lmul == "m1":
        plain = "N2" if layout == "fixed" else "Nx"
        masked_suffix = "M2" if layout == "fixed" else "Mx"
    else:
        plain = "N4" if layout == "fixed" else "Nx"
        masked_suffix = "M4" if layout == "fixed" else "Mx"
    suffix = masked_suffix if masked else plain
    mid = "vv" if fn == "atan2" else "v"
    return f"_ZGV{num}{suffix}{mid}_{fn}"


def pair_case_name(lmul, layout, masked):
    return f"{lmul}_{layout}_{'masked' if masked else 'unmasked'}"


def pair_list(text, flag, *, minimum=1):
    fields = [field.strip() for field in text.split(",")]
    if not 1 <= len(fields) <= 16 or any(
            not re.fullmatch(r"[0-9]{1,10}", field) for field in fields):
        raise SystemExit(f"{flag}: expected 1 to 16 decimal values")
    values = [int(field) for field in fields]
    if any(not minimum <= value < 2**32 for value in values):
        raise SystemExit(f"{flag}: value out of range [{minimum}, UINT32_MAX]")
    if len(set(values)) != len(values):
        raise SystemExit(f"{flag}: duplicate value")
    return values


def pair_cases(functions, args):
    """Manifest registration in the same order as the target probe tables."""
    parts = args.only_case.replace(",", ";").split(";")
    selected = [name.strip() for name in parts if name.strip()]
    if len(selected) != len(set(selected)):
        raise SystemExit("--only-case: duplicate probe name")
    modes = ["latency", "tput"] if args.modes == "both" else [args.modes]
    cases = []
    for fn in PAIRED_FUNCTIONS:
        if fn not in functions:
            continue
        for lmul, layout, masked in PAIRED_CASE_DIMS:
            lanes = 2 if lmul == "m1" else 4
            if layout == "scalable":
                lanes *= args.vlen // 128
            suffix = "masked" if masked else "unmasked"
            cases.append(dict(case=f"{fn}|{lmul}|{layout}|{suffix}", function=fn,
                              lanes=lanes, probe=pair_case_name(lmul, layout, masked),
                              modes=modes))
    if args.calib:
        for probe in ("calib_reload", "calib_bridge", "calib_inject8",
                      "calib_inject16", "calib_inject32"):
            cases.append(dict(case=probe, function="calib", lanes=2,
                              probe=probe, modes=["latency"]))
    unknown = set(selected) - {case["probe"] for case in cases}
    if unknown:
        raise SystemExit(f"--only-case: unavailable probes: {', '.join(sorted(unknown))}")
    if selected:
        cases = [case for case in cases if case["probe"] in selected]
    return cases, ";".join(selected)


def pair_audit_specs(fn):
    """Audit specs for every probe symbol of one function.

    Both the latency and the throughput probe of a case are audited; the
    throughput probe of a masked case must still load its arguments from the
    frozen corpus and must not contain a bridge.
    """
    specs = {}
    for lmul, layout, masked in PAIRED_CASE_DIMS:
        name = pair_case_name(lmul, layout, masked)
        lmul_num = 1 if lmul == "m1" else 2
        callee = pair_callee(fn, lmul, layout, masked)
        specs[f"probe_{fn}_{name}"] = dict(
            mode="latency", lmul=lmul_num, binary=(fn == "atan2"),
            masked=masked, callee=callee)
        specs[f"probe_{fn}_{name}" + "_tput"] = dict(
            mode="tput", lmul=lmul_num, binary=(fn == "atan2"),
            masked=masked, callee=callee)
    return specs


def pair_defaults(functions):
    """Per-function default N/batch (design table, not a measured optimum)."""
    if len(functions) == 1 and functions[0] == "atan2":
        return PAIRED_ATAN2_DEFAULT_N, "4"
    if len(functions) == 1:
        return PAIRED_UNARY_DEFAULT_N, "8"
    # Several functions share one binary: measure the common N points plus one
    # smaller point; the batch must be the slower (atan2) default so every
    # candidate of a function uses the same statistical policy.
    return "128,256,512", "4"


def validate_paired_header(path, info, *, binary):
    """Check the frozen binary64 arrays needed by the exact-input bridge."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(), flags=re.S)
    count = info["DATA_COUNT"]
    if not 1 <= count < 2**32 or not 0 <= info["ORACLE_LIMIT"] < 2**32:
        raise SystemExit(f"{path}: invalid DATA_COUNT or ORACLE_LIMIT")
    for name in ("input_bits", "reference_bits", "second_bits"):
        matches = re.findall(
            rf"\b{name}\s*\[\s*(DATA_COUNT|[0-9]+)\s*\]\s*=\s*\{{([^}}]*)\}}\s*;",
            text)
        if name == "second_bits" and not binary and not matches:
            continue
        if len(matches) != 1 or name == "second_bits" and not binary:
            raise SystemExit(f"{path}: unexpected or missing {name} initializer")
        size, values = matches[0]
        fields = values.strip().removesuffix(",").split(",")
        if (size not in {"DATA_COUNT", str(count)} or len(fields) != count or
                not all(re.fullmatch(r"\s*0x[0-9a-fA-F]{16}(?:[uU](?:ll|LL))?\s*", f)
                        for f in fields)):
            raise SystemExit(f"{path}: malformed {name}; expected {count} binary64 words")
        for index, field in enumerate(fields):
            exponent = (int(field.strip()[2:18], 16) >> 52) & 0x7ff
            if exponent == 0x7ff or (name != "reference_bits" and exponent == 0):
                raise SystemExit(f"{path}: {name}[{index}] violates finite normal seed / "
                                 "finite reference contract")


def load_header_sha_file(path):
    """{basename: sha256} from a sha256sum-style file."""
    out = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[a-fA-F0-9]{64}", parts[0]):
            raise SystemExit(f"invalid SHA-256 entry in {path}: {line!r}")
        name = Path(parts[1].lstrip("*")).name
        if name in out:
            raise SystemExit(f"duplicate SHA-256 entry for {name} in {path}")
        out[name] = parts[0].lower()
    if not out:
        raise SystemExit(f"no SHA-256 entries in {path}")
    return out


def build_paired(args):
    """Build + audit one paired exact-input measurement binary."""
    if not args.data_dir:
        raise SystemExit("paired builds require --data-dir")
    if args.calib and (args.trace or args.modes == "tput"):
        raise SystemExit("--calib requires timed latency (or both modes)")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", args.candidate_id):
        raise SystemExit("--candidate-id: expected a nonempty protocol token")

    outdir = Path(args.output).expanduser().resolve()
    if outdir.exists():
        raise SystemExit(f"output directory already exists: {outdir}")

    functions = [f.strip() for f in args.functions.split(",") if f.strip()]
    unknown = [f for f in functions if f not in PAIRED_FUNCTIONS]
    if unknown:
        raise SystemExit(f"unknown function(s): {unknown}")
    if len(set(functions)) != len(functions):
        raise SystemExit("--functions: duplicate name")
    if args.calib and "atan" not in functions:
        # The calibration probes reuse the atan frozen corpus to drive the
        # load-only chain; selecting them without atan would need a second copy
        # of the corpus in the binary for no benefit.
        functions = ["atan"] + functions
    if not functions:
        raise SystemExit("--functions is empty")

    dn, db = pair_defaults(functions)
    ns = pair_list(dn if args.lengths == "auto" else args.lengths, "--lengths")
    bs = pair_list(db if args.batches == "auto" else args.batches, "--batches")
    ks = pair_list(args.paired_k, "--paired-k", minimum=0)
    ss = pair_list(args.streams, "--streams")
    if not set(ss) <= {1, 2, 4}:
        raise SystemExit("--streams must contain only 1, 2 or 4")
    if not 1 <= args.rounds <= 65535:
        raise SystemExit("--rounds must be in [1,65535]")
    lengths, batches, klist, streams = (",".join(map(str, values))
                                       for values in (ns, bs, ks, ss))
    cases, only_cases = pair_cases(functions, args)
    if not any(n % case["lanes"] == 0 and (
            "latency" in case["modes"] or
            any((n // case["lanes"]) % s == 0 for s in ss))
            for n in ns for case in cases):
        raise SystemExit("paired matrix has no measurable windows")
    if (len(cases) * len(ns) * len(ks) * args.rounds *
            (1 + len(bs) * len(ss))) > 2_000_000:
        raise SystemExit("paired matrix too large; split into smaller builds")

    try:
        repeat = parse_timing_repeat(args.timing_repeat)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.trace:
        if repeat == "auto":
            repeat = 1
        elif repeat != 1:
            raise SystemExit("--trace requires --timing-repeat 1")
    repeat_spec = str(repeat)
    max_batch = 1 if args.modes == "latency" else max(bs)
    max_repeat = 64 if repeat == "auto" else repeat
    if max_batch * max(ks) * max_repeat * max(1, max(ns) // 2) >= 2**32:
        raise SystemExit("paired window exceeds the unsigned probe call counter")

    data_dir = Path(args.data_dir).expanduser().resolve()
    prefix, cc, objdump, nm, objcopy = find_prefix_tools(args)

    # ---- frozen corpus identity --------------------------------------
    expected = {}
    if args.header_sha_file:
        expected = load_header_sha_file(args.header_sha_file)
    headers = {}
    for fn in functions:
        path = data_dir / f"{fn}.h"
        if not path.exists():
            raise SystemExit(f"missing frozen data header: {path}")
        actual = sha256(path)
        if expected:
            want = expected.get(path.name)
            if want is None:
                raise SystemExit(
                    f"{path.name} is not listed in --header-sha-file")
            if want != actual:
                raise SystemExit(
                    f"{path.name}: sha256 {actual} != expected {want}")
        elif not args.allow_unfrozen_header:
            raise SystemExit(
                "paired builds require --header-sha-file (or an explicit "
                "--allow-unfrozen-header for a non-measurement smoke build)")
        headers[path.name] = {"path": str(path), "sha256": actual,
                              **parse_data_header(path)}

    counts = {v["DATA_COUNT"] for v in headers.values()}
    if len(counts) != 1:
        raise SystemExit(f"mixed DATA_COUNT across headers: {counts}")
    data_count = counts.pop()
    max_len = max(ns)
    if max_len > data_count:
        raise SystemExit(
            f"--lengths max {max_len} exceeds DATA_COUNT {data_count}")

    # 编译校验后的快照，锁定链接输入对应的数据。
    snap = outdir / "source"
    paths = [LATENCY / f"paired_{fn}.c" for fn in functions]
    paths += [LATENCY / name for name in ("paired_main.c", "paired_protocol.h",
                                           "paired_probe_impl.inc", "paired_bridge.h",
                                           "paired_api.h")]
    if args.calib:
        paths.append(LATENCY / "paired_calib.c")
    paths += RUNTIME_SOURCES + [PAIR_TLS_LD, PAIR_TLS_ANCHOR, PAIR_ENTRY_RNE,
                               Path(__file__).resolve(), HERE / "paired_audit.py"]
    outdir.mkdir(parents=True)
    sources = {}
    for path in paths:
        rel = path.relative_to(REPO)
        dest = snap / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        sources[str(rel)] = sha256(dest)
    frozen = snap / "oracles"
    frozen.mkdir()
    for name, info in headers.items():
        dest = frozen / name
        shutil.copy2(info["path"], dest)
        if sha256(dest) != info["sha256"]:
            raise SystemExit(f"data changed while copying {name}")
        validate_paired_header(dest, info, binary=(name == "atan2.h"))
        info["snapshot"] = str(dest)

    lto = bool(args.lto)
    common = [str(snap / "autovec") if f == str(REPO / "autovec") else f
              for f in COMMON if f != "-fno-lto"]
    common.append("-flto" if lto else "-fno-lto")

    libs = {name: manifest_lib(cc, prefix, name)
            for name in ("libmvec.a", "libm.a", "libm-2.44.a", "libc.a",
                         "libgcc.a")}
    installed = Path(libs["libmvec.a"]["path"])
    archive = args.math_archive or installed
    # 整份文件的 SHA 必须覆盖链接成员，不接受 thin archive 或链接脚本。
    for path in (archive, *(Path(libs[name]["path"])
                            for name in ("libm-2.44.a", "libc.a", "libgcc.a"))):
        with path.open("rb") as stream:
            if stream.read(8) != b"!<arch>\n":
                raise SystemExit(f"link input must be a regular static archive: {path}")
    archive_sha = sha256(archive)
    required = {spec["callee"] for fn in functions
                for spec in pair_audit_specs(fn).values()}
    commands = []
    if args.math_archive:
        if archive.samefile(installed):
            raise SystemExit("--math-archive must not designate the installed libmvec.a")
        gcc_nm = cc.with_name(f"{args.triplet}-gcc-nm")
        if not gcc_nm.is_file():
            raise SystemExit(f"missing plugin-aware nm for --math-archive: {gcc_nm}")
        cmd = [str(gcc_nm), "-g", "--defined-only", str(archive)]
        commands.append({"label": "archive-symbols", "argv": cmd})
        exported, _ = must("archive-symbols", cmd, outdir)
        names = set(re.findall(r"^\s*[0-9a-fA-F]+\s+[TW]\s+(_ZGV\S+)$",
                               exported.read_text(), re.MULTILINE))
        if required - names:
            missing = ", ".join(sorted(required - names))
            raise SystemExit(f"--math-archive missing entries: {missing}")

    vlen = args.vlen
    vlenb = vlen // 8
    scal_m1 = vlen // 64
    scal_m2 = vlen // 32
    group_defs = [f"-DPAIRED_GROUP_{fn.upper()}" for fn in functions]
    if args.calib:
        group_defs.append("-DPAIRED_GROUP_CALIB")
    knobs = [
        f'-DPAIRED_N_LIST="{lengths}"',
        f'-DPAIRED_B_LIST="{batches}"',
        f'-DPAIRED_K_LIST="{klist}"',
        f'-DPAIRED_S_LIST="{streams}"',
        f'-DPAIRED_MODES="{args.modes}"',
        f"-DPAIRED_ROUNDS={args.rounds}",
        f"-DPAIRED_SERIAL={args.serial}",
        f'-DPAIRED_TIMING_REPEAT="{repeat_spec}"',
        f"-DPAIRED_VLENB={vlenb}",
        f"-DPAIRED_TRACE={1 if args.trace else 0}",
        f"-DPAIRED_MAX_N={data_count}",
        f"-DPAIRED_SCAL_M1_LANES={scal_m1}",
        f"-DPAIRED_SCAL_M2_LANES={scal_m2}",
        f'-DPAIRED_CANDIDATE="{args.candidate_id}"',
        f'-DPAIRED_LTO="{"on" if lto else "off"}"',
        f'-DPAIRED_ONLY_CASES="{only_cases}"',
    ]

    objects = []

    def compile_tu(label, source, extra):
        obj = outdir / f"{label}.o"
        cmd = [str(cc), *common, *extra, "-c", str(source), "-o", str(obj)]
        commands.append({"label": label, "argv": cmd})
        must(f"cc-{label}", cmd, outdir)
        objects.append(obj)
        return obj

    runtime = outdir / "runtime.o"
    cmd = [str(cc), *common, "-fno-tree-vectorize", "-c",
           str(snap / RUNTIME_SRC.relative_to(REPO)), "-o", str(runtime)]
    commands.append({"label": "runtime", "argv": cmd})
    must("cc-runtime", cmd, outdir)

    tdir = snap / LATENCY.relative_to(REPO)
    func_objs = {}
    for fn in functions:
        func_objs[fn] = compile_tu("paired_" + fn, tdir / f"paired_{fn}.c",
                                   ["-I", str(frozen), *knobs])
    if args.calib:
        compile_tu("paired_calib", tdir / "paired_calib.c", knobs)
    main_obj = compile_tu("paired_main", tdir / "paired_main.c",
                          knobs + group_defs)
    for source in (PAIR_TLS_ANCHOR, PAIR_ENTRY_RNE):
        compile_tu(source.stem, snap / source.relative_to(REPO), ["-fno-lto"])

    # 显式链接标量库和唯一向量归档；不让 -lm 脚本引入第二份向量库。
    elf = outdir / f"paired-{args.tag}.elf"
    bin_ = outdir / f"paired-{args.tag}.bin"
    link = [str(cc), *common, "-nostdlib", "-static", "-no-pie",
            *[str(o) for o in objects], str(runtime),
            "-Wl,--wrap=_trm_init", "-Wl,--wrap=main", "-Wl,--build-id=none",
            "-T", str(snap / PAIR_TLS_LD.relative_to(REPO)),
            "-T", str(snap / LINK_LD.relative_to(REPO)),
            "-Wl,-Map=" + str(outdir / "link.map"), "-Wl,-t",
            str(AM_A), "-Wl,--start-group", str(KLIB_A),
            libs["libm-2.44.a"]["path"], str(archive), libs["libc.a"]["path"],
            libs["libgcc.a"]["path"], "-Wl,--end-group", "-o", str(elf)]
    commands.append({"label": "link", "argv": link})
    link_stdout, _ = must("link", link, outdir)
    (outdir / "link.trace.txt").write_text(link_stdout.read_text(errors="replace"))
    link_map = (outdir / "link.map").read_text(errors="replace")
    archive_members = sorted(set(re.findall(
        re.escape(str(archive)) + r"\(([^()\n]+)\)", link_map)))
    if not archive_members:
        raise SystemExit(f"link did not pull any members from {archive}")
    other_libmvec = re.search(r"(?:^|[\s/])libmvec\.a\(",
                              link_map.replace(f"{archive}(", ""))
    if other_libmvec:
        raise SystemExit("link pulled members from a second libmvec.a")
    if sha256(archive) != archive_sha:
        raise SystemExit(f"archive changed during build: {archive}")
    archive_info = {"path": str(archive), "sha256": archive_sha,
                    "members": archive_members}

    cmd = [str(objcopy), "-S", "--set-section-flags", ".bss=alloc,contents",
           "-O", "binary", str(elf), str(bin_)]
    commands.append({"label": "objcopy", "argv": cmd})
    must("objcopy", cmd, outdir)

    # ---- compiled-code audit -----------------------------------------
    from paired_audit import (parse_section_items, parse_linked_insns,
                              symbol_ranges, audit_probe_object,
                              audit_probe_linked, find_all_loops, mnemonic)
    audit_lines = []
    elf_insns, _ = parse_linked_insns(disassemble(objdump, elf, outdir, "elf"))
    nm_stdout, _ = must("nm-elf", [str(nm), "-S", "--defined-only", str(elf)], outdir)
    nm_elf = nm_stdout.read_text()
    (outdir / "elf-symbols.txt").write_text(nm_elf)
    elf_ranges = symbol_ranges(nm_elf)

    linked_only = lto
    problems_all = []
    if args.calib:
        # 最终 ELF 必须在计时窗口的回边内读取向量，而不只加载最后一项。
        name = "probe_calib_reload"
        start, end = elf_ranges.get(name, (0, 0))
        body = [(addr, insn) for addr, insn in elf_insns if start <= addr < end]
        reads = [addr for addr, insn in body if mnemonic(insn) == "rdcycle"]
        loads = [addr for addr, insn in body if mnemonic(insn) == "vle64.v"]
        ok = len(reads) == 2 and any(
            reads[0] < lo <= addr < hi < reads[1]
            for lo, hi in find_all_loops(body) for addr in loads)
        if not ok:
            problems_all.append(f"{name}: no vector load in the timed back edge")
        audit_lines.append(f"{'ok' if ok else 'FAIL'} {name} [linked-reload-loop]")
    for fn in functions:
        obj = func_objs[fn]
        items = []
        ranges = {}
        if not linked_only:
            obj_text = disassemble(objdump, obj, outdir, "paired_" + fn)
            items = parse_section_items(obj_text)
            nm_obj = subprocess.run(
                [str(nm), "-S", "--defined-only", str(obj)],
                capture_output=True, text=True).stdout
            ranges = symbol_ranges(nm_obj)
        for sym, spec in pair_audit_specs(fn).items():
            if linked_only:
                # LTO objects are bytecode: the linked image is the only
                # disassemblable artifact.  Probe symbols still exist there
                # because the probe table keeps their address.
                if sym not in elf_ranges:
                    audit_lines.append(
                        f"FAIL {sym}: symbol missing from the linked image")
                    problems_all.append(f"{sym}: missing from linked image")
                    continue
                start, end = elf_ranges[sym]
                problems, notes = audit_probe_linked(sym, spec, elf_insns,
                                                     start, end)
                policy = "linked-inline"
            elif sym not in ranges:
                audit_lines.append(f"FAIL {sym}: symbol missing from "
                                   f"paired_{fn}.o")
                problems_all.append(f"{sym}: missing from object")
                continue
            else:
                start, end = ranges[sym]
                problems, notes = audit_probe_object(
                    sym, spec, items, start, end)
                policy = "reloc"
            audit_lines.append(
                f"{'FAIL' if problems else 'ok'} {sym} [{policy}]")
            for n in notes:
                audit_lines.append(f"    . {n}")
            for pr in problems:
                audit_lines.append(f"    ! {pr}")
            problems_all.extend(f"{sym}: {pr}" for pr in problems)
            if not problems and spec["callee"] not in nm_elf:
                problems_all.append(
                    f"{sym}: callee {spec['callee']} missing from the linked "
                    f"image")
                audit_lines.append(f"    ! callee {spec['callee']} missing")
    (outdir / "audit-dependency.txt").write_text(
        "\n".join(audit_lines) + "\n")
    audit_failed = problems_all

    # ---- identity manifest -------------------------------------------
    artifacts = {}
    for path in (elf, bin_, main_obj):
        artifacts[path.name] = {"path": str(path), "sha256": sha256(path),
                                "bytes": path.stat().st_size}
    manifest = {
        "schema_version": 2,
        "suite": "libmvec-paired",
        "protocol": 2,
        "wire_format": "pair-trace" if args.trace else "pair-compact-r",
        "build_status": "built",
        "tag": args.tag,
        "created_at": subprocess.run(
            ["date", "-Is"], capture_output=True, text=True).stdout.strip(),
        "host": os.uname().nodename,
        "output": str(outdir),
        "candidate": {"id": args.candidate_id,
                      "cross_lmul": args.cross_lmul,
                      "lto": "on" if lto else "off"},
        "functions": functions,
        "cases": cases,
        "knobs": {"only_cases": only_cases,
                  "lengths": lengths, "batches": batches,
                  "paired_k": klist, "streams": streams,
                  "modes": args.modes, "rounds": args.rounds,
                  "serial": args.serial, "trace": bool(args.trace),
                  "timing_repeat": repeat,
                  "vlen": vlen, "vlenb": vlenb,
                  "scalable_lanes": {"m1": scal_m1, "m2": scal_m2},
                  "fixed_lanes": {"m1": 2, "m2": 4},
                  "calib": bool(args.calib)},
        "toolchain": {
            "prefix": str(prefix),
            "cc": {"path": str(cc), "version": version_of(cc),
                   "sha256": sha256(cc)},
            "objdump": {"path": str(objdump), "sha256": sha256(objdump)},
            "nm": {"path": str(nm), "sha256": sha256(nm)},
            "objcopy": {"path": str(objcopy), "sha256": sha256(objcopy)},
            "libs": libs,
            "support_archives": {str(AM_A): sha256(AM_A),
                                 str(KLIB_A): sha256(KLIB_A)},
        },
        "math_archive": archive_info if args.math_archive else None,
        "vector_archive": archive_info,
        "data": {"dir": str(data_dir), "headers": headers,
                 "frozen": bool(expected)},
        "sources": sources,
        "commands": commands,
        "artifacts": artifacts,
        "audit": {"status": "fail" if audit_failed else "ok",
                  "mode": "linked-inline" if (lto or linked_only) else "reloc",
                  "findings": audit_failed,
                  "report": str(outdir / "audit-dependency.txt")},
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    provenance = [
        f"tag={args.tag}",
        f"protocol=2",
        f"candidate={args.candidate_id}",
        f"cross_lmul={args.cross_lmul}",
        f"lto={'on' if lto else 'off'}",
        f"functions={','.join(functions)}",
        f"lengths={lengths}",
        f"batches={batches}",
        f"paired_k={klist}",
        f"streams={streams}",
        f"modes={args.modes}",
        f"only_cases={only_cases}",
        f"rounds={args.rounds}",
        f"timing_repeat={repeat_spec}",
        f"serial={args.serial}",
        f"trace={1 if args.trace else 0}",
        f"vlen={vlen}",
        f"prefix={prefix}",
        f"cc={cc}",
        f"gcc={version_of(cc)}",
        f"libmvec={libs['libmvec.a']['path']}",
        f"libmvec_sha256={libs['libmvec.a']['sha256']}",
    ]
    provenance += [f"vector_archive={archive}", f"vector_archive_sha256={archive_sha}",
                   f"vector_archive_members={','.join(archive_members)}"]
    for name, info in headers.items():
        provenance.append(f"header_{name}_sha256={info['sha256']}")
    provenance += [
        f"elf={elf}",
        f"bin={bin_}",
        f"bin_sha256={sha256(bin_)}",
        f"audit={'fail' if audit_failed else 'ok'}",
    ]
    (outdir / "provenance.txt").write_text("\n".join(provenance) + "\n")
    (outdir / "sha256.txt").write_text(
        "".join(f"{sha256(p)}  {p}\n" for p in (elf, bin_) if p.exists()))

    print(f"{'BUILD_UNAUDITED' if audit_failed else 'BUILD_OK'} tag={args.tag} elf={elf}")
    print(f"  vector archive {archive} sha256={archive_sha}")
    print(f"  bin sha256={sha256(bin_)}")
    for line in audit_lines:
        print("  " + line)
    if audit_failed and not args.skip_audit:
        sys.stderr.write("AUDIT_FAILED\n")
        for finding in audit_failed:
            sys.stderr.write(f"  ! {finding}\n")
        return 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build one libmvec function-latency baremetal binary.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--prefix", required=True,
                    help="toolchain install prefix (bin/, sysroot/ with libmvec.a)")
    ap.add_argument("--output", required=True,
                    help="new output directory (must not exist)")
    ap.add_argument("--tag", default="latency", help="artifact name suffix")
    ap.add_argument("--groups", default="calib,atan,acos_nx,acos_masked",
                    help="comma list of " + ",".join(GROUPS))
    ap.add_argument("--data-dir",
                    help="directory with frozen inputs (required for paired and frozen-header legacy groups)")
    ap.add_argument("--k-list", default="0,4,8,16,32",
                    help="K values the binary walks (comma separated)")
    ap.add_argument("--rounds", type=int, default=10,
                    help="windows per (entry, K): 1 first + 2 warmup + 7 steady")
    ap.add_argument("--prelude", type=int, default=16,
                    help="untimed chain steps to reach the closed-loop operating point")
    ap.add_argument("--entries", default="",
                    help="optional ';' or ',' separated allow list of entry names")
    ap.add_argument("--serial", type=int, default=0, choices=[0, 1],
                    help="bracket each window with sfence.vma (1) or not (0)")
    ap.add_argument("--triplet", default="riscv64-unknown-linux-gnu")
    ap.add_argument("--cc")
    ap.add_argument("--objdump")
    ap.add_argument("--nm")
    ap.add_argument("--objcopy")
    ap.add_argument("--math-archive", type=Path, default=None,
                    help="paired only: replacement static vector archive; "
                         "all selected _ZGV entries must come from it. "
                         "Scalar libm, libc and libgcc still come from the prefix")
    ap.add_argument("--skip-audit", action="store_true",
                    help="do not fail the build on a dependency-chain audit finding")
    # ---- paired protocol (protocol=2) knobs ----
    ap.add_argument("--protocol", choices=["legacy", "paired"],
                    default="legacy",
                    help="legacy: affine-map latency groups; paired: exact-input "
                         "paired latency+throughput probes")
    ap.add_argument("--functions", default="atan,atan2,acos,log10",
                    help="paired: comma list of functions to instantiate")
    ap.add_argument("--lengths", default="auto",
                    help="paired: scalar N list; 'auto' uses the design default "
                         "of the selected function(s)")
    ap.add_argument("--batches", default="auto",
                    help="paired: throughput batch B list; 'auto' uses the "
                         "design default of the selected function(s)")
    ap.add_argument("--paired-k", default=PAIRED_DEFAULT_K,
                    help="paired: batch-multiplier K list")
    ap.add_argument("--streams", default=PAIRED_DEFAULT_STREAMS,
                    help="paired: throughput stream counts")
    ap.add_argument("--modes", default="both",
                    choices=["both", "latency", "tput"],
                    help="paired: measure latency, throughput, or both in one BIN")
    ap.add_argument("--both", action="store_true",
                    help="paired: shorthand for --modes both (one BIN measures "
                         "the paired latency chain and independent throughput)")
    ap.add_argument("--vlen", type=int, default=128, choices=[128, 256],
                    help="paired: expected VLEN; fixed lanes stay 2/4, scalable "
                         "lanes become VLEN/64 and VLEN/32")
    ap.add_argument("--lto", action="store_true",
                    help="paired: compile and link with -flto")
    ap.add_argument("--calib", action="store_true",
                    help="paired: add the bridge calibration control group")
    ap.add_argument("--trace", action="store_true",
                    help="paired: non-timed trace build that records every "
                         "argument and asserts bit-exact seed recovery")
    ap.add_argument("--candidate-id", default="installed-baseline-8a",
                    help="paired: candidate identity recorded in the manifest")
    ap.add_argument("--cross-lmul",
                    default="none",
                    help="paired: set when an m1 wrapper runs an m2 kernel, e.g. "
                         "'ZGV1 wrapper over ZGV2 core' (excluded from m1 ranking)")
    ap.add_argument("--header-sha-file",
                    help="paired: sha256sum-style file pinning every frozen "
                         "data header; a mismatch fails the build")
    ap.add_argument("--only-case", default="",
                    help="paired: ';' separated probe-name allow list, e.g. "
                         "'m1_fixed_unmasked;m2_fixed_unmasked' (case slice)")
    ap.add_argument("--timing-repeat", default="auto",
                    help="paired: 'auto' (default; estimate R per measurement "
                         "point so W >= 40,000 cycles, acceptance floor "
                         "20,000) or an explicit integer R in [1,64]. One "
                         "rdcycle interval then covers R x K x B x ncall "
                         "calls. Trace builds require 1.")
    ap.add_argument("--allow-unfrozen-header", action="store_true",
                    help="paired: allow a build without --header-sha-file "
                         "(smoke builds only; manifest records frozen=false)")
    args = ap.parse_args(argv)
    if args.both:
        args.modes = "both"

    if args.math_archive is not None:
        if args.protocol != "paired":
            ap.error("--math-archive requires --protocol paired")
        args.math_archive = args.math_archive.expanduser().resolve()
        if not args.math_archive.is_file():
            raise SystemExit(f"missing --math-archive: {args.math_archive}")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", args.tag):
        ap.error("--tag: expected a nonempty filename token")
    if args.protocol == "paired":
        return build_paired(args)
    outdir = Path(args.output).expanduser().resolve()
    if outdir.exists() and any(outdir.iterdir()):
        raise SystemExit(f"output directory already in use: {outdir}")
    outdir.mkdir(parents=True, exist_ok=True)

    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise SystemExit(f"unknown group(s): {unknown}; known: {list(GROUPS)}")

    if any(GROUPS[g]["header"] for g in groups) and not args.data_dir:
        raise SystemExit("these groups require --data-dir")
    data_dir = Path(args.data_dir).expanduser().resolve() if args.data_dir else None
    prefix, cc, objdump, nm, objcopy = find_prefix_tools(args)

    headers = {}
    for g in groups:
        hdr = GROUPS[g]["header"]
        if hdr and hdr not in headers:
            path = data_dir / hdr
            if not path.exists():
                raise SystemExit(f"missing frozen data header: {path}")
            headers[hdr] = parse_data_header(path)
    oracle_limits = {v["ORACLE_LIMIT"] for v in headers.values()}
    if len(oracle_limits) > 1:
        raise SystemExit(f"mixed ORACLE_LIMIT across headers: {oracle_limits}")

    libs = {name: manifest_lib(cc, prefix, name)
            for name in ("libmvec.a", "libm.a", "libm-2.44.a", "libc.a",
                         "libgcc.a")}

    # ---- compile -------------------------------------------------------
    defs_groups = [f"-DLAT_GROUP_{g.upper()}" for g in groups]
    defs_knobs = [f'-DLAT_K_LIST="{args.k_list}"',
                  f"-DLAT_ROUNDS={args.rounds}",
                  f"-DLAT_PRELUDE={args.prelude}",
                  f"-DLAT_SERIAL={args.serial}",
                  '-DLAT_ONLY_ENTRIES="%s"'
                  % args.entries.replace(",", ";").strip()]
    objects = []
    commands = []

    def compile_tu(label, source, extra, group_defs=()):
        obj = outdir / f"{label}.o"
        cmd = [str(cc), *COMMON, *group_defs, *extra, "-c", str(source),
               "-o", str(obj)]
        commands.append({"label": label, "argv": cmd})
        must(f"cc-{label}", cmd, outdir)
        objects.append(obj)
        return obj

    # runtime reuses the published veclibm baremetal runtime (AM/TLS/errno)
    runtime = outdir / "runtime.o"
    cmd = [str(cc), *COMMON, "-I", str(REPO / "single" / "libmvec"),
           "-fno-tree-vectorize", "-c", str(RUNTIME_SRC), "-o", str(runtime)]
    commands.append({"label": "runtime", "argv": cmd})
    must("cc-runtime", cmd, outdir)

    compile_tu("noop", LATENCY / "latency_noop.c", [])
    for g in groups:
        extra = [f"-DLAT_SERIAL={args.serial}"]
        hdr = GROUPS[g]["header"]
        if hdr:
            extra += [f'-DLAT_DATA_HEADER="{hdr}"', "-I", str(data_dir)]
        elif GROUPS[g].get("scalar"):
            extra += ["-DLAT_SCALAR_ORACLE=1",
                      f"-DLAT_SCALAR_FN={GROUPS[g]['scalar']}"]
            if GROUPS[g].get("binary"):
                extra += [f"-DLAT_SCALAR_FN2={GROUPS[g]['scalar']}"]
        compile_tu(g, LATENCY / "latency_driver.c", extra,
                   [f"-DLAT_GROUP_{g.upper()}"])
    main_obj = compile_tu("main", LATENCY / "latency_main.c", defs_knobs,
                          defs_groups)

    # ---- link ----------------------------------------------------------
    elf = outdir / f"latency-{args.tag}.elf"
    bin_ = outdir / f"latency-{args.tag}.bin"
    link = [str(cc), *COMMON, "-nostdlib", "-static", "-no-pie",
            *[str(o) for o in objects], str(runtime)]
    link += ["-Wl,--wrap=_trm_init", "-Wl,--build-id=none",
            "-T", str(TLS_LD), "-T", str(LINK_LD),
            "-Wl,-Map=" + str(outdir / "link.map"), "-Wl,-t",
            str(AM_A), "-Wl,--start-group", str(KLIB_A), "-lm", "-lc", "-lgcc",
            "-Wl,--end-group", "-o", str(elf)]
    commands.append({"label": "link", "argv": link})
    trace_stdout, _ = must("link", link, outdir)
    link_trace = trace_stdout.read_text(errors="replace")
    (outdir / "link.trace.txt").write_text(link_trace)

    cmd = [str(objcopy), "-S", "--set-section-flags", ".bss=alloc,contents",
           "-O", "binary", str(elf), str(bin_)]
    commands.append({"label": "objcopy", "argv": cmd})
    must("objcopy", cmd, outdir)

    # ---- audit ---------------------------------------------------------
    audit_lines = []
    audit_failed = []
    # keep the linked image disassembly as evidence (relocations are resolved
    # there, so the chain audit below runs on the objects where they are not)
    disassemble(objdump, elf, outdir, "elf")
    for g in groups:
        obj = outdir / f"{g}.o"
        items = parse_section_items(disassemble(objdump, obj, outdir, g))
        ranges = symbol_ranges(nm, obj, outdir, f"{g}-nm")
        for entry in GROUPS[g]["entries"]:
            func = PROBE_FUNCS[entry]
            if func not in ranges:
                audit_failed.append(f"{entry}: probe {func} not in {obj.name}")
                audit_lines.append(f"FAIL {entry} (missing {func})")
                continue
            start, end = ranges[func]
            problems, notes = audit_probe(entry, items, start, end, outdir)
            status = "FAIL" if problems else "ok"
            audit_lines.append(f"{status} {entry} [{g}.o]")
            for note in notes:
                audit_lines.append(f"    . {note}")
            for problem in problems:
                audit_lines.append(f"    ! {problem}")
            audit_failed.extend(f"{entry}: {p}" for p in problems)

    noop_obj = outdir / "noop.o"
    noop_problems = audit_noop(objdump, nm, noop_obj, outdir, "noop")
    audit_lines.append(("FAIL" if noop_problems else "ok") + " empty-abi")
    for problem in noop_problems:
        audit_lines.append(f"    ! {problem}")
    audit_failed.extend(noop_problems)

    # the entry symbols must be pulled from the intended libmvec archive
    nm_all = subprocess.run([str(nm), "--defined-only", str(elf)],
                            capture_output=True, text=True).stdout
    (outdir / "elf-symbols.txt").write_text(nm_all)
    for entry in [e for g in groups for e in GROUPS[g]["entries"]]:
        sym = AUDIT.get(entry, {}).get("callee")
        if not sym or not sym.startswith("_ZGV"):
            continue
        if sym in nm_all:
            audit_lines.append(f"ok symbol {sym}")
        else:
            audit_failed.append(f"{entry}: {sym} missing from the linked image")
    link_map = (outdir / "link.map").read_text(errors="replace")
    members = sorted({m.split("(")[-1].rstrip(")")
                      for m in link_map.split()
                      if "libmvec.a(" in m})
    audit_lines.append("libmvec members pulled: " + (", ".join(members) or "none"))
    needs_libmvec = any(GROUPS[g]["header"] or GROUPS[g].get("scalar") for g in groups)
    if needs_libmvec and not members:
        audit_failed.append("no libmvec.a member pulled; -lm did not reach libmvec")
    if not needs_libmvec and members:
        audit_failed.append("calibration-only build pulled libmvec members")
    (outdir / "audit-dependency.txt").write_text("\n".join(audit_lines) + "\n")

    # ---- identity ------------------------------------------------------
    artifacts = {}
    for path in (elf, bin_, outdir / "runtime.o", main_obj, noop_obj):
        artifacts[path.name] = {"path": str(path), "sha256": sha256(path),
                                "bytes": path.stat().st_size}
    sources = {str(p.relative_to(REPO)) if str(p).startswith(str(REPO))
               else str(p): sha256(p) for p in SOURCES if p.exists()}
    headers_out = {}
    for hdr, info in headers.items():
        path = data_dir / hdr
        headers_out[hdr] = {"path": str(path), "sha256": sha256(path),
                            **info}

    # freeze the exact sources used, so a hash alone is not the only record
    snap = outdir / "source"
    snap.mkdir(exist_ok=True)
    for p in SOURCES:
        if p.exists():
            shutil.copy2(p, snap / p.name)

    manifest = {
        "schema_version": 1,
        "suite": "libmvec-latency",
        "build_status": "built",
        "tag": args.tag,
        "created_at": subprocess.run(
            ["date", "-Is"], capture_output=True, text=True).stdout.strip(),
        "host": os.uname().nodename,
        "output": str(outdir),
        "knobs": {"groups": groups, "entries": args.entries,
                  "k_list": args.k_list,
                  "rounds": args.rounds, "prelude": args.prelude,
                  "serial": args.serial,
                  "vlen_expected": 128,
                  "vlenb_expected": 16},
        "toolchain": {
            "prefix": str(prefix),
            "cc": {"path": str(cc), "version": version_of(cc),
                   "sha256": sha256(cc)},
            "objdump": {"path": str(objdump), "sha256": sha256(objdump)},
            "nm": {"path": str(nm), "sha256": sha256(nm)},
            "objcopy": {"path": str(objcopy), "sha256": sha256(objcopy)},
            "libs": libs,
            "link_scripts": {"tls.ld": str(TLS_LD), "link.ld": str(LINK_LD)},
            "support_archives": {str(AM_A): sha256(AM_A),
                                 str(KLIB_A): sha256(KLIB_A)},
        },
        "math_archive": None,
        "data": {"dir": str(data_dir) if data_dir else None, "headers": headers_out},
        "sources": sources,
        "commands": commands,
        "artifacts": artifacts,
        "audit": {"status": "fail" if audit_failed else "ok",
                  "findings": audit_failed,
                  "report": str(outdir / "audit-dependency.txt")},
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    provenance = [
        f"tag={args.tag}",
        f"prefix={prefix}",
        f"cc={cc}",
        f"gcc={version_of(cc)}",
        f"libmvec={libs['libmvec.a']['path']}",
        f"libmvec_sha256={libs['libmvec.a']['sha256']}",
        "math_archive=installed",
        f"data_dir={data_dir}",
        f"groups={','.join(groups)}",
        f"k_list={args.k_list}",
        f"rounds={args.rounds}",
        f"prelude={args.prelude}",
        f"serial={args.serial}",
        f"entries={args.entries or 'all'}",
        f"oracle_limit={','.join(str(v) for v in sorted(oracle_limits)) or 'none'}",
        f"elf={elf}",
        f"bin={bin_}",
        f"bin_sha256={sha256(bin_)}",
        f"audit={'fail' if audit_failed else 'ok'}",
    ]
    (outdir / "provenance.txt").write_text("\n".join(provenance) + "\n")
    (outdir / "sha256.txt").write_text(
        "".join(f"{sha256(p)}  {p}\n" for p in (elf, bin_) if p.exists()))

    print(f"BUILD_OK tag={args.tag} elf={elf}")
    print(f"  libmvec.a sha256={libs['libmvec.a']['sha256']}")
    print(f"  bin sha256={sha256(bin_)} members={','.join(members) or 'none'}")
    for line in audit_lines:
        print("  " + line)
    if audit_failed and not args.skip_audit:
        sys.stderr.write("AUDIT_FAILED\n")
        for finding in audit_failed:
            sys.stderr.write(f"  ! {finding}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
