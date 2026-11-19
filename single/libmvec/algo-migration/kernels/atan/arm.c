/* ARM SVE-derived RVV candidate kernel: double-precision atan.

   Algorithm and coefficients are taken from
     glibc/sysdeps/aarch64/fpu/sve/atan.c
   (Copyright (C) 2023-2026 Free Software Foundation, Inc., LGPL-2.1-or-later).
   The SVE rewrite of that file is a port of the AArch64 vector atan: evaluate
     atan(x) = shift + z + z^3 * P(z^2),  z = x            if |x| <= 1
                                          z = -1/x, shift = sign(x)*pi/2  if
   |x| > 1 with a degree-19 odd polynomial P evaluated by a split Estrin
   scheme.

   RVV 1.0 port notes
   ------------------
   * Every exception-raising floating-point instruction (mul/fma/div) is issued
     with the caller mask `m` (m1: vbool64_t, m2: vbool32_t) or a mask derived
     from it.  Inactive lanes never signal.
   * The SVE masked-add used to fold the pi/2 shift is replaced by a masked add
     followed by an explicit vmerge, because the RISC-V masked intrinsic leaves
     inactive lanes undefined; vmerge does not raise FP exceptions.
   * |x| > 1 is decided by an unsigned integer comparison of the absolute-value
     bit pattern instead of SVE svacgt.  The result is identical for all finite
     inputs, and quiet NaNs no longer raise INVALID in the reduction itself;
     the arithmetic that follows still quiets sNaNs.
   * The polynomial uses scalar coefficient broadcasts (vfmadd.vf) instead of
     SVE svld1rq + svmla_lane; the arithmetic sequence and order of roundings
     are unchanged.
   * The all-ones mask path and the partial-mask path are the same instruction
     sequence, only the predicate differs, so the all-ones result is bit
     identical to the unpredicated algorithm.

   Interface:
     static inline vfloat64m1_t atan_arm_m1 (vfloat64m1_t x, vbool64_t m,
   size_t vl); static inline vfloat64m2_t atan_arm_m2 (vfloat64m2_t x,
   vbool32_t m, size_t vl);
*/

#include <riscv_vector.h>
#include <stddef.h>
#include <stdint.h>

#define ATAN_PI2 0x1.921fb54442d18p+0

static const double atan_c[20]
    = { -0x1.555555555552ap-2,  0x1.9999999995aebp-3,  -0x1.24924923923f6p-3,
        0x1.c71c7184288a2p-4,   -0x1.745d11fb3d32bp-4, 0x1.3b136a18051b9p-4,
        -0x1.110e6d985f496p-4,  0x1.e1bcf7f08801dp-5,  -0x1.ae644e28058c3p-5,
        0x1.82eeb1fed85c6p-5,   -0x1.59d7f901566cbp-5, 0x1.2c982855ab069p-5,
        -0x1.eb49592998177p-6,  0x1.69d8b396e3d38p-6,  -0x1.ca980345c4204p-7,
        0x1.dc050eafde0b3p-8,   -0x1.7ea70755b8eccp-9, 0x1.ba3da3de903e8p-11,
        -0x1.44a4b059b6f67p-13, 0x1.c4a45029e5a91p-17 };

static inline vfloat64m1_t
atan_poly_m1 (vfloat64m1_t z2, vfloat64m1_t z4, vfloat64m1_t z8,
              vfloat64m1_t z16, vbool64_t m, size_t vl)
{
  vfloat64m1_t p01 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[1], __riscv_vfmv_v_f_f64m1 (atan_c[0], vl), vl);
  vfloat64m1_t p23 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[3], __riscv_vfmv_v_f_f64m1 (atan_c[2], vl), vl);
  vfloat64m1_t p45 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[5], __riscv_vfmv_v_f_f64m1 (atan_c[4], vl), vl);
  vfloat64m1_t p67 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[7], __riscv_vfmv_v_f_f64m1 (atan_c[6], vl), vl);
  vfloat64m1_t p03 = __riscv_vfmadd_vv_f64m1_m (m, z4, p23, p01, vl);
  vfloat64m1_t p47 = __riscv_vfmadd_vv_f64m1_m (m, z4, p67, p45, vl);
  vfloat64m1_t p07 = __riscv_vfmadd_vv_f64m1_m (m, z8, p47, p03, vl);
  vfloat64m1_t p89 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[9], __riscv_vfmv_v_f_f64m1 (atan_c[8], vl), vl);
  vfloat64m1_t p1011 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[11], __riscv_vfmv_v_f_f64m1 (atan_c[10], vl), vl);
  vfloat64m1_t p811 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1011, p89, vl);
  vfloat64m1_t p1213 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[13], __riscv_vfmv_v_f_f64m1 (atan_c[12], vl), vl);
  vfloat64m1_t p1415 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[15], __riscv_vfmv_v_f_f64m1 (atan_c[14], vl), vl);
  vfloat64m1_t p1215 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1415, p1213, vl);
  vfloat64m1_t p1617 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[17], __riscv_vfmv_v_f_f64m1 (atan_c[16], vl), vl);
  vfloat64m1_t p1819 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, atan_c[19], __riscv_vfmv_v_f_f64m1 (atan_c[18], vl), vl);
  vfloat64m1_t p1619 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1819, p1617, vl);
  vfloat64m1_t p815 = __riscv_vfmadd_vv_f64m1_m (m, z8, p1215, p811, vl);
  vfloat64m1_t p819 = __riscv_vfmadd_vv_f64m1_m (m, z16, p1619, p815, vl);
  return __riscv_vfmadd_vv_f64m1_m (m, z16, p819, p07, vl);
}

