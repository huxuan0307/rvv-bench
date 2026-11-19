/* ARM SVE-derived RVV candidate kernel: double-precision atan2.

   Algorithm and coefficients are taken from
     glibc/sysdeps/aarch64/fpu/sve/atan2.c
   (Copyright (C) 2023-2026 Free Software Foundation, Inc., LGPL-2.1-or-later).
   The file is a sign/quadrant reduction around the same degree-19 odd
   polynomial P used by atan:
     z     = (|y| > |x|) ? -|x|/|y| : |y|/|x|
     shift = 1 * (+-pi/2)   when |y| > |x|
           = 2 * (+-pi/2)   when |y| <= |x| and x < 0
           = 0              when |y| <= |x| and x >= 0
     r     = shift + z + z^3 * P(z^2),  result = r xor sign(x) xor sign(y)
   All finite non-zero inputs are handled by this path.  The SVE code calls the
   scalar atan2 for lanes where x or y is 0, inf or NaN; this port replaces
   that fallback with a pure vector mask/merge sequence (no scalar libm call).

   RVV 1.0 port notes
   ------------------
   * Every exception-raising FP instruction is predicated by the caller mask
     `m`; inactive lanes never signal.
   * `origin` of the shift is built with the same integer bit tricks as SVE
     (svlsr/svorr on the sign bits); the final sign application is an integer
     xor on the bit pattern.
   * pred (|y| > |x|) uses an unsigned integer comparison of the magnitude bit
     patterns.  For finite inputs this is identical to SVE svcmpgt; it also
     means quiet NaNs do not raise INVALID during the reduction (they are still
     quieted by the arithmetic below).
   * Special values (zero, infinity, NaN, and all quadrant combinations) are
     resolved by vector compares/merges: magnitude of the result is selected
     from {0, pi/4, pi/2, 3pi/4, pi} and its sign is taken from y with vfsgnj,
     which reproduces glibc's scalar atan2 exactly (including signed zeros).
     NaN lanes are overridden with the canonical qNaN after the sign step.
   * The generic path still runs for special lanes (like the SVE code, which
     computes it before calling the scalar fallback), so e.g. atan2(0,0) can
     raise INVALID through the 0/0 division; this is not observable in the
     returned value and is noted in the task report.

   Interface:
     static inline vfloat64m1_t atan2_arm_m1 (vfloat64m1_t y, vfloat64m1_t x,
                                              vbool64_t m, size_t vl);
     static inline vfloat64m2_t atan2_arm_m2 (vfloat64m2_t y, vfloat64m2_t x,
                                              vbool32_t m, size_t vl);
*/

#include <riscv_vector.h>
#include <stddef.h>
#include <stdint.h>

#define A2_PI2 0x1.921fb54442d18p+0
#define A2_PI4 0x1.921fb54442d18p-1
#define A2_3PI4 0x1.2d97c7f3321d2p+1
#define A2_PI 0x1.921fb54442d18p+1
/* 2 * asuint64(inf) - 1: (i<<1)-1 >= this  <=>  i is 0, inf or NaN.  */
#define A2_ZEROINFF 0xffdfffffffffffffULL
#define A2_INFBITS 0x7ff0000000000000ULL

static const double a2_c[20]
    = { -0x1.555555555552ap-2,  0x1.9999999995aebp-3,  -0x1.24924923923f6p-3,
        0x1.c71c7184288a2p-4,   -0x1.745d11fb3d32bp-4, 0x1.3b136a18051b9p-4,
        -0x1.110e6d985f496p-4,  0x1.e1bcf7f08801dp-5,  -0x1.ae644e28058c3p-5,
        0x1.82eeb1fed85c6p-5,   -0x1.59d7f901566cbp-5, 0x1.2c982855ab069p-5,
        -0x1.eb49592998177p-6,  0x1.69d8b396e3d38p-6,  -0x1.ca980345c4204p-7,
        0x1.dc050eafde0b3p-8,   -0x1.7ea70755b8eccp-9, 0x1.ba3da3de903e8p-11,
        -0x1.44a4b059b6f67p-13, 0x1.c4a45029e5a91p-17 };

