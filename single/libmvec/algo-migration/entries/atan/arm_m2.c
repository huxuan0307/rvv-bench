/* ARM SVE-derived atan vector ABI adapter.
   Masked arguments are scrubbed in integer lanes.
   Call the kernel in RNE and restore the caller mode.  */
#include <riscv_vector.h>
#include <stddef.h>
#include <stdint.h>

#include "../../kernels/atan/arm.c"

#define AM_FIXED_VL 4

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
static inline vuint64m2_t
am_scrub_2 (vbool32_t mask, vuint64m2_t bits, uint64_t scrub, size_t vl)
{
  vuint64m2_t pattern = __riscv_vmv_v_x_u64m2 ((uint64_t)scrub, vl);
  return __riscv_vmerge_vvm_u64m2 (pattern, bits, mask, vl);
}

static inline vfloat64m2_t
am_scrub_f_2 (vbool32_t mask, vfloat64m2_t x, uint64_t scrub, size_t vl)
{
  return __riscv_vreinterpret_v_u64m2_f64m2 (
      am_scrub_2 (mask, __riscv_vreinterpret_v_f64m2_u64m2 (x), scrub, vl));
}

/* _ZGV2N4v_atan: unmasked fixed 4-lane. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m2_t
_ZGV2N4v_atan (vfloat64m2_t x)
{
  const size_t vl = __riscv_vsetvl_e64m2 (4);
  unsigned old = am_enter_rne ();
  vfloat64m2_t r = atan_arm_m2 (x, __riscv_vmset_m_b32 (vl), vl);
  am_leave_rne (old);
  return r;
}
/* _ZGV2Nxv_atan: unmasked VLMAX. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m2_t
_ZGV2Nxv_atan (vfloat64m2_t x)
{
  const size_t vl = __riscv_vsetvlmax_e64m2 ();
  /* The ABI guarantees VLEN >= 128, so VLMAX is never 0; the
     guard is a plain branch, never a vsetvl-zero preamble. */
  if (__builtin_expect (vl == 0, 0))
    return x;
  unsigned old = am_enter_rne ();
  vfloat64m2_t r = atan_arm_m2 (x, __riscv_vmset_m_b32 (vl), vl);
  am_leave_rne (old);
  return r;
}
/* _ZGV2M4v_atan: masked fixed 4-lane. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m2_t
_ZGV2M4v_atan (vbool32_t mask, vfloat64m2_t x)
{
  const size_t vl = __riscv_vsetvl_e64m2 (4);
  unsigned old = am_enter_rne ();
  vfloat64m2_t r
      = atan_arm_m2 (am_scrub_f_2 (mask, x, 0x0000000000000000ULL, vl),
                     __riscv_vmset_m_b32 (vl), vl);
  am_leave_rne (old);
  return r;
}
/* _ZGV2Mxv_atan: masked VLMAX. */
/* flatten: the candidate core is a static inline function; -O2 alone may
   emit an out-of-line vector-ABI call that adds a whole-register move
   and a vsetvl-zero preamble to every entry. */
__attribute__ ((flatten)) vfloat64m2_t
_ZGV2Mxv_atan (vbool32_t mask, vfloat64m2_t x)
{
  const size_t vl = __riscv_vsetvlmax_e64m2 ();
  /* The ABI guarantees VLEN >= 128, so VLMAX is never 0; the
     guard is a plain branch, never a vsetvl-zero preamble. */
  if (__builtin_expect (vl == 0, 0))
    return x;
  unsigned old = am_enter_rne ();
  vfloat64m2_t r
      = atan_arm_m2 (am_scrub_f_2 (mask, x, 0x0000000000000000ULL, vl),
                     __riscv_vmset_m_b32 (vl), vl);
  am_leave_rne (old);
  return r;
}