static inline vfloat64m2_t
atan_poly_m2 (vfloat64m2_t z2, vfloat64m2_t z4, vfloat64m2_t z8,
              vfloat64m2_t z16, vbool32_t m, size_t vl)
{
  vfloat64m2_t p01 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[1], __riscv_vfmv_v_f_f64m2 (atan_c[0], vl), vl);
  vfloat64m2_t p23 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[3], __riscv_vfmv_v_f_f64m2 (atan_c[2], vl), vl);
  vfloat64m2_t p45 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[5], __riscv_vfmv_v_f_f64m2 (atan_c[4], vl), vl);
  vfloat64m2_t p67 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[7], __riscv_vfmv_v_f_f64m2 (atan_c[6], vl), vl);
  vfloat64m2_t p03 = __riscv_vfmadd_vv_f64m2_m (m, z4, p23, p01, vl);
  vfloat64m2_t p47 = __riscv_vfmadd_vv_f64m2_m (m, z4, p67, p45, vl);
  vfloat64m2_t p07 = __riscv_vfmadd_vv_f64m2_m (m, z8, p47, p03, vl);
  vfloat64m2_t p89 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[9], __riscv_vfmv_v_f_f64m2 (atan_c[8], vl), vl);
  vfloat64m2_t p1011 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[11], __riscv_vfmv_v_f_f64m2 (atan_c[10], vl), vl);
  vfloat64m2_t p811 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1011, p89, vl);
  vfloat64m2_t p1213 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[13], __riscv_vfmv_v_f_f64m2 (atan_c[12], vl), vl);
  vfloat64m2_t p1415 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[15], __riscv_vfmv_v_f_f64m2 (atan_c[14], vl), vl);
  vfloat64m2_t p1215 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1415, p1213, vl);
  vfloat64m2_t p1617 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[17], __riscv_vfmv_v_f_f64m2 (atan_c[16], vl), vl);
  vfloat64m2_t p1819 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, atan_c[19], __riscv_vfmv_v_f_f64m2 (atan_c[18], vl), vl);
  vfloat64m2_t p1619 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1819, p1617, vl);
  vfloat64m2_t p815 = __riscv_vfmadd_vv_f64m2_m (m, z8, p1215, p811, vl);
  vfloat64m2_t p819 = __riscv_vfmadd_vv_f64m2_m (m, z16, p1619, p815, vl);
  return __riscv_vfmadd_vv_f64m2_m (m, z16, p819, p07, vl);
}