static inline vfloat64m1_t
a2_poly_m1 (vfloat64m1_t z2, vfloat64m1_t z4, vfloat64m1_t z8,
            vfloat64m1_t z16, vbool64_t m, size_t vl)
{
  vfloat64m1_t p01 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[1], __riscv_vfmv_v_f_f64m1 (a2_c[0], vl), vl);
  vfloat64m1_t p23 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[3], __riscv_vfmv_v_f_f64m1 (a2_c[2], vl), vl);
  vfloat64m1_t p45 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[5], __riscv_vfmv_v_f_f64m1 (a2_c[4], vl), vl);
  vfloat64m1_t p67 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[7], __riscv_vfmv_v_f_f64m1 (a2_c[6], vl), vl);
  vfloat64m1_t p03 = __riscv_vfmadd_vv_f64m1_m (m, z4, p23, p01, vl);
  vfloat64m1_t p47 = __riscv_vfmadd_vv_f64m1_m (m, z4, p67, p45, vl);
  vfloat64m1_t p07 = __riscv_vfmadd_vv_f64m1_m (m, z8, p47, p03, vl);
  vfloat64m1_t p89 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[9], __riscv_vfmv_v_f_f64m1 (a2_c[8], vl), vl);
  vfloat64m1_t p1011 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[11], __riscv_vfmv_v_f_f64m1 (a2_c[10], vl), vl);
  vfloat64m1_t p811 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1011, p89, vl);
  vfloat64m1_t p1213 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[13], __riscv_vfmv_v_f_f64m1 (a2_c[12], vl), vl);
  vfloat64m1_t p1415 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[15], __riscv_vfmv_v_f_f64m1 (a2_c[14], vl), vl);
  vfloat64m1_t p1215 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1415, p1213, vl);
  vfloat64m1_t p1617 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[17], __riscv_vfmv_v_f_f64m1 (a2_c[16], vl), vl);
  vfloat64m1_t p1819 = __riscv_vfmadd_vf_f64m1_m (
      m, z2, a2_c[19], __riscv_vfmv_v_f_f64m1 (a2_c[18], vl), vl);
  vfloat64m1_t p1619 = __riscv_vfmadd_vv_f64m1_m (m, z4, p1819, p1617, vl);
  vfloat64m1_t p815 = __riscv_vfmadd_vv_f64m1_m (m, z8, p1215, p811, vl);
  vfloat64m1_t p819 = __riscv_vfmadd_vv_f64m1_m (m, z16, p1619, p815, vl);
  return __riscv_vfmadd_vv_f64m1_m (m, z16, p819, p07, vl);
}

