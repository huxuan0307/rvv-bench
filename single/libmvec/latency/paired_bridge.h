/* Exact-input dependency-reset bridge for the paired measurement protocol.
 *
 * The legacy affine maps in latency_map.h close the latency chain by feeding a
 * *contracted* result back into the next call, so the measured input workpoint
 * drifts away from the frozen corpus.  The paired protocol needs the opposite:
 * every call consumes a frozen seed bit pattern, and the dependency on the
 * previous call is introduced by FP arithmetic that is provably a +0.0:
 *
 *     d      = r - r            (vfsub.vv, both sources identical)
 *     x_next = seed + d         (vfadd.vv, seed loaded from the frozen header)
 *
 * For a finite r, IEEE-754 RNE gives d = +0.0 (not -0.0), and seed + (+0.0) is
 * bit-identical to seed for every finite non-zero normal seed.  The next call
 * therefore consumes exactly the frozen header bit pattern while still waiting
 * for r.  The bridge is a measurement device, not an algorithm change.
 *
 * Why inline asm: the two operations are written as a volatile asm with an
 * explicit "f(seed, f(r, r))" data flow so the compiler cannot fold r-r to a
 * constant or reorder the bridge out of the window.  Fast-math is not enabled
 * anywhere in the latency build, and the object/ELF audit in
 * build_latency.py re-checks the emitted instruction sequence, so a compiler
 * that learned to fold the bridge would fail the build instead of silently
 * producing an input-inconsistent number.
 *
 * inf/NaN: d = r - r is NaN for infinite/NaN r, which would poison the next
 * argument.  The protocol restricts the frozen performance corpus to finite
 * normal inputs (the loader rejects other bit patterns); the trace build also
 * asserts every r is finite bit-by-bit.  Inf/NaN entries of the corpus stay
 * correctness-only inputs and never enter this chain.
 *
 * Masked entries: the inactive lanes of a masked call are architecturally
 * undefined.  They are explicitly replaced by a finite +0.0 vector with an
 * integer merge *before* the bridge, so a garbage inactive NaN cannot leak
 * into d; the active lanes keep the real return dependency.  The merge costs
 * one instruction per call and is part of the masked candidate's measured
 * bridge cost (unmasked candidates do not pay it).
 */
#ifndef PAIRED_BRIDGE_H
#define PAIRED_BRIDGE_H

#include <riscv_vector.h>

#if !defined(__riscv_vector)
#error "paired_bridge.h needs the vector extension (-march=rva23u64)"
#endif

#define PAIR_AI static inline __attribute__ ((always_inline))

/* d = r - r.  The two source operands of vfsub.vv are the same register, which
 * is what makes the instruction a value-independent +0.0 producer that still
 * has a true register/FP data dependency on the call's return value. */
PAIR_AI vfloat64m1_t
lat_pair_zero_m1 (vfloat64m1_t r)
{
  vfloat64m1_t d;
  __asm__ volatile ("vfsub.vv %0, %1, %1" : "=vr"(d) : "vr"(r));
  return d;
}

PAIR_AI vfloat64m2_t
lat_pair_zero_m2 (vfloat64m2_t r)
{
  vfloat64m2_t d;
  __asm__ volatile ("vfsub.vv %0, %1, %1" : "=vr"(d) : "vr"(r));
  return d;
}

/* x = seed + d.  seed is always a vector loaded from the frozen header, so the
 * destination is a real FP sum of a frozen value and the bridge zero. */
PAIR_AI vfloat64m1_t
lat_pair_add_m1 (vfloat64m1_t seed, vfloat64m1_t d)
{
  vfloat64m1_t x;
  __asm__ volatile ("vfadd.vv %0, %1, %2" : "=vr"(x) : "vr"(seed), "vr"(d));
  return x;
}

PAIR_AI vfloat64m2_t
lat_pair_add_m2 (vfloat64m2_t seed, vfloat64m2_t d)
{
  vfloat64m2_t x;
  __asm__ volatile ("vfadd.vv %0, %1, %2" : "=vr"(x) : "vr"(seed), "vr"(d));
  return x;
}

/* Unary reset: x_next = seed_next + (r - r). */
PAIR_AI vfloat64m1_t
lat_pair_reset_m1 (vfloat64m1_t r, vfloat64m1_t seed_next)
{
  return lat_pair_add_m1 (seed_next, lat_pair_zero_m1 (r));
}

PAIR_AI vfloat64m2_t
lat_pair_reset_m2 (vfloat64m2_t r, vfloat64m2_t seed_next)
{
  return lat_pair_add_m2 (seed_next, lat_pair_zero_m2 (r));
}

/* Masked inactive-lane scrub: active lanes keep r, inactive lanes become +0.0
 * (vmerge.vvm selects op1 when the mask bit is clear).  Uses the integer merge
 * so the scrub itself cannot be folded into an FP identity: the value still
 * flows through a register write, and the audit accepts this pre-bridge insn.
 */
PAIR_AI vfloat64m1_t
lat_pair_san_m1 (vfloat64m1_t r, vbool64_t m, size_t vl)
{
  vfloat64m1_t zero = __riscv_vfmv_v_f_f64m1 (0.0, vl);
  return __riscv_vmerge (zero, r, m, vl);
}

PAIR_AI vfloat64m2_t
lat_pair_san_m2 (vfloat64m2_t r, vbool32_t m, size_t vl)
{
  vfloat64m2_t zero = __riscv_vfmv_v_f_f64m2 (0.0, vl);
  return __riscv_vmerge (zero, r, m, vl);
}

#endif /* PAIRED_BRIDGE_H */
