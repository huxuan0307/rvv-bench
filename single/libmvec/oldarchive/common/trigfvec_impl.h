/* RVV widening adaptation of glibc flt-32 s_sinf.c / s_cosf.c.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later

   Algorithm source
   ----------------
   GNU libc sysdeps/ieee754/flt-32/{s_sinf.c,s_cosf.c,s_sincosf.h,
   s_sincosf_data.c,sincosf_poly.h} with TOINT_INTRINSICS == 0 (the RISC-V
   generic configuration).  The scalar kernels compute in double, reduce with
   one multiply-subtract against hpi_inv = 0x1.45F306DC9C883p+23 = (2/pi)*2^24
   and IMAG=0x800000 >> 24, and evaluate either the sine or the cosine
   polynomial of sinf_poly() selected by the quadrant parity.  Because
   TOINT_INTRINSICS == 0 the scalar code computes
   n = ((int32_t) (y * hpi_inv) + IMAG) >> 24, i.e. the quadrant lands in
   bits 24..31; the kernels keep the same integer expression on the widened
   double value.

   Vector structure (LMUL1 -> e64m2, LMUL2 -> e64m4 double work)
   -------------------------------------------------------------
   * |y| >= 120 (and inf/nan) cannot be range-reduced by the single-step
     multiply: those lanes take the scalar libm path, which also supplies
     Payne-Hanek reduction, NaN quieting and Inf EDOM.  If any lane is cold the
     whole register goes through the scalar dispatch, matching the expf kernel
     convention (it also means a mixed vector is exact, not approximate).
   * Every remaining lane uses the same reduction; the direct branch
     (abstop12(y) < abstop12(pi/4)) is exactly the n == 0 case of it because
     the scalar's own direct branch is the conservative |y| < 0.75 test.
   * table[1] of the scalar data is table[0] with the cosine polynomial
     negated, so the vector kernel keeps table[0] and applies the table sign as
     an exact sign flip of the cosine result.
   * sinf returns y for |y| < 2^-12 and forces a float underflow for
     subnormal y; cosf returns 1 for |y| < 2^-12.  Both are replicated,
     including the scalar's underflow-forcing narrow.

   Build recipe (performance)
   -------------------------
   The measured matrix builds these units with -DSIXMATH_COLD_DISPATCH: the
   scalar fallback then lives in a separate noinline function and the native
   entry shrinks from 259/316 instructions (baseline, 39/… stack references) to
   130/185 (18/32).  The seven-function baseline used the same cold-dispatch
   contract for exp/expf/logf/pow and plain baseline for double sin/cos; the
   float entries follow the expf/logf choice because their scalar fallback is
   also the inf/nan lane, and the float kernels are small enough that the inlined
   fallback dominates the hot path.

   Accuracy contract
   -----------------
   Experimental RNE evaluation against the MPFR512 corpus
   (script/generate_float3_data.py corpus, 100095 sinf / 100095 cosf /
   100017 powf points, x in [-32,32) plus structured pi/2-multiple and
   branch-boundary points): every sample is within 1 encoding step of the
   correctly rounded float32 result; no sample exceeds it.  The published
   scalar figure is worst-case 0.5607 ULP.  Non-RNE frm is not validated.
   No operation in the reduction is contracted away: the multiply-subtract is
   one fused op, matching the scalar's double rounding of n*pi/2. */
#define OP_(a,t) __riscv_##a##_##t
#define OP(a,t) OP_(a,t)
#define OP2_(a,t1,t2) __riscv_##a##_##t1##_##t2
#define OP2(a,t1,t2) OP2_(a,t1,t2)
#define F(a) OP(a,FT)
#define D(a) OP(a,DT)
#define U(a) OP(a,UT)
#define I(a) OP(a,IT)
#define UM(a) OP2(a,UT,BT)   /* u32 compare -> mask */
#define IM(a) OP2(a,IT,BT)   /* i64 compare -> mask */
#define MM(a) OP(a,BT)       /* mask-only reductions */
#define TRIGF_CAT_(a,b) a##b
#define TRIGF_CAT(a,b) TRIGF_CAT_(a,b)