static inline vfloat64m2_t
a2_poly_m2 (vfloat64m2_t z2, vfloat64m2_t z4, vfloat64m2_t z8,
            vfloat64m2_t z16, vbool32_t m, size_t vl)
{
  vfloat64m2_t p01 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[1], __riscv_vfmv_v_f_f64m2 (a2_c[0], vl), vl);
  vfloat64m2_t p23 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[3], __riscv_vfmv_v_f_f64m2 (a2_c[2], vl), vl);
  vfloat64m2_t p45 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[5], __riscv_vfmv_v_f_f64m2 (a2_c[4], vl), vl);
  vfloat64m2_t p67 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[7], __riscv_vfmv_v_f_f64m2 (a2_c[6], vl), vl);
  vfloat64m2_t p03 = __riscv_vfmadd_vv_f64m2_m (m, z4, p23, p01, vl);
  vfloat64m2_t p47 = __riscv_vfmadd_vv_f64m2_m (m, z4, p67, p45, vl);
  vfloat64m2_t p07 = __riscv_vfmadd_vv_f64m2_m (m, z8, p47, p03, vl);
  vfloat64m2_t p89 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[9], __riscv_vfmv_v_f_f64m2 (a2_c[8], vl), vl);
  vfloat64m2_t p1011 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[11], __riscv_vfmv_v_f_f64m2 (a2_c[10], vl), vl);
  vfloat64m2_t p811 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1011, p89, vl);
  vfloat64m2_t p1213 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[13], __riscv_vfmv_v_f_f64m2 (a2_c[12], vl), vl);
  vfloat64m2_t p1415 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[15], __riscv_vfmv_v_f_f64m2 (a2_c[14], vl), vl);
  vfloat64m2_t p1215 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1415, p1213, vl);
  vfloat64m2_t p1617 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[17], __riscv_vfmv_v_f_f64m2 (a2_c[16], vl), vl);
  vfloat64m2_t p1819 = __riscv_vfmadd_vf_f64m2_m (
      m, z2, a2_c[19], __riscv_vfmv_v_f_f64m2 (a2_c[18], vl), vl);
  vfloat64m2_t p1619 = __riscv_vfmadd_vv_f64m2_m (m, z4, p1819, p1617, vl);
  vfloat64m2_t p815 = __riscv_vfmadd_vv_f64m2_m (m, z8, p1215, p811, vl);
  vfloat64m2_t p819 = __riscv_vfmadd_vv_f64m2_m (m, z16, p1619, p815, vl);
  return __riscv_vfmadd_vv_f64m2_m (m, z16, p819, p07, vl);
}

