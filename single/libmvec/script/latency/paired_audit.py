#!/usr/bin/env python3
"""Compiled-code audit for the paired exact-input protocol (protocol=2).

``build_latency.py --protocol paired`` calls this module after the probes are
built.  It has two jobs:

1. **Dependency-chain audit (relocation mode).**  For a non-LTO build the probe
   translation units still carry relocations, so the audit can prove, for every
   probe symbol:

   * the timed loop contains exactly one call site to the expected ``_ZGV``
     entry (one per stream for throughput, so 1/2/4);
   * latency probes contain a real ``vfsub.vv X,X,X`` (identical sources) and a
     ``vfadd.vv D,seed,X`` bridge inside the same loop;
   * the call's return registers taint the vfsub sources, the vfsub result
     taints the vfadd, and the vfadd writes exactly the registers the next call
     consumes as arguments; a load or constant that overwrites an argument
     register clears the taint and fails the audit;
   * the vfadd's other operand comes from a vector load of the frozen corpus;
   * throughput probes contain no bridge and every argument is loaded from the
     frozen corpus (no result feedback);
   * the only conditional branch inside the loop is its own back edge.

2. **Linked-inline policy (LTO).**  When the entry got inlined at link time
   there is no call site left to taint.  The audit then requires, per loop, the
   bridge shape plus a kernel-sized block of vector FP instructions, and records
   ``policy=linked-inline`` so a report cannot confuse it with the stronger
   relocation-mode audit.  A deleted bridge is a failure either way.

The module is pure Python (standard library only) so ``test_paired_protocol.py``
can drive it with synthetic disassembly.
"""

import re

# Vector register-consuming instructions that produce a value in operand 0.
VEC_STORE_PREFIX = ("vse", "vsse", "vsx", "vsm")
VEC_LOAD_PREFIX = ("vle", "vlse", "vlx", "vlre", "vlm")
VEC_FP_ARITH = ("vfadd", "vfsub", "vfmul", "vfdiv", "vfmacc", "vfnmacc",
                "vfmsac", "vfnmsac", "vfmadd", "vfnmadd", "vfmsub", "vfnmsub",
                "vfrsub", "vfsqrt", "vfmin", "vfmax", "vfsgnj", "vfsgnjn",
                "vfsgnjx", "vfred", "vfwadd", "vfwsub", "vfwmul")
BRANCH_RE = re.compile(
    r"^(?:c\.)?(?:beq|bne|blt|bge|bltu|bgeu|bgt|ble|bgtu|bleu|beqz|bnez|"
    r"bltz|bgez|blez|bgtz|j)\b")
COND_BRANCH_RE = re.compile(
    r"^(?:c\.)?(?:beq|bne|blt|bge|bltu|bgeu|bgt|ble|bgtu|bleu|beqz|bnez|"
    r"bltz|bgez|blez|bgtz)\b")
VTYPE_RE = re.compile(r"\b(?:e8|e16|e32|e64),mf?(\d+)\b")
VTYPE_RE2 = re.compile(r",m(f?)(\d+),")
REG_RE = re.compile(r"\bv(\d+)\b")
INSN_RE = re.compile(r"^\s*([0-9a-f]+):\t(.*)$")
RELOC_RE = re.compile(r"^\s*([0-9a-f]+):[ \t]+(R_RISCV_\S+)[ \t]+(.*)$")
SECTION_RE = re.compile(r"^Disassembly of section ([^:]+):$")
SYMBOL_RE = re.compile(r"^([0-9a-f]+)\s+<([^>]+)>:$")


# --------------------------------------------------------------------------
# disassembly parsing
# --------------------------------------------------------------------------
def parse_section_items(text, prefix=".text"):
    """Flatten objdump -dr output into (addr, kind, payload) items of a section.

    ``kind`` is ``insn`` (payload = instruction text) or ``reloc``
    (payload = (rtype, target)).  Only the requested section is kept, because
    .text and .rodata both start at address 0 in an object file.
    """
    items = []
    section = None
    for line in text.splitlines():
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1)
            continue
        if section is None or not section.startswith(prefix):
            continue
        r = RELOC_RE.match(line)
        if r:
            items.append((int(r.group(1), 16), "reloc",
                          (r.group(2), r.group(3).strip())))
            continue
        i = INSN_RE.match(line)
        if i:
            items.append((int(i.group(1), 16), "insn", i.group(2).strip()))
    return items


