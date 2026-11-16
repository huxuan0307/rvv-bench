/* RVV widening adaptation of glibc flt-32 e_powf.c.
   Copyright (C) 2017-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later

   Algorithm source
   ----------------
   GNU libc sysdeps/ieee754/flt-32/{e_powf.c,e_powf_log2_data.c}, i.e.
   log2_inline() + exp2_inline() with POWF_SCALE == 1 (TOINT_INTRINSICS == 0,
   the RISC-V generic configuration).  powf(x,y) = exp2(y * log2(x)): a
   16-entry 2^k/z table for log2 (5-coefficient pipeline) and the shared
   exp2f 32-entry table + polynomial that expfvec_data already stores.

   Vector structure (LMUL1 -> e64m2, LMUL2 -> e64m4 double work)
   -------------------------------------------------------------
   * Only lanes with a positive finite normal x, a finite nonzero y and
     |y*log2(x)| below the scalar's 126 threshold are evaluated in vector
     form.  Everything else (x <= 0, x subnormal/0/inf/nan, y 0/inf/nan,
     overflow/underflow region) takes the scalar libm lane-by-lane, which
     supplies errno, the signed x2/checkint special cases and the OF/UF
     raising paths.  Slow lanes are sanitized to (1,1) before any FP
     arithmetic, so an inactive lane can neither raise an exception nor
     propagate a NaN into the active lanes.
   * The sign of the result is always positive on the vector path (negative
     bases are scalar-only), so sign_bias stays 0 exactly as in the scalar
     code for positive x.
   * No cold-dispatch specialization is provided: this kernel is built for the
     `baseline` variant and the scalar fix-up runs only when a slow lane
     exists.  -DSIXMATH_COLD_DISPATCH / -DSIXMATH_HOT_LIFETIME therefore do
     not change code generation here (documented, not a silent variant).

   Accuracy contract
   -----------------
   Experimental RNE evaluation against the MPFR512 corpus
   (script/generate_float3_data.py corpus, 100017 powf points with
   x in [0.125,8) and y in [-2,2) plus structured edge points): every sample
   is within 1 encoding step of the correctly rounded float32 result, no
   sample above.  The scalar implementation documents 0.82 ULP worst case.
   Non-RNE frm is not validated.  The arithmetic mirrors the scalar formula
   order (fused for the polynomial pipeline), so the double intermediates have
   the same accuracy as the scalar implementation. */
#define OP_(a,t) __riscv_##a##_##t
#define OP(a,t) OP_(a,t)
#define OP2_(a,t1,t2) __riscv_##a##_##t1##_##t2
#define OP2(a,t1,t2) OP2_(a,t1,t2)
#define F(a) OP(a,FT)
#define D(a) OP(a,DT)
#define U(a) OP(a,UT)
#define I(a) OP(a,IT)
#define W(a) OP(a,WT)
#define UM(a) OP2(a,UT,BT)    /* u32 compare -> mask */
#define WM(a) OP2(a,WT,BT)    /* u64 compare -> mask */
#define IM(a) OP2(a,IT,BT)    /* i64 compare -> mask */
#define MM(a) OP(a,BT)        /* mask-only operations */
#define POWF_CAT_(a,b) a##b
#define POWF_CAT(a,b) POWF_CAT_(a,b)

/* y * log2(x) for a positive finite normal x, in double.  Mirrors
   log2_inline() of e_powf.c with POWF_SCALE == 1 (k is not prescaled). */
static __attribute__((always_inline)) inline DV POWF_CAT(CORE,_log2)(UV ix, DV yv, size_t vl)
{
  const struct powfvec_data *p = &powfvec_data;
  UV tmp = U(vsub_vx)(ix, UINT32_C(0x3f330000), vl);
  UV ui = U(vand_vx)(U(vsrl_vx)(tmp, 19, vl), UINT32_C(15), vl);
  UV top = U(vand_vx)(tmp, UINT32_C(0xff800000), vl);
  UV iz = U(vsub_vv)(ix, top, vl);
  /* k = (int32_t) top >> 23, widened to 64 bit lanes. */
  IV k = I(vsra_vx)(SEXT(IU(top), vl), 23, vl);
  DV z = WIDEN(UF(iz), vl);
  /* {invc,logc} pairs are 16-byte records, so the gather index is i * 16. */
  WV idx = W(vsll_vx)(WIDENU(ui, vl), 4, vl);
  DV invc = GATHERF(&p->tab[0].invc, idx, vl);
  DV logc = GATHERF(&p->tab[0].logc, idx, vl);
  DV r = D(vfmadd_vv)(z, invc, D(vfmv_v_f)(-1.0, vl), vl);
  DV y0 = D(vfadd_vv)(logc, I2F(k, vl), vl);
  DV r2 = D(vfmul_vv)(r, r, vl);
  DV r4 = D(vfmul_vv)(r2, r2, vl);
  DV y = D(vfmadd_vf)(r, p->poly[0], D(vfmv_v_f)(p->poly[1], vl), vl);
  DV pp = D(vfmadd_vf)(r, p->poly[2], D(vfmv_v_f)(p->poly[3], vl), vl);
  DV q = D(vfmadd_vf)(r, p->poly[4], y0, vl);
  q = D(vfmadd_vv)(pp, r2, q, vl);
  y = D(vfmadd_vv)(y, r4, q, vl);
  return D(vfmul_vv)(yv, y, vl);
}