/* Pure vector body; the caller guarantees no lane needs the scalar path. */
static __attribute__((always_inline)) inline FV TRIGF_CAT(CORE,_calc)(FV a, size_t vl)
{
  const struct sincosfvec_data *p = &sincosfvec_data;
  UV ub = FU(a);
  UV ab = U(vand_vx)(ub, UINT32_C(0x7fffffff), vl);
  /* glibc abstop12 comparisons, expressed on the whole |bits| word.  */
  BV tiny = ULT(ab, UINT32_C(0x39800000), vl);   /* |y| < 2^-12 */
#if !TRIG_COS
  BV subn = ULT(ab, UINT32_C(0x00800000), vl);   /* subnormal y */
#endif
  /* The scalar leaves this branch through early returns; zero those lanes so
     the inactive polynomial work cannot raise a spurious inexact flag. */
  FV calc = F(vfmerge_vfm)(a, 0.0f, tiny, vl);
  DV x = WIDEN(calc, vl);
  DV xt = D(vfmul_vf)(x, p->table[0].hpi_inv, vl);
  IV ni = RTZI(xt, vl);                          /* (int32_t)r : truncation */
  ni = I(vadd_vx)(ni, INT64_C(0x800000), vl);
  ni = I(vsra_vx)(ni, 24, vl);
  DV nd = I2F(ni, vl);
  /* x - (double)n * pi/2, fused exactly like the scalar's contracted form. */
  DV xr = D(vfnmsac_vf)(x, p->table[0].hpi, nd, vl);
  /* sign[n & 3] is -1 exactly when ((n + 1) & 2) != 0. */
  DV nxr = D(vfneg_v)(xr, vl);
  BV neg = IM(vmsne_vx)(I(vand_vx)(I(vadd_vx)(ni, 1, vl), 2, vl), 0, vl);
  DV xs = D(vmerge_vvm)(xr, nxr, neg, vl);
  DV x2 = D(vfmul_vv)(xr, xr, vl);
  DV x3 = D(vfmul_vv)(xs, x2, vl);
  DV x5 = D(vfmul_vv)(x3, x2, vl);
  DV s1p = D(vfmadd_vf)(x2, p->table[0].s3, D(vfmv_v_f)(p->table[0].s2, vl), vl);
  DV sinv = D(vfmadd_vv)(x5, s1p,
                         D(vfmadd_vf)(x3, p->table[0].s1, xs, vl), vl);
  DV x4 = D(vfmul_vv)(x2, x2, vl);
  DV x6 = D(vfmul_vv)(x4, x2, vl);
  DV c2p = D(vfmadd_vf)(x2, p->table[0].c4, D(vfmv_v_f)(p->table[0].c3, vl), vl);
  DV c1p = D(vfmadd_vf)(x2, p->table[0].c1, D(vfmv_v_f)(p->table[0].c0, vl), vl);
  DV cosv = D(vfmadd_vv)(x6, c2p, D(vfmadd_vf)(x4, p->table[0].c2, c1p, vl), vl);
  /* table[1] (n & 2) negates the cosine polynomial. */
  DV ncosv = D(vfneg_v)(cosv, vl);
  BV q = IM(vmsne_vx)(I(vand_vx)(ni, 2, vl), 0, vl);
  cosv = D(vmerge_vvm)(cosv, ncosv, q, vl);
  /* sinf_poly uses the sine polynomial for even n, cosf for n ^ 1. */
#if TRIG_COS
  BV cosp = IM(vmseq_vx)(I(vand_vx)(ni, 1, vl), 0, vl);   /* sinf_poly(n ^ 1) */
#else
  BV cosp = IM(vmsne_vx)(I(vand_vx)(ni, 1, vl), 0, vl);
#endif
  FV out = NARROW(D(vmerge_vvm)(sinv, cosv, cosp, vl), vl);
#if TRIG_COS
  /* cosf early-returns 1 for tiny y, without touching the underflow flags. */
  out = F(vfmerge_vfm)(out, 1.0f, tiny, vl);
#else
  /* sinf returns y for tiny y ... */
  out = F(vmerge_vvm)(out, a, tiny, vl);
  /* ... and forces math_force_eval((float)(x*x)) for subnormal y, which is
     where the scalar's UF|NX comes from.  Only subnormal lanes enter that
     square; every other lane squares an exact zero. */
  DV x0 = WIDEN(a, vl);
  DV ufin = D(vmerge_vvm)(D(vfmv_v_f)(0.0, vl), x0, subn, vl);
  FV uf = NARROW(D(vfmul_vv)(ufin, ufin, vl), vl);
  __asm__ volatile("" : : "vr"(uf) : "memory");
#endif
  return out;
}