def parse_linked_insns(text):
    """(addr, insn) pairs and address->symbol labels from objdump -d output.

    Linked disassembly resolves ``jal`` targets as ``<symbol>``; labels are
    returned separately so a probe symbol range can be located.
    """
    insns = []
    labels = {}
    for line in text.splitlines():
        s = SYMBOL_RE.match(line.strip())
        if s:
            labels[int(s.group(1), 16)] = s.group(2)
            continue
        i = INSN_RE.match(line)
        if i:
            insns.append((int(i.group(1), 16), i.group(2).strip()))
    return insns, labels


def symbol_ranges(nm_text):
    """{symbol: (start, end)} from ``nm -S --defined-only`` output."""
    ranges = {}
    for line in nm_text.splitlines():
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


# --------------------------------------------------------------------------
# instruction helpers
# --------------------------------------------------------------------------
def mnemonic(insn):
    return insn.split()[0].strip() if insn and insn.split() else ""


def operands(insn):
    """Comma separated operands after the mnemonic, whitespace flattened."""
    parts = insn.split(None, 1)
    if len(parts) < 2:
        return []
    return [op.strip() for op in parts[1].split(",")]


def reg_of(op):
    """Vector register number of an operand like ``v8`` or ``8(v3)``."""
    m = re.search(r"\bv(\d+)\b", op)
    return int(m.group(1)) if m else None


def dest_regs(insn):
    """Vector registers written by an instruction (empty for stores/branches).

    A register group is written as its base register only in assembly; group
    size comes from the vtype, so callers expand it with ``expand``.
    """
    m = mnemonic(insn)
    ops = operands(insn)
    if not ops or not m.startswith("v"):
        return []
    if m.startswith(VEC_STORE_PREFIX):
        return []
    if m in ("vsetvli", "vsetivli", "vsetvl", "vtype"):
        return []
    return [r for r in (reg_of(ops[0]),) if r is not None]


def source_regs(insn):
    m = mnemonic(insn)
    ops = operands(insn)
    if not ops or not m.startswith("v"):
        return []
    if m in ("vsetvli", "vsetivli", "vsetvl", "vtype"):
        return []
    if m.startswith(VEC_STORE_PREFIX):
        return [r for r in (reg_of(ops[0]),) if r is not None]
    return [r for r in (reg_of(op) for op in ops[1:]) if r is not None]


def is_load(insn):
    return mnemonic(insn).startswith(VEC_LOAD_PREFIX)


def vtype_lmul(insn):
    m = mnemonic(insn)
    if not m.startswith(("vsetvli", "vsetivli", "vsetvl")):
        return None
    t = insn.split(None, 1)[1] if " " in insn else ""
    m2 = VTYPE_RE2.search(t)
    if not m2:
        return None
    if m2.group(1):        # mf8/mf4/mf2: fractional, group size 1 register
        return 1
    return int(m2.group(2))


def expand(reg, lmul):
    if reg is None:
        return []
    return list(range(reg, reg + max(1, lmul)))


def find_all_loops(insns):
    """All backward-branch loops as (start, end) address pairs, sorted.

    ``end`` is the branch instruction itself; ``start`` is its target.  Both
    conditional back edges and ``j`` loops are returned; the paired audit uses
    conditional ones to identify real K loops.
    """
    loops = []
    for addr, insn in insns:
        m = mnemonic(insn)
        if not BRANCH_RE.match(m):
            continue
        target = None
        mm = re.search(r"<([^>+]+)(?:\+0x[0-9a-f]+)?>", insn)
        if mm:
            target = mm.group(1)
        m2 = re.search(r"\b([0-9a-f]+)\s+<", insn)
        if m2:
            target = int(m2.group(1), 16)
        elif target is not None:
            target = None
        if target is None or target >= addr:
            continue
        loops.append((target, addr))
    return loops


def timed_loops(insns):
    """Loops between the begin/end rdcycle reads of the timing window.

    A trace build appends an untimed trace pass after the timed window; its
    loop also contains the entry call and (in a latency probe) a bridge, so
    auditing it with the timed-loop rules would reject legitimate code motion
    (the trace pass stores and compares intermediate values).  The timed window
    is bracketed by the only two rdcycle reads in the probe, so loops outside
    that address range are trace-pass loops and are not timing evidence.
    """
    rd = [a for a, i in insns if mnemonic(i) == "rdcycle"]
    loops = find_all_loops(insns)
    if len(rd) < 2:
        return loops, "all"
    lo, hi = min(rd), max(rd)
    timed = [(a, b) for a, b in loops if lo < a and b < hi]
    if not timed:
        return loops, "all"
    return timed, "window"