static inline vfloat64m1_t
atan_arm_m1 (vfloat64m1_t x, vbool64_t m, size_t vl)
{
  vuint64m1_t ix = __riscv_vreinterpret_v_f64m1_u64m1 (x);
  vuint64m1_t isign = __riscv_vand_vx_u64m1 (ix, 0x8000000000000000ULL, vl);
  vfloat64m1_t ax = __riscv_vfabs_v_f64m1 (x, vl);
  vuint64m1_t iax = __riscv_vreinterpret_v_f64m1_u64m1 (ax);
  /* red = |x| > 1, decided on the magnitude bit pattern.  */
  vbool64_t red = __riscv_vmand_mm_b64 (
      __riscv_vmsgtu_vx_u64m1_b64 (iax, 0x3ff0000000000000ULL, vl), m, vl);
  /* z = red ? -1/x : x.  Masked-off lanes are overwritten by vmerge.  */
  vfloat64m1_t z = __riscv_vfrdiv_vf_f64m1_m (red, x, -1.0, vl);
  z = __riscv_vmerge_vvm_f64m1 (x, z, red, vl);
  /* shift0 = red ? pi/2 : 0, sign of x reinserted (matches SVE eor).  */
  vfloat64m1_t sh = __riscv_vfmerge_vfm_f64m1 (
      __riscv_vfmv_v_f_f64m1 (0.0, vl), ATAN_PI2, red, vl);
  sh = __riscv_vreinterpret_v_u64m1_f64m1 (__riscv_vxor_vv_u64m1 (
      __riscv_vreinterpret_v_f64m1_u64m1 (sh), isign, vl));
  /* t = red ? z + shift0 : z.  */
  vfloat64m1_t t = __riscv_vfadd_vv_f64m1_m (red, z, sh, vl);
  t = __riscv_vmerge_vvm_f64m1 (z, t, red, vl);

  vfloat64m1_t z2 = __riscv_vfmul_vv_f64m1_m (m, z, z, vl);
  vfloat64m1_t z4 = __riscv_vfmul_vv_f64m1_m (m, z2, z2, vl);
  vfloat64m1_t z8 = __riscv_vfmul_vv_f64m1_m (m, z4, z4, vl);
  vfloat64m1_t z16 = __riscv_vfmul_vv_f64m1_m (m, z8, z8, vl);
  vfloat64m1_t p = atan_poly_m1 (z2, z4, z8, z16, m, vl);
  vfloat64m1_t y = __riscv_vfmul_vv_f64m1_m (m, z2, p, vl);
  /* atan is odd: this only matters for -0, whose +0/-0 addition would
     otherwise round to +0.  */
  return __riscv_vfsgnj_vv_f64m1 (__riscv_vfmadd_vv_f64m1_m (m, z, y, t, vl),
                                  x, vl);
}

static inline vfloat64m2_t
atan_arm_m2 (vfloat64m2_t x, vbool32_t m, size_t vl)
{
  vuint64m2_t ix = __riscv_vreinterpret_v_f64m2_u64m2 (x);
  vuint64m2_t isign = __riscv_vand_vx_u64m2 (ix, 0x8000000000000000ULL, vl);
  vfloat64m2_t ax = __riscv_vfabs_v_f64m2 (x, vl);
  vuint64m2_t iax = __riscv_vreinterpret_v_f64m2_u64m2 (ax);
  vbool32_t red = __riscv_vmand_mm_b32 (
      __riscv_vmsgtu_vx_u64m2_b32 (iax, 0x3ff0000000000000ULL, vl), m, vl);
  vfloat64m2_t z = __riscv_vfrdiv_vf_f64m2_m (red, x, -1.0, vl);
  z = __riscv_vmerge_vvm_f64m2 (x, z, red, vl);
  vfloat64m2_t sh = __riscv_vfmerge_vfm_f64m2 (
      __riscv_vfmv_v_f_f64m2 (0.0, vl), ATAN_PI2, red, vl);
  sh = __riscv_vreinterpret_v_u64m2_f64m2 (__riscv_vxor_vv_u64m2 (
      __riscv_vreinterpret_v_f64m2_u64m2 (sh), isign, vl));
  vfloat64m2_t t = __riscv_vfadd_vv_f64m2_m (red, z, sh, vl);
  t = __riscv_vmerge_vvm_f64m2 (z, t, red, vl);

  vfloat64m2_t z2 = __riscv_vfmul_vv_f64m2_m (m, z, z, vl);
  vfloat64m2_t z4 = __riscv_vfmul_vv_f64m2_m (m, z2, z2, vl);
  vfloat64m2_t z8 = __riscv_vfmul_vv_f64m2_m (m, z4, z4, vl);
  vfloat64m2_t z16 = __riscv_vfmul_vv_f64m2_m (m, z8, z8, vl);
  vfloat64m2_t p = atan_poly_m2 (z2, z4, z8, z16, m, vl);
  vfloat64m2_t y = __riscv_vfmul_vv_f64m2_m (m, z2, p, vl);
  return __riscv_vfsgnj_vv_f64m2 (__riscv_vfmadd_vv_f64m2_m (m, z, y, t, vl),
                                  x, vl);
}

/* ---- self test harness (unary kernels) ---------------------------------- */