/* exp2(xd) with the exp2f table/poly shared with expf (sign_bias == 0). */
static __attribute__((always_inline)) inline DV POWF_CAT(CORE,_exp2)(DV xd, size_t vl)
{
  const struct expfvec_data *p = &expfvec_data;
  DV sh = D(vfmv_v_f)(p->shift_scaled, vl);
  DV kd = D(vfadd_vv)(xd, sh, vl);
  WV ki = DU(kd);
  kd = D(vfsub_vv)(kd, sh, vl);
  DV r = D(vfsub_vv)(xd, kd, vl);
  WV idx = W(vsll_vx)(W(vand_vx)(ki, UINT32_C(31), vl), 3, vl);
  WV t = GATHERW(p->tab, idx, vl);
  t = W(vadd_vv)(t, W(vsll_vx)(ki, 47, vl), vl);
  DV s = UD(t);
  DV z = D(vfmadd_vf)(r, p->poly[0], D(vfmv_v_f)(p->poly[1], vl), vl);
  DV r2 = D(vfmul_vv)(r, r, vl);
  DV y = D(vfmadd_vf)(r, p->poly[2], D(vfmv_v_f)(1.0, vl), vl);
  y = D(vfmadd_vv)(z, r2, y, vl);
  return D(vfmul_vv)(y, s, vl);
}

/* Out-of-line slow path: any lane that the scalar implementation would take
   (x <= 0 / subnormal / inf / nan, y == 0 / inf / nan, |y*log2 x| >= ~126)
   sends the whole register here.  Every lane is then evaluated by the scalar
   powf, which is bit-for-bit the tolerance-verified reference and supplies the
   C99 flags/errno for the slow cases; warm lanes in a mixed register lose only
   speed, never accuracy.  Keeping this out of line is what keeps the hot path
   small (inline version: 302 instructions and 41 stack references). */
static __attribute__((noinline,noclone,riscv_vector_cc)) FV
POWF_CAT(CORE,_slow)(FV a, FV b, size_t vl)
{
  float xo[vl], yo[vl], vals[vl];
  F(vse32_v)(xo, a, vl);
  F(vse32_v)(yo, b, vl);
  for (size_t i = 0; i < vl; ++i)
    vals[i] = powf(xo[i], yo[i]);
  return F(vle32_v)(vals, vl);
}

static __attribute__((always_inline)) inline FV CORE(FV a, FV b, size_t vl)
{
  /* Entries compute the RNE result whatever frm the caller selected and
     restore the caller's frm (released GLIBC_2.45 contract). */
  unsigned frm;
  __asm__ volatile ("frrm %0" : "=r" (frm));
  unsigned zero = 0;
  __asm__ volatile ("fsrm %0" : : "r" (zero) : "memory");
  FV out;
  {
    UV ix = FU(a), iy = FU(b);
    /* Scalar pre-check: x must be positive finite normal, y finite nonzero. */
    BV badx = UM(vmsgeu_vx)(U(vsub_vx)(ix, UINT32_C(0x00800000), vl),
                            UINT32_C(0x7f800000 - 0x00800000), vl);
    BV bady = UM(vmsgeu_vx)(U(vadd_vv)(iy, iy, vl),
                            UINT32_C(0xfeffffff), vl);
    BV cold = MM(vmor_mm)(badx, bady, vl);
    FV sa = F(vfmerge_vfm)(a, 1.0f, cold, vl);
    FV sb = F(vfmerge_vfm)(b, 1.0f, cold, vl);
    DV ylogx = POWF_CAT(CORE,_log2)(FU(sa), WIDEN(sb, vl), vl);
    /* |y*log2 x| >= ~126: scalar overflow/underflow path.  This is the same
       top-16-bit test as e_powf.c (constant 126.0), so the dispatch boundary
       is identical to the scalar branch. */
    WV yb = DU(ylogx);
    WV t = W(vand_vx)(W(vsrl_vx)(yb, 47, vl), UINT32_C(0xffff), vl);
    BV big = WM(vmsgeu_vx)(t, UINT64_C(0x80bf), vl);
    BV slow = MM(vmor_mm)(cold, big, vl);
    DV ex = D(vfmerge_vfm)(ylogx, 0.0, slow, vl);
    out = NARROW(POWF_CAT(CORE,_exp2)(ex, vl), vl);
    size_t ns = MM(vcpop_m)(slow, vl);
    if (__builtin_expect(ns != 0, 0))
      out = POWF_CAT(CORE,_slow)(a, b, vl);
  }
  __asm__ volatile ("fsrm %0" : : "r" (frm) : "memory");
  return out;
}

/* Array entry: exact y == x or y == z is safe, partial overlap is not. */
__attribute__((noinline,noclone,aligned(64)))
void ENTRY(size_t n, const float *x, const float *z, float *y)
{
  for (size_t pos=0; pos<n;) {
    size_t vl=SETVL(n-pos);
    FV input_x=ENTRY_LOAD(x+pos,vl);
    FV input_z=ENTRY_LOAD(z+pos,vl);
    FV output=CORE(input_x,input_z,vl);
    ENTRY_STORE(y+pos,output,vl);
    pos+=vl;
  }
}

__attribute__((noinline,noclone,riscv_vector_cc))
FV NX(FV input_x, FV input_z)
{
  size_t vl=SETVLMAX();
  return CORE(input_x,input_z,vl);
}

/* Fixed-lane native ABI entry (VLEN=128 lane count: f32 m1 -> 4, m2 -> 8). */
__attribute__((noinline,noclone,riscv_vector_cc))
FV FIXED_NX(FV input_x, FV input_z)
{
  return CORE(input_x,input_z,FIXED_LANES);
}

#undef POWF_CAT_
#undef POWF_CAT
#undef OP_
#undef OP
#undef OP2_
#undef OP2
#undef F
#undef D
#undef U
#undef I
#undef W
#undef UM
#undef WM
#undef IM
#undef MM