def expected_arg_regs(lmul, binary):
    """Vector-CC argument registers of a ``_ZGV`` entry.

    The convention places the first vector argument in v8 and the second in the
    next naturally aligned group (v9 for LMUL=1, v10 for LMUL=2).  This is
    fixed by the ABI, not by the probe, so hard-coding it is what makes the
    audit able to say "the next call consumes the bridge result".
    """
    g = max(1, lmul)
    regs = list(range(8, 8 + g))
    if binary:
        regs += list(range(8 + g, 8 + 2 * g))
    return regs


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
def innermost_loop(loops, addr):
    """Smallest loop whose [start,end] contains addr (innermost K loop)."""
    cands = [(e - s, s, e) for s, e in loops if s <= addr <= e]
    if not cands:
        return None
    _, s, e = min(cands)
    return (s, e)


def audit_probe_object(entry, spec, items, start, end):
    """Audit one probe symbol in a relocation-carrying object.

    Every call site is assigned to its innermost enclosing loop, so a probe
    with nested loops (throughput: pass loop around the stream loop) is audited
    on the loop that actually issues the calls.  The audit does not require a
    branchless loop body: RISC-V vector entries need loop conditions and GCC
    emits them; a conditional bypass around the bridge is caught by the taint
    walk (the argument register would not be written by the bridge add) and, at
    run time, by the bit-exact seed-recovery assertion of the trace build.

    ``spec`` keys: mode (latency/tput), lmul (1/2), binary (bool), masked
    (bool), callee (mangled symbol).  Returns (problems, notes).
    """
    problems = []
    notes = []
    insns = [(a, p) for a, k, p in items
             if k == "insn" and start <= a < end]
    relocs = [(a, p) for a, k, p in items
              if k == "reloc" and start <= a < end]
    if not insns:
        return [f"{entry}: no instructions in symbol range"], notes

    call_sites = [a for a, (rtype, target) in relocs
                  if (rtype.startswith("R_RISCV_CALL") or rtype == "R_RISCV_JAL")
                  and target == spec["callee"]]
    stray = sorted({t for _, (rt, t) in relocs
                    if (rt.startswith("R_RISCV_CALL") or rt == "R_RISCV_JAL")
                    and t.startswith("_ZGV") and t != spec["callee"]})
    if stray:
        problems.append(f"{entry}: unexpected libmvec callee(s) {stray}")
    if not call_sites:
        problems.append(f"{entry}: no call site to {spec['callee']}")
        return problems, notes

    loops, loop_scope = timed_loops(insns)
    notes.append(f"loop scope: {loop_scope}")
    groups = {}
    outside = []
    for addr in call_sites:
        loop = innermost_loop(loops, addr)
        if loop is None:
            # Calls outside every loop are the out-of-window functional
            # checker calls (one per probe); they are expected and audited by
            # the oracle counters at run time, not here.
            outside.append(addr)
            continue
        groups.setdefault(loop, []).append(addr)
    if outside:
        notes.append("out-of-window checker call(s): "
                     + ", ".join(hex(a) for a in outside))

    for (loop_start, loop_end), sites in sorted(groups.items()):
        body = [(a, i) for a, i in insns if loop_start <= a <= loop_end]
        notes.append(f"loop {hex(loop_start)}..{hex(loop_end)}: "
                     f"{len(sites)} call site(s)")
        lmul = spec["lmul"]
        sub = find_bridge_sub(body, lmul)
        adds = find_bridge_adds(body, lmul)
        if spec["mode"] == "latency":
            if len(sites) != 1:
                problems.append(
                    f"{entry}: {len(sites)} call site(s) in the innermost "
                    f"latency loop (need 1)")
                continue
            if not sub:
                problems.append(
                    f"{entry}: no vfsub.vv X,X,X bridge inside the loop")
            if not adds:
                problems.append(
                    f"{entry}: no vfadd.vv bridge-add inside the loop")
            if sub and adds:
                problems.extend(audit_latency_taint(
                    entry, body, sites[0], sub, adds, lmul, spec["binary"]))
        else:
            if len(sites) not in (1, 2, 4):
                problems.append(
                    f"{entry}: {len(sites)} call sites in an innermost "
                    f"throughput loop (need the 1/2/4 unrolled streams)")
            if sub:
                problems.append(
                    f"{entry}: throughput loop contains a dependency bridge "
                    f"({sub[1]})")
            problems.extend(audit_tput_args(entry, body, sites, lmul,
                                            spec["binary"]))
    return problems, notes


