/* ARM SVE-derived RVV candidate kernel: double-precision acos.

   Algorithm and coefficients are taken from
     glibc/sysdeps/aarch64/fpu/sve/acos.c
   (Copyright (C) 2023-2026 Free Software Foundation, Inc., LGPL-2.1-or-later).

   For |x| <= 0.5:   z2 = x*x,          z = |x|,  Q = z + z*z2*P(z2)
   For |x| >  0.5:   z2 = (1-|x|)/2,    z = sqrt(z2), Q = z + z*z2*P(z2)
   acos(x) = pi/2 - sign(x)*Q           (|x| <= 0.5)
           = 2*Q                        ( 0.5 < x <= 1)
           = pi - 2*Q                   (-1 <= x < -0.5)
   The two branches share one degree-11 minimax polynomial P for
   (asin(sqrt(t)) - sqrt(t)) / (t*sqrt(t)) on [2^-106, 2^-2].

   RVV 1.0 port notes
   ------------------
   * Every exception-raising FP instruction is predicated by the caller mask
     `m` (or a mask derived from it); inactive lanes never signal.
   * (1-|x|)/2 is evaluated as an fma into 0.5 with scalar -0.5; the SVE masked
     sqrt's "inactive = |x|" behaviour is reproduced with an explicit vmerge
     because RISC-V masked intrinsics leave masked-off lanes undefined.
   * sqrt of a negative z2 for |x| > 1 produces the IEEE domain NaN and raises
     INVALID exactly like the SVE code; there is no scalar fallback.
   * |x| > 0.5 is decided on the magnitude bit pattern (same result for finite
     inputs; quiet NaNs do not raise INVALID in the branch select).

   Interface:
     static inline vfloat64m1_t acos_arm_m1 (vfloat64m1_t x, vbool64_t m,
   size_t vl); static inline vfloat64m2_t acos_arm_m2 (vfloat64m2_t x,
   vbool32_t m, size_t vl);
*/

#include <riscv_vector.h>
#include <stddef.h>
#include <stdint.h>

#define ACOS_PI2 0x1.921fb54442d18p+0
#define ACOS_HALF 0x1p-1

static const double acos_c[12]
    = { 0x1.555555555554ep-3, 0x1.3333333337233p-4,  0x1.6db6db67f6d9fp-5,
        0x1.f1c71fbd29fbbp-6, 0x1.6e8b264d467d6p-6,  0x1.1c5997c357e9dp-6,
        0x1.c86a22cd9389dp-7, 0x1.856073c22ebbep-7,  0x1.fd1151acb6bedp-8,
        0x1.087182f799c1dp-6, -0x1.6602748120927p-7, 0x1.cfa0dd1f9478p-6 };

static inline vfloat64m1_t
acos_poly_m1 (vfloat64m1_t z2, vfloat64m1_t z4, vfloat64m1_t z8, vbool64_t m,
              size_t vl)
{
  vfloat64m1_t p01 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, acos_c[1], __riscv_vfmv_v_f_f64m1 (acos_c[0], vl), vl);
  vfloat64m1_t p23 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, acos_c[3], __riscv_vfmv_v_f_f64m1 (acos_c[2], vl), vl);
  vfloat64m1_t p03 = __riscv_vfmadd_vv_f64m1_m (m, z4, p23, p01, vl);
  vfloat64m1_t p45 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, acos_c[5], __riscv_vfmv_v_f_f64m1 (acos_c[4], vl), vl);
  vfloat64m1_t p67 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, acos_c[7], __riscv_vfmv_v_f_f64m1 (acos_c[6], vl), vl);
  vfloat64m1_t p47 = __riscv_vfmadd_vv_f64m1_m (m, z4, p67, p45, vl);
  vfloat64m1_t p89 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, acos_c[9], __riscv_vfmv_v_f_f64m1 (acos_c[8], vl), vl);
  vfloat64m1_t p1011 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, acos_c[11], __riscv_vfmv_v_f_f64m1 (acos_c[10], vl), vl);
  vfloat64m1_t p811 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1011, p89, vl);
  vfloat64m1_t p411 = __riscv_vfmadd_vv_f64m1_m (m, z8, p811, p47, vl);
  return __riscv_vfmadd_vv_f64m1_m (m, z8, p411, p03, vl);
}

