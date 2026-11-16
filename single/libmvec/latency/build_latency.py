#!/usr/bin/env python3
"""Build one baremetal libmvec function-latency binary.

The binary contains a configurable subset of the latency probes declared in
``latency_driver.c`` and links against the real static libmvec of a toolchain
prefix (``-lm`` resolves to a ld script that also pulls ``libmvec.a``).

What the script guarantees besides producing ``latency-<tag>.elf/.bin``:

* every input is named explicitly (prefix, frozen data headers, K list, rounds);
* identity is recorded, not inferred: sha256 of the toolchain binaries, the
  libmvec/libm/libc/libgcc archives, the runtime sources, data headers and the
  compiler command lines go into ``manifest.json``;
* the dependency chain is audited on the object code: each probe must contain a
  real call site to its entry plus the two closing map instructions inside the
  same K loop, and the empty ABI functions must stay empty.  A chain the
  compiler deleted or serialised away is a build failure, not a quiet zero
  slope at run time.

Typical use::

    python3 -B single/libmvec/latency/build_latency.py \\
        --prefix /path/to/prefix --output /new/dir --tag calib \\
        --groups calib,acos_masked --k-list 0,4,8,16,32

Everything is written under ``--output``; the script never writes into the
toolchain prefix or into a build directory that is used as a run directory.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]  # .dsh/rvv-bench
SHARE = REPO.parent  # .dsh

RUNTIME_SRC = REPO / "single" / "veclibm-runtime.c"
TLS_LD = REPO / "autovec" / "tls.ld"
LINK_LD = REPO / "thirdparty" / "link.ld"
AM_A = REPO / "thirdparty" / "am-riscv64-xs.a"
KLIB_A = REPO / "thirdparty" / "klib-riscv64-xs.a"

SOURCES = [
    HERE / "latency_driver.c",
    HERE / "latency_main.c",
    HERE / "latency_noop.c",
    HERE / "latency_map.h",
    HERE / "latency_probe.h",
    RUNTIME_SRC,
    REPO / "autovec" / "av_runtime.c",
    REPO / "autovec" / "av_runtime.h",
    TLS_LD,
    LINK_LD,
]

DEFAULT_DATA_DIR = (
    SHARE / "experiments" / "2026-11-15-2244-libmvec-expand" / "inputs" / "perf"
)

# group -> (driver TU tag, frozen header, entries)
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

VFP_MNEMONICS = ("vfmul", "vfadd", "vfsub", "vfmacc", "vfmadd", "vfnmacc",
                 "vfnmadd", "vfmsac", "vfmsub", "vfrsub", "vfdiv", "vfsqrt")

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
    out = subprocess.run([str(cc), f"-print-file-name={name}"],
                         capture_output=True, text=True)
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


def audit_probe(entry, items, start, end, outdir):
    """Verify one probe body keeps the dependency chain the harness claims."""
    import re
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
    back_edges = []
    for addr, insn in insns:
        if not insn:
            continue
        mnem = insn.split()[0]
        if mnem.startswith(("j", "b", "c.j", "c.b")):
            m = re.search(r"\b([0-9a-f]+) <", insn)
            if m and int(m.group(1), 16) < addr:
                back_edges.append(addr)
    if spec.get("loop", True):
        if not back_edges:
            problems.append("no backward branch (K loop missing)")
        else:
            last = max(back_edges)
            if call_sites and max(call_sites) > last:
                problems.append("call site after the loop back-edge")
            notes.append(f"K loop back-edge at {hex(last)}")
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
    ap.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR),
                    help="directory with the frozen atan.h/acos.h reference bits")
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
    ap.add_argument("--skip-audit", action="store_true",
                    help="do not fail the build on a dependency-chain audit finding")
    args = ap.parse_args(argv)

    outdir = Path(args.output).expanduser().resolve()
    if outdir.exists() and any(outdir.iterdir()):
        raise SystemExit(f"output directory already in use: {outdir}")
    outdir.mkdir(parents=True, exist_ok=True)

    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise SystemExit(f"unknown group(s): {unknown}; known: {list(GROUPS)}")

    data_dir = Path(args.data_dir).expanduser().resolve()
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

    compile_tu("noop", HERE / "latency_noop.c", [])
    for g in groups:
        extra = [f"-DLAT_SERIAL={args.serial}"]
        hdr = GROUPS[g]["header"]
        if hdr:
            extra += [f'-DLAT_DATA_HEADER="{hdr}"', "-I", str(data_dir)]
        compile_tu(g, HERE / "latency_driver.c", extra,
                   [f"-DLAT_GROUP_{g.upper()}"])
    main_obj = compile_tu("main", HERE / "latency_main.c", defs_knobs,
                          defs_groups)

    # ---- link ----------------------------------------------------------
    elf = outdir / f"latency-{args.tag}.elf"
    bin_ = outdir / f"latency-{args.tag}.bin"
    link = [str(cc), *COMMON, "-nostdlib", "-static", "-no-pie",
            *[str(o) for o in objects], str(runtime),
            "-Wl,--wrap=_trm_init", "-Wl,--build-id=none",
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
    for sym in ["_ZGV1Nxv_atan", "_ZGV2Nxv_atan", "_ZGV1Nxv_acos",
                "_ZGV2Nxv_acos", "_ZGV1M2v_acos", "_ZGV2M4v_acos"]:
        if sym in nm_all:
            audit_lines.append(f"ok symbol {sym}")
    link_map = (outdir / "link.map").read_text(errors="replace")
    members = sorted({m.split("(")[-1].rstrip(")")
                      for m in link_map.split()
                      if "libmvec.a(" in m})
    audit_lines.append("libmvec members pulled: " + (", ".join(members) or "none"))
    needs_libmvec = any(GROUPS[g]["header"] for g in groups)
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
        "data": {"dir": str(data_dir), "headers": headers_out},
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