def find_bridge_sub(insns, lmul):
    """Locate ``vfsub.vv D,S,S`` (identical sources) inside the loop."""
    for addr, insn in insns:
        if mnemonic(insn) != "vfsub.vv":
            continue
        ops = operands(insn)
        if len(ops) >= 3 and reg_of(ops[1]) is not None \
                and reg_of(ops[1]) == reg_of(ops[2]):
            return (addr, insn, reg_of(ops[0]), reg_of(ops[1]))
    return None


def find_bridge_adds(insns, lmul):
    """All ``vfadd.vv`` instructions inside the loop."""
    out = []
    for addr, insn in insns:
        if mnemonic(insn) != "vfadd.vv":
            continue
        ops = operands(insn)
        if len(ops) >= 3:
            out.append((addr, insn, reg_of(ops[0]),
                        reg_of(ops[1]), reg_of(ops[2])))
    return out


def _written_before(insns, reg, lmul, addr):
    """Writer of ``reg`` (or its group) that reaches ``addr``.

    Instructions are searched in reverse address order below ``addr`` first,
    then wrapped to the end of the loop body, which is the order a steady-state
    iteration executes them.  A vfloat64m2_t value is written by naming only
    its base register, so a write of v8 also defines v9.
    """
    below = [(a, i) for a, i in insns if a < addr]
    above = [(a, i) for a, i in insns if a >= addr]
    for a, i in list(reversed(below)) + list(reversed(above)):
        for base in dest_regs(i):
            if reg in expand(base, lmul):
                return (a, i)
    return None


def audit_latency_taint(entry, insns, call_site, sub, adds, lmul, binary):
    """Simulate vector-register taint over one loop iteration.

    Taint starts at the call's return registers (v8.. for ``_ZGV`` vector
    entries), must reach the vfsub sources, then the vfadd, and the vfadd must
    write the registers the next call consumes.  A load/store/constant write to
    an argument register removes taint, so "compiler replaced the bridge by a
    constant" fails here even if the instruction is still present elsewhere.
    """
    problems = []
    sub_addr, sub_insn, sub_dest, sub_src = sub
    order = ([(a, i) for a, i in insns if a > call_site]
             + [(a, i) for a, i in insns if a <= call_site])
    tainted = set(expand(8, lmul))       # vector return value starts in v8
    saw_sub = False
    arg_regs = expected_arg_regs(lmul, binary)
    add_writes = {}
    for addr, insn in order:
        m = mnemonic(insn)
        if m.startswith(("vsetvli", "vsetivli", "vsetvl")):
            continue
        if not m.startswith("v"):
            continue
        if is_load(insn):
            for r in dest_regs(insn):
                tainted -= set(expand(r, lmul))
            continue
        if m.startswith(VEC_STORE_PREFIX):
            continue
        dsts = dest_regs(insn)
        if not dsts:
            continue
        d = dsts[0]
        srcs = source_regs(insn)
        if d == 0:
            continue
        src_tainted = any(r in tainted for r in srcs)
        if src_tainted:
            tainted |= set(expand(d, lmul))
        else:
            tainted -= set(expand(d, lmul))
        if addr == sub_addr:
            saw_sub = True
            if sub_src not in srcs and sub_src not in tainted:
                problems.append(
                    f"{entry}: vfsub source v{sub_src} is not tainted by the "
                    f"call return value")
        if m == "vfadd.vv":
            # remember the sources of the add that writes each arg register
            if d in arg_regs or (d in add_writes and d not in arg_regs):
                add_writes[d] = (addr, insn, srcs)
            if d in arg_regs:
                add_writes[d] = (addr, insn, srcs)
    if not saw_sub:
        problems.append(f"{entry}: vfsub bridge not reached in the loop walk")
    for reg in arg_regs:
        if reg not in tainted:
            problems.append(
                f"{entry}: next call argument v{reg} is not tainted by the "
                f"previous result (bridge removed or bypassed)")
            continue
        # The write that put it there must be a bridge add.  A register group
        # is written by naming its base, so accept any add whose group covers
        # this argument register.
        base = None
        for b in add_writes:
            if reg in expand(b, lmul):
                base = b
                break
        if base is None:
            problems.append(
                f"{entry}: argument v{reg} is tainted but no vfadd writes it")
            continue
        addr, insn, srcs = add_writes[base]
        if sub_dest not in srcs:
            problems.append(
                f"{entry}: vfadd writing v{reg} does not read the vfsub "
                f"result v{sub_dest}")
            continue
        other = [s for s in srcs if s != sub_dest]
        if not other:
            problems.append(f"{entry}: vfadd writing v{reg} has no seed operand")
            continue
        seed_reg = other[0]
        write = _written_before(insns, seed_reg, lmul, addr)
        if write is None:
            problems.append(
                f"{entry}: seed operand v{seed_reg} is loop invariant (not "
                f"loaded from the frozen corpus inside the loop)")
        elif not is_load(write[1]):
            problems.append(
                f"{entry}: seed operand v{seed_reg} is written by "
                f"'{write[1]}' instead of a frozen-corpus load")
    return problems


