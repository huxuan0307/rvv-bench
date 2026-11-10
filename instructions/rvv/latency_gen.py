#!/usr/bin/env python3
"""Generate the first, conservative RVV RAW-latency assembly slice.

The existing benchmark generator remains the source of truth for the active
instruction forms.  This generator deliberately emits only templates whose
register dependency is explicit and easy to validate; unsupported forms are
reported on stderr instead of being emitted as potentially illegal chains.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass


KIND_NAMES = {
    0: "vector_direct",
    1: "vector_to_mask_bridge",
    2: "mask_to_scalar_bridge",
    3: "mask_to_vector_bridge",
    4: "load_to_address_use",
    5: "mask_direct",
}


@dataclass(frozen=True)
class Form:
    opcode: str
    kind: int


def is_move(op: str) -> bool:
    return op.startswith("vmv") or op.startswith("vfmv")


def is_store(op: str) -> bool:
    return (
        op.startswith("vse")
        or op.startswith("vsse")
        or op.startswith("vsseg")
        or op.startswith(("vsuxei", "vsoxei", "vsuxseg", "vsoxseg"))
        or op.startswith("vssseg")
        or op == "vsm.v"
    )


def is_load(op: str) -> bool:
    return (
        op.startswith("vle")
        or op.startswith(("vluxei", "vloxei", "vluxseg", "vloxseg"))
        or op.startswith("vlse")
        or op.startswith("vlseg")
        or op.startswith("vlsseg")
        or op == "vlm.v"
    )


def is_vector_to_mask(op: str) -> bool:
    prefixes = (
        "vmseq.", "vmsne.", "vmslt", "vmsle", "vmsgt",
        "vmfeq.", "vmfne.", "vmflt.", "vmfle.", "vmfgt.", "vmfge.",
        "vmadc.", "vmsbc.",
    )
    return op.startswith(prefixes)


def is_mask_direct(op: str) -> bool:
    return op in {
        "vmand.mm", "vmandn.mm", "vmor.mm", "vmorn.mm",
        "vmxor.mm", "vmxnor.mm", "vmnand.mm", "vmnor.mm",
        "vmsbf.m", "vmsof.m", "vmsif.m",
    }


def is_simple_direct(op: str) -> bool:
    """Return whether the generic v8/v16 chain template is conservative."""
    if op in {"vcompress.vm", "vfmerge.vfm"} or op.startswith("vmerge."):
        return True
    if not any(op.endswith(suffix) for suffix in (
        ".vv", ".vx", ".vi", ".vf", ".v", ".vs", ".wv", ".wx", ".wi", ".wf",
    )):
        return False
    if op in {"vid.v", "viota.m"}:
        return False
    if is_move(op) or is_store(op) or is_load(op) or is_vector_to_mask(op) or is_mask_direct(op):
        return False
    # These forms require width-changing or instruction-specific overlap
    # templates. They are kept out of this first generated slice.
    complex_prefixes = (
        "vn", "vw", "vfw", "vwm", "vwsll", "vsext", "vzext",
        "vfncvt", "vfwcvt",
        "vaes", "vsha", "vghsh", "vgmul",
    )
    return not op.startswith(complex_prefixes)


def classify(op: str) -> int | None:
    if op in {"add", "mul", "vmv.v.i", "vid.v"}:
        return None
    # Whole-register memory transfers need dedicated dependency templates;
    # never treat their .v suffix as a generic two-register arithmetic form.
    if re.fullmatch(r"v(?:l[1248]re(?:8|16|32|64)|s[1248]r)\.v", op):
        return None
    # Indexed memory needs dedicated index/address dependency templates.
    if re.fullmatch(r"v[ls][uo]xei(8|16|32|64)\.v", op):
        return None
    # Carry-in forms have an architectural v0 carry operand. They need a
    # dedicated alternating-mask template and are not emitted by this slice.
    if op.startswith(("vmadc.", "vmsbc.")) and op.endswith((".vvm", ".vxm", ".vim")):
        return None
    if is_store(op):
        return None
    if is_load(op):
        # Keep the first load slice legal under the initial e32/m1 driver.
        if op == "vlm.v" or re.match(r"^vl(?:e|se)(8|16|32|64)(ff)?\.v$", op):
            return 4
        if re.match(r"^vlseg2e(8|16|32|64)(ff)?\.v$", op):
            return 4
        return None
    if op in {"vcpop.m", "vfirst.m"}:
        return 2
    if op == "viota.m":
        return 3
    if is_vector_to_mask(op):
        return 1
    if is_mask_direct(op):
        return 5
    if is_simple_direct(op):
        return 0
    return None


def vector_direct_step(op: str) -> str | None:
    if op == "vcompress.vm":
        return "vcompress.vm v8, v16, v0"
    if op == "vfmerge.vfm":
        return "vfmerge.vfm v8, v8, ft0, v0"
    if op == "vmerge.vvm":
        return "vmerge.vvm v8, v8, v16, v0"
    if op == "vmerge.vxm":
        return "vmerge.vxm v8, v8, t0, v0"
    if op == "vmerge.vim":
        return "vmerge.vim v8, v8, 13, v0"
    if op.endswith(".vv"):
        return f"{op} v8, v8, v16"
    if op.endswith(".vx"):
        # FMA forms use scalar-first operand order in the existing generator.
        if op.startswith(("vmadd", "vmacc", "vnmsub", "vnmsac")):
            return f"{op} v8, t0, v8"
        return f"{op} v8, v8, t0"
    if op.endswith(".vi"):
        return f"{op} v8, v8, 13"
    if op.endswith(".vf"):
        if op.startswith(("vfmadd", "vfmsub", "vfmacc", "vfmsac", "vfnm")):
            return f"{op} v8, ft0, v8"
        return f"{op} v8, v8, ft0"
    if op.endswith(".v"):
        return f"{op} v8, v8"
    if op.endswith(".vs"):
        if op.startswith(("vaes",)):
            return f"{op} v8, v8"
        return f"{op} v8, v8, v16"
    if op.endswith(".wv"):
        return f"{op} v8, v8, v16"
    if op.endswith(".wx"):
        return f"{op} v8, v8, t0"
    if op.endswith(".wi"):
        return f"{op} v8, v8, 13"
    if op.endswith(".wf"):
        return f"{op} v8, v8, ft0"
    return None


def nonoverlap_step(op: str, index: int) -> str | None:
    """Emit a RAW step for forms whose vd must not overlap their vector source."""
    dst = "v8" if index % 2 == 0 else "v16"
    src = "v16" if index % 2 == 0 else "v8"

    if op.endswith(".vv"):
        return f"{op} {dst}, {src}, v24"
    if op.endswith(".vx"):
        return f"{op} {dst}, {src}, t0"
    if op.endswith(".vi"):
        return f"{op} {dst}, {src}, 1"
    if op.endswith(".vf"):
        return f"{op} {dst}, {src}, ft0"
    return None


def needs_nonoverlap_step(op: str) -> bool:
    if op in {
        "vslideup.vi", "vslideup.vx", "vslide1up.vx",
        "vfslide1up.vf",
    }:
        return True
    return op.startswith("vrgather.") or op == "vrgatherei16.vv"


def vector_mask_step(op: str) -> str | None:
    if op.endswith(".vv"):
        return f"{op} v0, v8, v16"
    if op.endswith(".vx"):
        return f"{op} v0, v8, t0"
    if op.endswith(".vi"):
        return f"{op} v0, v8, 13"
    if op.endswith(".vf"):
        return f"{op} v0, v8, ft0"
    if op.endswith(".vvm"):
        return f"{op} v0, v8, v16, v1"
    if op.endswith(".vxm"):
        return f"{op} v0, v8, t0, v1"
    if op.endswith(".vim"):
        return f"{op} v0, v8, 13, v1"
    return None


def load_step(op: str) -> str:
    if op == "vlm.v" or op.startswith("vle") or op.startswith("vlseg"):
        load = f"{op} v8, (t0)"
    else:
        load = f"{op} v8, (t0), t2"
    return "\n".join((load, "vmv.x.s t1, v8", "add t0, s0, t1"))


def setup_for(kind: int, op: str) -> list[str]:
    if kind == 4:
        return ["mv t0, s0", "li t2, 8"]
    if kind in (1, 2, 3, 5):
        return ["vid.v v2", "vmseq.vi v0, v2, 0"]
    return ["vmv.v.i v8, 1", "vmv.v.i v16, 1", "vmv.v.i v24, 1"]


def emit_step(form: Form, index: int) -> list[str]:
    op = form.opcode
    if form.kind == 0:
        if needs_nonoverlap_step(op):
            step = nonoverlap_step(op, index)
            if step is None:
                raise ValueError(op)
            return [step]
        if form.opcode == "vcompress.vm":
            dst = "v8" if index % 2 == 0 else "v16"
            src = "v16" if index % 2 == 0 else "v8"
            return [f"vcompress.vm {dst}, {src}, v0"]
        if form.opcode.startswith(("vred", "vfred")) and form.opcode.endswith(".vs"):
            dst = "v8" if index % 2 == 0 else "v16"
            src = "v16" if index % 2 == 0 else "v8"
            return [f"{form.opcode} {dst}, {src}, v24"]
        step = vector_direct_step(op)
        if step is None:
            raise ValueError(op)
        return [step]
    if form.kind == 1:
        step = vector_mask_step(op)
        if step is None:
            raise ValueError(op)
        return [step, "vadd.vv v8, v8, v16, v0.t"]
    if form.kind == 2:
        src = "v0" if index % 2 == 0 else "v1"
        dst = "v1" if index % 2 == 0 else "v0"
        op_name = op
        return [f"{op_name} t0, {src}", f"vmseq.vx {dst}, v2, t0"]
    if form.kind == 3:
        src = "v0" if index % 2 == 0 else "v1"
        dst = "v1" if index % 2 == 0 else "v0"
        return [f"viota.m {dst}, {src}", f"vmseq.vi {src}, {dst}, 0"]
    if form.kind == 4:
        return load_step(op).splitlines()
    if form.kind == 5:
        if op in {"vmsbf.m", "vmsof.m", "vmsif.m"}:
            src = "v16" if index % 2 == 0 else "v8"
            dst = "v8" if index % 2 == 0 else "v16"
            return [f"{op} {dst}, {src}"]
        return [f"{op} v8, v8, v16"]
    raise ValueError(form.kind)


def emit_chain(form: Form, chain: int) -> None:
    for index in range(chain):
        for line in emit_step(form, index):
            print(f"  {line}")


def emit() -> None:
    raw = {line.strip() for line in sys.stdin if line.strip()}
    forms: list[Form] = []
    skipped: dict[str, int] = {}
    for op in sorted(raw):
        kind = classify(op)
        if kind is None:
            skipped[op] = 1
        else:
            forms.append(Form(op, kind))

    print("# generated by latency_gen.py; do not edit")
    print('#include "config.h"')
    print("#define LATENCY_SAVE \"sd\"")
    print("#if __riscv_xlen == 32")
    print("#define lx lw")
    print("#define sx sw")
    print("#define defptr .word")
    print("#else")
    print("#define lx ld")
    print("#define sx sd")
    print("#define defptr .dword")
    print("#endif")
    print("#define VL s3")
    print("#define t7 s4")
    print("#define t8 s5")
    print("\n.data\n.balign 8")
    print(".global latency_count")
    print("latency_count:")
    print(f"  .dword {len(forms)}")
    for array, values in (("latency_names", [f"latency_name_{i}" for i in range(len(forms))]),
                          ("latency_kinds", [str(f.kind) for f in forms])):
        print(f".global {array}\n{array}:")
        for value in values:
            print(f"  .dword {value}")
    for i, form in enumerate(forms):
        print(f"latency_name_{i}: .string \"{form.opcode}\"")
    for chain in (4, 16, 64):
        print(f".global latency_funcs_{chain}\nlatency_funcs_{chain}:")
        for i in range(len(forms)):
            print(f"  .dword latency_{i}_c{chain}")

    print("\n.text\n.balign 8")
    for i, form in enumerate(forms):
        for chain in (4, 16, 64):
            print(f".global latency_{i}_c{chain}\nlatency_{i}_c{chain}:")
            for line in setup_for(form.kind, form.opcode):
                print(f"  {line}")
            print("  li a0, WARMUP")
            print("1:")
            emit_chain(form, chain)
            print("  addi a0, a0, -1")
            print("  bnez a0, 1b")
            print("  csrr t6, cycle")
            print("  li a0, LOOP")
            print("2:")
            print(".rept UNROLL")
            emit_chain(form, chain)
            print(".endr")
            print("  addi a0, a0, -1")
            print("  bnez a0, 2b")
            print("  csrr a0, cycle")
            print("  sub a0, a0, t6")
            print("  ret")

    print("\n/* Runtime wrapper: a0=function, a1=vtype, a2=AVL(0 means VLMAX), a3=base. */")
    print(".global run_latency\nrun_latency:")
    print("  addi sp, sp, -72")
    print("  sx ra, 8(sp)\n  sx s0, 16(sp)\n  sx s1, 24(sp)\n  sx s2, 32(sp)\n  sx VL, 40(sp)\n  sx t7, 48(sp)\n  sx t8, 56(sp)")
    print("  mv s1, a0\n  mv s2, a1\n  mv s0, a3\n  mv VL, a2")
    print("  vsetvl VL, VL, s2\n  bnez VL, 1f\n  vsetvl VL, x0, s2\n1:")
    print("  vmv.v.i v8, 1\n  vmv.v.i v16, 1\n  vmv.v.i v24, 1")
    print("  vid.v v2\n  vmseq.vi v0, v2, 0")
    print("  li t0, 1\n  li t2, 8\n  li t1, 0x3f800000\n  fmv.w.x ft0, t1")
    print("  jalr s1")
    print("  lx ra, 8(sp)\n  lx s0, 16(sp)\n  lx s1, 24(sp)\n  lx s2, 32(sp)\n  lx VL, 40(sp)\n  lx t7, 48(sp)\n  lx t8, 56(sp)\n  addi sp, sp, 72\n  ret")

    print(f"# forms={len(forms)} skipped={len(skipped)}", file=sys.stderr)
    print("# skipped: " + ", ".join(sorted(skipped)), file=sys.stderr)


if __name__ == "__main__":
    emit()