static inline vfloat64m1_t
atan2_arm_m1 (vfloat64m1_t y, vfloat64m1_t x, vbool64_t m, size_t vl)
{
  vuint64m1_t ix = __riscv_vreinterpret_v_f64m1_u64m1 (x);
  vuint64m1_t iy = __riscv_vreinterpret_v_f64m1_u64m1 (y);
  vfloat64m1_t ax = __riscv_vfabs_v_f64m1 (x, vl);
  vfloat64m1_t ay = __riscv_vfabs_v_f64m1 (y, vl);
  vuint64m1_t iax = __riscv_vreinterpret_v_f64m1_u64m1 (ax);
  vuint64m1_t iay = __riscv_vreinterpret_v_f64m1_u64m1 (ay);
  vuint64m1_t sx = __riscv_vxor_vv_u64m1 (ix, iax, vl);
  vuint64m1_t sy = __riscv_vxor_vv_u64m1 (iy, iay, vl);
  vuint64m1_t sxy = __riscv_vxor_vv_u64m1 (sx, sy, vl);

  /* zero / infinity / NaN lanes (SVE zeroinfnan bit trick).  */
  vuint64m1_t tx
      = __riscv_vsub_vx_u64m1 (__riscv_vsll_vx_u64m1 (ix, 1, vl), 1, vl);
  vuint64m1_t ty
      = __riscv_vsub_vx_u64m1 (__riscv_vsll_vx_u64m1 (iy, 1, vl), 1, vl);
  vbool64_t cx = __riscv_vmand_mm_b64 (
      __riscv_vmsgeu_vx_u64m1_b64 (tx, A2_ZEROINFF, vl), m, vl);
  vbool64_t cy = __riscv_vmand_mm_b64 (
      __riscv_vmsgeu_vx_u64m1_b64 (ty, A2_ZEROINFF, vl), m, vl);
  vbool64_t cxy = __riscv_vmor_mm_b64 (cx, cy, vl);

  /* pred = |y| > |x| on magnitude bit patterns.  */
  vbool64_t pred = __riscv_vmand_mm_b64 (
      __riscv_vmsltu_vv_u64m1_b64 (iax, iay, vl), m, vl);
  vfloat64m1_t nax = __riscv_vfsgnjn_vv_f64m1 (ax, ax, vl);
  vfloat64m1_t num = __riscv_vmerge_vvm_f64m1 (ay, nax, pred, vl);
  vfloat64m1_t den = __riscv_vmerge_vvm_f64m1 (ax, ay, pred, vl);
  vfloat64m1_t z = __riscv_vfdiv_vv_f64m1_m (m, num, den, vl);

  /* shift = (pred ? 1.0 : (x<0 ? 2.0 : 0)) * (+-pi/2).  */
  vfloat64m1_t sh
      = __riscv_vreinterpret_v_u64m1_f64m1 (__riscv_vsrl_vx_u64m1 (sx, 1, vl));
  sh = __riscv_vfmerge_vfm_f64m1 (sh, 1.0, pred, vl);
  vfloat64m1_t pi2v = __riscv_vfmv_v_f_f64m1 (A2_PI2, vl);
  vfloat64m1_t shm = __riscv_vreinterpret_v_u64m1_f64m1 (__riscv_vor_vv_u64m1 (
      sx, __riscv_vreinterpret_v_f64m1_u64m1 (pi2v), vl));
  /* zs = z + sh * shm  (SVE svmla(z, shift, shift_mul)).  */
  vfloat64m1_t zs = __riscv_vfmacc_vv_f64m1_m (m, z, sh, shm, vl);

  vfloat64m1_t z2 = __riscv_vfmul_vv_f64m1_m (m, z, z, vl);
  vfloat64m1_t z3 = __riscv_vfmul_vv_f64m1_m (m, z2, z, vl);
  vfloat64m1_t z4 = __riscv_vfmul_vv_f64m1_m (m, z2, z2, vl);
  vfloat64m1_t z8 = __riscv_vfmul_vv_f64m1_m (m, z4, z4, vl);
  vfloat64m1_t z16 = __riscv_vfmul_vv_f64m1_m (m, z8, z8, vl);
  vfloat64m1_t p = a2_poly_m1 (z2, z4, z8, z16, m, vl);
  vfloat64m1_t r = __riscv_vfmadd_vv_f64m1_m (m, z3, p, zs, vl);
  vfloat64m1_t ret = __riscv_vreinterpret_v_u64m1_f64m1 (
      __riscv_vxor_vv_u64m1 (__riscv_vreinterpret_v_f64m1_u64m1 (r), sxy, vl));

  /* ---- pure vector special-case resolution ---- */
  vbool64_t xinf = __riscv_vmand_mm_b64 (
      __riscv_vmseq_vx_u64m1_b64 (iax, A2_INFBITS, vl), m, vl);
  vbool64_t yinf = __riscv_vmand_mm_b64 (
      __riscv_vmseq_vx_u64m1_b64 (iay, A2_INFBITS, vl), m, vl);
  vbool64_t xzero
      = __riscv_vmand_mm_b64 (__riscv_vmseq_vx_u64m1_b64 (iax, 0, vl), m, vl);
  vbool64_t yzero
      = __riscv_vmand_mm_b64 (__riscv_vmseq_vx_u64m1_b64 (iay, 0, vl), m, vl);
  vbool64_t xnan = __riscv_vmand_mm_b64 (
      __riscv_vmsgtu_vx_u64m1_b64 (iax, A2_INFBITS, vl), m, vl);
  vbool64_t ynan = __riscv_vmand_mm_b64 (
      __riscv_vmsgtu_vx_u64m1_b64 (iay, A2_INFBITS, vl), m, vl);
  vbool64_t xneg
      = __riscv_vmsne_vx_u64m1_b64 (__riscv_vsrl_vx_u64m1 (ix, 63, vl), 0, vl);
  xneg = __riscv_vmand_mm_b64 (xneg, m, vl);

  vfloat64m1_t pioz = __riscv_vfmerge_vfm_f64m1 (
      __riscv_vfmv_v_f_f64m1 (0.0, vl), A2_PI, xneg, vl);
  vfloat64m1_t mag = pi2v;
  mag = __riscv_vmerge_vvm_f64m1 (mag, pioz, xinf, vl);
  vfloat64m1_t yfm = __riscv_vfmerge_vfm_f64m1 (
      __riscv_vfmv_v_f_f64m1 (A2_PI4, vl), A2_3PI4, xneg, vl);
  yfm = __riscv_vmerge_vvm_f64m1 (pi2v, yfm, xinf, vl);
  mag = __riscv_vmerge_vvm_f64m1 (mag, yfm, yinf, vl);
  mag = __riscv_vmerge_vvm_f64m1 (mag, pioz, yzero, vl);
  vfloat64m1_t sres = __riscv_vfsgnj_vv_f64m1 (mag, y, vl);
  vbool64_t nanm = __riscv_vmor_mm_b64 (xnan, ynan, vl);
  sres = __riscv_vmerge_vvm_f64m1 (
      sres, __riscv_vfmv_v_f_f64m1 (__builtin_nan (""), vl), nanm, vl);
  return __riscv_vmerge_vvm_f64m1 (ret, sres, cxy, vl);
}