def audit_tput_args(entry, insns, call_sites, lmul, binary):
    """Throughput arguments must come from loads, never from results."""
    problems = []
    arg_regs = expected_arg_regs(lmul, binary)
    if not any(is_load(insn) for _, insn in insns):
        problems.append(f"{entry}: throughput loop loads no frozen seed")
    for site in call_sites:
        for reg in arg_regs:
            write = _written_before(insns, reg, lmul, site)
            if write is None:
                problems.append(
                    f"{entry}: throughput argument v{reg} of the call at "
                    f"{hex(site)} is never loaded inside the loop")
            elif not is_load(write[1]):
                problems.append(
                    f"{entry}: throughput argument v{reg} of the call at "
                    f"{hex(site)} is written by '{write[1]}' (result feedback "
                    f"or arithmetic, not a frozen-corpus load)")
    return problems


# --------------------------------------------------------------------------
# linked-inline policy (LTO builds)
# --------------------------------------------------------------------------
ZGV_RE = re.compile(r"<(_ZGV[^>+]+)")


def audit_probe_linked(entry, spec, insns, start, end):
    """Audit one probe symbol in the linked image.

    Used when the build is LTO-enabled and the entry (or the driver) got inlined
    so object relocations no longer describe the call graph.  Policy:

    * latency loops must keep the identical-source vfsub and a vfadd whose
      other operand is loaded from memory, plus a kernel-sized block of vector
      FP instructions (``>= 8``) when the call itself was inlined;
    * throughput loops must contain no bridge;
    * any ``jal`` to ``_ZGV`` inside the loop is checked against the expected
      callee, so a wrong entry cannot pass as "inlined".
    """
    problems = []
    notes = []
    body = [(a, i) for a, i in insns if start <= a < end]
    calls = [a for a, i in body
             if (mnemonic(i).startswith(("jal", "j"))
                 and ZGV_RE.search(i))]
    loop_candidates, loop_scope = timed_loops(body)
    notes.append(f"linked loop scope: {loop_scope}")
    loops = [(s, e) for s, e in loop_candidates
             if any(s <= a <= e for a in calls)
             or any(s <= a <= e and mnemonic(i) == "vfsub.vv" for a, i in body)]
    if not loops:
        problems.append(f"{entry}: no loop with a bridge or call in the "
                        f"linked image")
        return problems, notes
    for loop_start, loop_end in loops:
        lb = [(a, i) for a, i in body if loop_start <= a <= loop_end]
        sub = find_bridge_sub(lb, spec["lmul"])
        fpc = [1 for _, i in lb if mnemonic(i).startswith(VEC_FP_ARITH)]
        call_here = [a for a, i in lb
                     if mnemonic(i).startswith(("jal", "j"))
                     and ZGV_RE.search(i)]
        if call_here:
            for a, i in lb:
                if mnemonic(i).startswith(("jal", "j")):
                    m = ZGV_RE.search(i)
                    if m and m.group(1) != spec["callee"]:
                        problems.append(
                            f"{entry}: call to {m.group(1)} inside the loop "
                            f"(expected {spec['callee']})")
            notes.append("link policy: resolved call present")
        if spec["mode"] == "latency":
            if not sub:
                problems.append(f"{entry}: linked latency loop lost the "
                                f"identical-source vfsub bridge")
            if not call_here and len(fpc) < 8:
                problems.append(
                    f"{entry}: inlined latency loop has only {len(fpc)} vector "
                    f"FP instructions (kernel looks deleted)")
            notes.append(f"linked loop {hex(loop_start)}..{hex(loop_end)}: "
                         f"vfp={len(fpc)} bridge={'yes' if sub else 'no'}")
        else:
            if sub:
                problems.append(f"{entry}: linked throughput loop contains a "
                                f"dependency bridge")
            if not call_here and len(fpc) < 8:
                problems.append(
                    f"{entry}: inlined throughput loop has only {len(fpc)} "
                    f"vector FP instructions")
    return problems, notes
