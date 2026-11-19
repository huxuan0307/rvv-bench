/* ARM SVE-derived atan2 vector ABI adapter.
   Masked arguments are scrubbed in integer lanes.
   Call the kernel in RNE and restore the caller mode.  */
#include <riscv_vector.h>
#include <stddef.h>
#include <stdint.h>

#include "../../kernels/atan2/arm.c"

#define AM_FIXED_VL 2

/* Save the caller rounding mode and select RNE for the candidate kernel.  The
 * glibc pilot2 kernels do the same with SET_ROUNDTONEAREST/RESTORE_FRM.  */
static inline unsigned
am_enter_rne (void)
{
  unsigned old;
  __asm__ volatile ("frrm %0" : "=r"(old) : : "memory");
  if (__builtin_expect (old != 0, 0))
    __asm__ volatile ("fsrm zero" : : : "memory");
  return old;
}

static inline void
am_leave_rne (unsigned old)
{
  if (__builtin_expect (old != 0, 0))
    __asm__ volatile ("fsrm %0" : : "r"(old) : "memory");
}

/* Inactive lanes are replaced by a function-specific bit pattern with no FP
 * instruction, so a signaling NaN in an inactive lane cannot raise.  The
 * kernel then runs with an all-ones predicate (glibc pilot2 convention: no
 * whole-register copy of the v0 mask, hence no vsetvl-zero preamble).  */
static inline vuint64m1_t
am_scrub_1 (vbool64_t mask, vuint64m1_t bits, uint64_t scrub, size_t vl)
{
  vuint64m1_t pattern = __riscv_vmv_v_x_u64m1 ((uint64_t)scrub, vl);
  return __riscv_vmerge_vvm_u64m1 (pattern, bits, mask, vl);
}

static inline vfloat64m1_t
am_scrub_f_1 (vbool64_t mask, vfloat64m1_t x, uint64_t scrub, size_t vl)
{
  return __riscv_vreinterpret_v_u64m1_f64m1 (
      am_scrub_1 (mask, __riscv_vreinterpret_v_f64m1_u64m1 (x), scrub, vl));
}

/* _ZGV1N2vv_atan2: unmasked fixed 2-lane. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m1_t
_ZGV1N2vv_atan2 (vfloat64m1_t y, vfloat64m1_t x)
{
  const size_t vl = __riscv_vsetvl_e64m1 (2);
  unsigned old = am_enter_rne ();
  vfloat64m1_t r = atan2_arm_m1 (y, x, __riscv_vmset_m_b64 (vl), vl);
  am_leave_rne (old);
  return r;
}
/* _ZGV1Nxvv_atan2: unmasked VLMAX. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m1_t
_ZGV1Nxvv_atan2 (vfloat64m1_t y, vfloat64m1_t x)
{
  const size_t vl = __riscv_vsetvlmax_e64m1 ();
  /* The ABI guarantees VLEN >= 128, so VLMAX is never 0; the
     guard is a plain branch, never a vsetvl-zero preamble. */
  if (__builtin_expect (vl == 0, 0))
    return y;
  unsigned old = am_enter_rne ();
  vfloat64m1_t r = atan2_arm_m1 (y, x, __riscv_vmset_m_b64 (vl), vl);
  am_leave_rne (old);
  return r;
}
/* _ZGV1M2vv_atan2: masked fixed 2-lane. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m1_t
_ZGV1M2vv_atan2 (vbool64_t mask, vfloat64m1_t y, vfloat64m1_t x)
{
  const size_t vl = __riscv_vsetvl_e64m1 (2);
  unsigned old = am_enter_rne ();
  vfloat64m1_t r
      = atan2_arm_m1 (am_scrub_f_1 (mask, y, 0x0000000000000000ULL, vl),
                      am_scrub_f_1 (mask, x, 0x3ff0000000000000ULL, vl),
                      __riscv_vmset_m_b64 (vl), vl);
  am_leave_rne (old);
  return r;
}
/* _ZGV1Mxvv_atan2: masked VLMAX. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m1_t
_ZGV1Mxvv_atan2 (vbool64_t mask, vfloat64m1_t y, vfloat64m1_t x)
{
  const size_t vl = __riscv_vsetvlmax_e64m1 ();
  /* The ABI guarantees VLEN >= 128, so VLMAX is never 0; the
     guard is a plain branch, never a vsetvl-zero preamble. */
  if (__builtin_expect (vl == 0, 0))
    return y;
  unsigned old = am_enter_rne ();
  vfloat64m1_t r
      = atan2_arm_m1 (am_scrub_f_1 (mask, y, 0x0000000000000000ULL, vl),
                      am_scrub_f_1 (mask, x, 0x3ff0000000000000ULL, vl),
                      __riscv_vmset_m_b64 (vl), vl);
  am_leave_rne (old);
  return r;
}