static inline vfloat64m2_t
atan2_arm_m2 (vfloat64m2_t y, vfloat64m2_t x, vbool32_t m, size_t vl)
{
  vuint64m2_t ix = __riscv_vreinterpret_v_f64m2_u64m2 (x);
  vuint64m2_t iy = __riscv_vreinterpret_v_f64m2_u64m2 (y);
  vfloat64m2_t ax = __riscv_vfabs_v_f64m2 (x, vl);
  vfloat64m2_t ay = __riscv_vfabs_v_f64m2 (y, vl);
  vuint64m2_t iax = __riscv_vreinterpret_v_f64m2_u64m2 (ax);
  vuint64m2_t iay = __riscv_vreinterpret_v_f64m2_u64m2 (ay);
  vuint64m2_t sx = __riscv_vxor_vv_u64m2 (ix, iax, vl);
  vuint64m2_t sy = __riscv_vxor_vv_u64m2 (iy, iay, vl);
  vuint64m2_t sxy = __riscv_vxor_vv_u64m2 (sx, sy, vl);

  vuint64m2_t tx
      = __riscv_vsub_vx_u64m2 (__riscv_vsll_vx_u64m2 (ix, 1, vl), 1, vl);
  vuint64m2_t ty
      = __riscv_vsub_vx_u64m2 (__riscv_vsll_vx_u64m2 (iy, 1, vl), 1, vl);
  vbool32_t cx = __riscv_vmand_mm_b32 (
      __riscv_vmsgeu_vx_u64m2_b32 (tx, A2_ZEROINFF, vl), m, vl);
  vbool32_t cy = __riscv_vmand_mm_b32 (
      __riscv_vmsgeu_vx_u64m2_b32 (ty, A2_ZEROINFF, vl), m, vl);
  vbool32_t cxy = __riscv_vmor_mm_b32 (cx, cy, vl);

  vbool32_t pred = __riscv_vmand_mm_b32 (
      __riscv_vmsltu_vv_u64m2_b32 (iax, iay, vl), m, vl);
  vfloat64m2_t nax = __riscv_vfsgnjn_vv_f64m2 (ax, ax, vl);
  vfloat64m2_t num = __riscv_vmerge_vvm_f64m2 (ay, nax, pred, vl);
  vfloat64m2_t den = __riscv_vmerge_vvm_f64m2 (ax, ay, pred, vl);
  vfloat64m2_t z = __riscv_vfdiv_vv_f64m2_m (m, num, den, vl);

  vfloat64m2_t sh
      = __riscv_vreinterpret_v_u64m2_f64m2 (__riscv_vsrl_vx_u64m2 (sx, 1, vl));
  sh = __riscv_vfmerge_vfm_f64m2 (sh, 1.0, pred, vl);
  vfloat64m2_t pi2v = __riscv_vfmv_v_f_f64m2 (A2_PI2, vl);
  vfloat64m2_t shm = __riscv_vreinterpret_v_u64m2_f64m2 (__riscv_vor_vv_u64m2 (
      sx, __riscv_vreinterpret_v_f64m2_u64m2 (pi2v), vl));
  vfloat64m2_t zs = __riscv_vfmacc_vv_f64m2_m (m, z, sh, shm, vl);

  vfloat64m2_t z2 = __riscv_vfmul_vv_f64m2_m (m, z, z, vl);
  vfloat64m2_t z3 = __riscv_vfmul_vv_f64m2_m (m, z2, z, vl);
  vfloat64m2_t z4 = __riscv_vfmul_vv_f64m2_m (m, z2, z2, vl);
  vfloat64m2_t z8 = __riscv_vfmul_vv_f64m2_m (m, z4, z4, vl);
  vfloat64m2_t z16 = __riscv_vfmul_vv_f64m2_m (m, z8, z8, vl);
  vfloat64m2_t p = a2_poly_m2 (z2, z4, z8, z16, m, vl);
  vfloat64m2_t r = __riscv_vfmadd_vv_f64m2_m (m, z3, p, zs, vl);
  vfloat64m2_t ret = __riscv_vreinterpret_v_u64m2_f64m2 (
      __riscv_vxor_vv_u64m2 (__riscv_vreinterpret_v_f64m2_u64m2 (r), sxy, vl));

  vbool32_t xinf = __riscv_vmand_mm_b32 (
      __riscv_vmseq_vx_u64m2_b32 (iax, A2_INFBITS, vl), m, vl);
  vbool32_t yinf = __riscv_vmand_mm_b32 (
      __riscv_vmseq_vx_u64m2_b32 (iay, A2_INFBITS, vl), m, vl);
  vbool32_t xzero
      = __riscv_vmand_mm_b32 (__riscv_vmseq_vx_u64m2_b32 (iax, 0, vl), m, vl);
  vbool32_t yzero
      = __riscv_vmand_mm_b32 (__riscv_vmseq_vx_u64m2_b32 (iay, 0, vl), m, vl);
  vbool32_t xnan = __riscv_vmand_mm_b32 (
      __riscv_vmsgtu_vx_u64m2_b32 (iax, A2_INFBITS, vl), m, vl);
  vbool32_t ynan = __riscv_vmand_mm_b32 (
      __riscv_vmsgtu_vx_u64m2_b32 (iay, A2_INFBITS, vl), m, vl);
  vbool32_t xneg
      = __riscv_vmsne_vx_u64m2_b32 (__riscv_vsrl_vx_u64m2 (ix, 63, vl), 0, vl);
  xneg = __riscv_vmand_mm_b32 (xneg, m, vl);

  vfloat64m2_t pioz = __riscv_vfmerge_vfm_f64m2 (
      __riscv_vfmv_v_f_f64m2 (0.0, vl), A2_PI, xneg, vl);
  vfloat64m2_t mag = pi2v;
  mag = __riscv_vmerge_vvm_f64m2 (mag, pioz, xinf, vl);
  vfloat64m2_t yfm = __riscv_vfmerge_vfm_f64m2 (
      __riscv_vfmv_v_f_f64m2 (A2_PI4, vl), A2_3PI4, xneg, vl);
  yfm = __riscv_vmerge_vvm_f64m2 (pi2v, yfm, xinf, vl);
  mag = __riscv_vmerge_vvm_f64m2 (mag, yfm, yinf, vl);
  mag = __riscv_vmerge_vvm_f64m2 (mag, pioz, yzero, vl);
  vfloat64m2_t sres = __riscv_vfsgnj_vv_f64m2 (mag, y, vl);
  vbool32_t nanm = __riscv_vmor_mm_b32 (xnan, ynan, vl);
  sres = __riscv_vmerge_vvm_f64m2 (
      sres, __riscv_vfmv_v_f_f64m2 (__builtin_nan (""), vl), nanm, vl);
  return __riscv_vmerge_vvm_f64m2 (ret, sres, cxy, vl);
}

/* ---- self test harness (binary kernel atan2) ---------------------------- */