#ifdef SIXMATH_COLD_DISPATCH
static __attribute__((noinline,noclone,riscv_vector_cc)) FV TRIGF_CAT(CORE,_cold)(FV a, size_t vl)
{
  UV ub = FU(a);
  UV ab = U(vand_vx)(ub, UINT32_C(0x7fffffff), vl);
  BV cold = UGE(ab, UINT32_C(0x42f00000), vl);
  size_t nc = MM(vcpop_m)(cold, vl);
  FV safe = a;
  if (__builtin_expect(nc != 0, 0))
    safe = F(vfmerge_vfm)(safe, 0.0f, cold, vl);
  FV out = TRIGF_CAT(CORE,_calc)(safe, vl);
  if (__builtin_expect(nc != 0, 0))
    {
      float original[vl], values[vl];
      F(vse32_v)(original, a, vl);
      F(vse32_v)(values, out, vl);
      for (size_t i = 0; i < vl; ++i)
        {
          uint32_t b;
          __builtin_memcpy(&b, &original[i], 4);
          if ((b & UINT32_C(0x7fffffff)) >= UINT32_C(0x42f00000))
            values[i] = SCALAR(original[i]);
        }
      out = F(vle32_v)(values, vl);
    }
  return out;
}

static __attribute__((always_inline)) inline FV TRIGF_CAT(CORE,_body)(FV a, size_t vl)
{
  UV ub = FU(a);
  UV ab = U(vand_vx)(ub, UINT32_C(0x7fffffff), vl);
  BV cold = UGE(ab, UINT32_C(0x42f00000), vl);
  if (__builtin_expect(MM(vfirst_m)(cold, vl) >= 0, 0))
    return TRIGF_CAT(CORE,_cold)(a, vl);
#ifdef SIXMATH_HOT_LIFETIME
  __asm__ volatile("" : "+vr"(a));
#endif
  return TRIGF_CAT(CORE,_calc)(a, vl);
}
#else
static __attribute__((always_inline)) inline FV TRIGF_CAT(CORE,_body)(FV a, size_t vl)
{
  UV ub = FU(a);
  UV ab = U(vand_vx)(ub, UINT32_C(0x7fffffff), vl);
  BV cold = UGE(ab, UINT32_C(0x42f00000), vl);
  size_t nc = MM(vcpop_m)(cold, vl);
  FV safe = a;
  if (__builtin_expect(nc != 0, 0))
    safe = F(vfmerge_vfm)(safe, 0.0f, cold, vl);
  FV out = TRIGF_CAT(CORE,_calc)(safe, vl);
  if (__builtin_expect(nc != 0, 0))
    {
      float original[vl], values[vl];
      F(vse32_v)(original, a, vl);
      F(vse32_v)(values, out, vl);
      for (size_t i = 0; i < vl; ++i)
        {
          uint32_t b;
          __builtin_memcpy(&b, &original[i], 4);
          if ((b & UINT32_C(0x7fffffff)) >= UINT32_C(0x42f00000))
            values[i] = SCALAR(original[i]);
        }
      out = F(vle32_v)(values, vl);
    }
  return out;
}
#endif

/* The entries compute the RNE result whatever frm the caller selected and
   restore the caller's frm, which is the released GLIBC_2.45 contract the ABI
   suite checks (all five legal frm values must give the same bits). */
static __attribute__((always_inline)) inline unsigned TRIGF_CAT(CORE,_frm_rne)(void)
{
  unsigned old;
  __asm__ volatile ("frrm %0" : "=r" (old));
  unsigned zero = 0;
  __asm__ volatile ("fsrm %0" : : "r" (zero) : "memory");
  return old;
}
static __attribute__((always_inline)) inline void TRIGF_CAT(CORE,_frm_restore)(unsigned v)
{
  __asm__ volatile ("fsrm %0" : : "r" (v) : "memory");
}
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl)
{
  unsigned frm = TRIGF_CAT(CORE,_frm_rne)();
  FV out = TRIGF_CAT(CORE,_body)(a, vl);
  TRIGF_CAT(CORE,_frm_restore)(frm);
  return out;
}

#include "unary_entries.inc.h"

/* Fixed-lane native ABI entry (VLEN=128 lane count: f32 m1 -> 4, m2 -> 8).
   Only the fixed lane count is defined; higher lanes of the returned register
   are unspecified, as the released GLIBC_2.45 fixed-lane entries are. */
__attribute__((noinline,noclone,riscv_vector_cc))
FV FIXED_NX(FV input)
{
  return CORE(input, FIXED_LANES);
}

#undef TRIGF_CAT_
#undef TRIGF_CAT
#undef OP_
#undef OP
#undef OP2_
#undef OP2
#undef F
#undef D
#undef U
#undef I
#undef UM
#undef IM
#undef MM