static inline vfloat64m2_t
acos_poly_m2 (vfloat64m2_t z2, vfloat64m2_t z4, vfloat64m2_t z8, vbool32_t m,
              size_t vl)
{
  vfloat64m2_t p01 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, acos_c[1], __riscv_vfmv_v_f_f64m2 (acos_c[0], vl), vl);
  vfloat64m2_t p23 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, acos_c[3], __riscv_vfmv_v_f_f64m2 (acos_c[2], vl), vl);
  vfloat64m2_t p03 = __riscv_vfmadd_vv_f64m2_m (m, z4, p23, p01, vl);
  vfloat64m2_t p45 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, acos_c[5], __riscv_vfmv_v_f_f64m2 (acos_c[4], vl), vl);
  vfloat64m2_t p67 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, acos_c[7], __riscv_vfmv_v_f_f64m2 (acos_c[6], vl), vl);
  vfloat64m2_t p47 = __riscv_vfmadd_vv_f64m2_m (m, z4, p67, p45, vl);
  vfloat64m2_t p89 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, acos_c[9], __riscv_vfmv_v_f_f64m2 (acos_c[8], vl), vl);
  vfloat64m2_t p1011 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, acos_c[11], __riscv_vfmv_v_f_f64m2 (acos_c[10], vl), vl);
  vfloat64m2_t p811 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1011, p89, vl);
  vfloat64m2_t p411 = __riscv_vfmadd_vv_f64m2_m (m, z8, p811, p47, vl);
  return __riscv_vfmadd_vv_f64m2_m (m, z8, p411, p03, vl);
}

static inline vfloat64m1_t
acos_arm_m1 (vfloat64m1_t x, vbool64_t m, size_t vl)
{
  vuint64m1_t ix = __riscv_vreinterpret_v_f64m1_u64m1 (x);
  vuint64m1_t isign = __riscv_vand_vx_u64m1 (ix, 0x8000000000000000ULL, vl);
  vfloat64m1_t ax = __riscv_vfabs_v_f64m1 (x, vl);
  vuint64m1_t iax = __riscv_vreinterpret_v_f64m1_u64m1 (ax);
  /* gt = |x| > 0.5 on the magnitude bit pattern.  */
  vbool64_t gt = __riscv_vmand_mm_b64 (
      __riscv_vmsgtu_vx_u64m1_b64 (iax, 0x3fe0000000000000ULL, vl), m, vl);

  vfloat64m1_t xx = __riscv_vfmul_vv_f64m1_m (m, x, x, vl);
  vfloat64m1_t hm = __riscv_vfmacc_vf_f64m1_m (
      gt, __riscv_vfmv_v_f_f64m1 (0.5, vl), -0.5, ax, vl);
  vfloat64m1_t z2 = __riscv_vmerge_vvm_f64m1 (xx, hm, gt, vl);
  vfloat64m1_t sq = __riscv_vfsqrt_v_f64m1_m (gt, z2, vl);
  vfloat64m1_t z = __riscv_vmerge_vvm_f64m1 (ax, sq, gt, vl);
  vfloat64m1_t z3 = __riscv_vfmul_vv_f64m1_m (m, z2, z, vl);
  vfloat64m1_t z4 = __riscv_vfmul_vv_f64m1_m (m, z2, z2, vl);
  vfloat64m1_t z8 = __riscv_vfmul_vv_f64m1_m (m, z4, z4, vl);
  vfloat64m1_t p = acos_poly_m1 (z2, z4, z8, m, vl);
  p = __riscv_vfmadd_vv_f64m1_m (m, p, z3, z, vl);

  /* mul = (gt ? -2.0 : 1.0) with the sign of x xor'ed in (SVE bit trick).  */
  vuint64m1_t oneb = __riscv_vmv_v_x_u64m1 (0x3ff0000000000000ULL, vl);
  vuint64m1_t mb = __riscv_vsll_vx_u64m1_m (gt, oneb, 10, vl);
  mb = __riscv_vmerge_vvm_u64m1 (oneb, mb, gt, vl);
  mb = __riscv_vxor_vv_u64m1 (mb, isign, vl);
  vfloat64m1_t mul = __riscv_vreinterpret_v_u64m1_f64m1 (mb);

  /* add = gt ? pi/2 - (sign|pi/2) : pi/2  (0 or pi for gt lanes).  */
  vfloat64m1_t pi2v = __riscv_vfmv_v_f_f64m1 (ACOS_PI2, vl);
  vuint64m1_t ab = __riscv_vor_vv_u64m1 (
      isign, __riscv_vreinterpret_v_f64m1_u64m1 (pi2v), vl);
  vfloat64m1_t an = __riscv_vreinterpret_v_u64m1_f64m1 (ab);
  vfloat64m1_t at = __riscv_vfsub_vv_f64m1_m (gt, pi2v, an, vl);
  vfloat64m1_t add = __riscv_vmerge_vvm_f64m1 (pi2v, at, gt, vl);

  return __riscv_vfnmsac_vv_f64m1_m (m, add, p, mul, vl);
}

static inline vfloat64m2_t
acos_arm_m2 (vfloat64m2_t x, vbool32_t m, size_t vl)
{
  vuint64m2_t ix = __riscv_vreinterpret_v_f64m2_u64m2 (x);
  vuint64m2_t isign = __riscv_vand_vx_u64m2 (ix, 0x8000000000000000ULL, vl);
  vfloat64m2_t ax = __riscv_vfabs_v_f64m2 (x, vl);
  vuint64m2_t iax = __riscv_vreinterpret_v_f64m2_u64m2 (ax);
  vbool32_t gt = __riscv_vmand_mm_b32 (
      __riscv_vmsgtu_vx_u64m2_b32 (iax, 0x3fe0000000000000ULL, vl), m, vl);

  vfloat64m2_t xx = __riscv_vfmul_vv_f64m2_m (m, x, x, vl);
  vfloat64m2_t hm = __riscv_vfmacc_vf_f64m2_m (
      gt, __riscv_vfmv_v_f_f64m2 (0.5, vl), -0.5, ax, vl);
  vfloat64m2_t z2 = __riscv_vmerge_vvm_f64m2 (xx, hm, gt, vl);
  vfloat64m2_t sq = __riscv_vfsqrt_v_f64m2_m (gt, z2, vl);
  vfloat64m2_t z = __riscv_vmerge_vvm_f64m2 (ax, sq, gt, vl);
  vfloat64m2_t z3 = __riscv_vfmul_vv_f64m2_m (m, z2, z, vl);
  vfloat64m2_t z4 = __riscv_vfmul_vv_f64m2_m (m, z2, z2, vl);
  vfloat64m2_t z8 = __riscv_vfmul_vv_f64m2_m (m, z4, z4, vl);
  vfloat64m2_t p = acos_poly_m2 (z2, z4, z8, m, vl);
  p = __riscv_vfmadd_vv_f64m2_m (m, p, z3, z, vl);

  vuint64m2_t oneb = __riscv_vmv_v_x_u64m2 (0x3ff0000000000000ULL, vl);
  vuint64m2_t mb = __riscv_vsll_vx_u64m2_m (gt, oneb, 10, vl);
  mb = __riscv_vmerge_vvm_u64m2 (oneb, mb, gt, vl);
  mb = __riscv_vxor_vv_u64m2 (mb, isign, vl);
  vfloat64m2_t mul = __riscv_vreinterpret_v_u64m2_f64m2 (mb);

  vfloat64m2_t pi2v = __riscv_vfmv_v_f_f64m2 (ACOS_PI2, vl);
  vuint64m2_t ab = __riscv_vor_vv_u64m2 (
      isign, __riscv_vreinterpret_v_f64m2_u64m2 (pi2v), vl);
  vfloat64m2_t an = __riscv_vreinterpret_v_u64m2_f64m2 (ab);
  vfloat64m2_t at = __riscv_vfsub_vv_f64m2_m (gt, pi2v, an, vl);
  vfloat64m2_t add = __riscv_vmerge_vvm_f64m2 (pi2v, at, gt, vl);

  return __riscv_vfnmsac_vv_f64m2_m (m, add, p, mul, vl);
}

/* ---- self test harness (unary kernels) ---------------------------------- */
