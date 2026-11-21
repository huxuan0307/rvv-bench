/* RVV widening adaptation of glibc flt-32/e_logf.c.
   Copyright (C) 2017-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later. Input LMUL is 1/2, double work 2/4. */
#define OP_(a,t) __riscv_##a##_##t
#define OP(a,t) OP_(a,t)
#define F(a) OP(a,FT)
#define D(a) OP(a,DT)
#define U(a) OP(a,UT)
#define W(a) OP(a,WT)
#define I(a) OP(a,IT)
#define M(a) OP(a,BT)
#define SIX_CAT_(a,b) a##b
#define SIX_CAT(a,b) SIX_CAT_(a,b)
#ifdef SIXMATH_COLD_DISPATCH
static __attribute__((noinline,noclone,riscv_vector_cc)) FV SIX_CAT(CORE,_cold)(FV a, size_t vl){
#else
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl){
#endif
  UV ix=FU(a);
  BV cold=ULT(U(vsub_vx)(ix,UINT32_C(0x00800000),vl),UINT32_C(0x7f000000),vl);
  cold=M(vmnot_m)(cold,vl);size_t nc=M(vcpop_m)(cold,vl);
  FV safe=a;if(__builtin_expect(nc!=0,0))safe=F(vfmerge_vfm)(safe,1.0f,cold,vl);
  ix=FU(safe);UV tmp=U(vsub_vx)(ix,UINT32_C(0x3f330000),vl);
  UV index=U(vsll_vx)(U(vand_vx)(U(vsrl_vx)(tmp,19,vl),15,vl),4,vl);
  UV iz=U(vsub_vv)(ix,U(vand_vx)(tmp,UINT32_C(0xff800000),vl),vl);
  IV k=I(vsra_vx)(UI(tmp),23,vl);
  WV idx=WIDEN_U(index,vl);DV invc=GATHER(&logfvec_data.tab[0].invc,idx,vl);
  DV logc=GATHER(&logfvec_data.tab[0].logc,idx,vl);
  DV z=WIDEN(UF(iz),vl);DV kd=WIDEN_I(k,vl);
  DV r=D(vfmadd_vv)(z,invc,D(vfmv_v_f)(-1.0,vl),vl);
  DV y0=D(vfmadd_vf)(kd,logfvec_data.ln2,logc,vl);
  DV r2=D(vfmul_vv)(r,r,vl);
  DV out=D(vfmadd_vf)(r,logfvec_data.poly[1],D(vfmv_v_f)(logfvec_data.poly[2],vl),vl);
  out=D(vfmadd_vf)(r2,logfvec_data.poly[0],out,vl);
  out=D(vfmadd_vv)(out,r2,D(vfadd_vv)(y0,r,vl),vl);
  FV result=NARROW(out,vl);
  if(__builtin_expect(nc!=0,0)){
   float original[vl],values[vl];F(vse32_v)(original,a,vl);
   F(vse32_v)(values,result,vl);
   for(size_t i=0;i<vl;++i){uint32_t b;__builtin_memcpy(&b,&original[i],4);
    if(b-UINT32_C(0x00800000)>=UINT32_C(0x7f000000))values[i]=logf(original[i]);}
   result=F(vle32_v)(values,vl);
  }
  return result;
}

#ifdef SIXMATH_COLD_DISPATCH
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl){
  UV ix=FU(a);
  BV anycold=ULT(U(vsub_vx)(ix,UINT32_C(0x00800000),vl),UINT32_C(0x7f000000),vl);
  anycold=M(vmnot_m)(anycold,vl);
  if(__builtin_expect(M(vfirst_m)(anycold,vl)>=0,0))
    return SIX_CAT(CORE,_cold)(a,vl);
#ifdef SIXMATH_HOT_LIFETIME
  __asm__ volatile("" : "+vr"(a));
#endif
  ix=FU(a);UV tmp=U(vsub_vx)(ix,UINT32_C(0x3f330000),vl);
  UV index=U(vsll_vx)(U(vand_vx)(U(vsrl_vx)(tmp,19,vl),15,vl),4,vl);
  UV iz=U(vsub_vv)(ix,U(vand_vx)(tmp,UINT32_C(0xff800000),vl),vl);
  IV k=I(vsra_vx)(UI(tmp),23,vl);
  WV idx=WIDEN_U(index,vl);DV invc=GATHER(&logfvec_data.tab[0].invc,idx,vl);
  DV logc=GATHER(&logfvec_data.tab[0].logc,idx,vl);
  DV z=WIDEN(UF(iz),vl);DV kd=WIDEN_I(k,vl);
  DV r=D(vfmadd_vv)(z,invc,D(vfmv_v_f)(-1.0,vl),vl);
  DV y0=D(vfmadd_vf)(kd,logfvec_data.ln2,logc,vl);
  DV r2=D(vfmul_vv)(r,r,vl);
  DV out=D(vfmadd_vf)(r,logfvec_data.poly[1],D(vfmv_v_f)(logfvec_data.poly[2],vl),vl);
  out=D(vfmadd_vf)(r2,logfvec_data.poly[0],out,vl);
  out=D(vfmadd_vv)(out,r2,D(vfadd_vv)(y0,r,vl),vl);
  FV result=NARROW(out,vl);
  return result;
}
#endif
#include "../common/unary_entries.inc.h"
#undef SIX_CAT_
#undef SIX_CAT
#undef OP_
#undef OP
#undef F
#undef D
#undef U
#undef W
#undef I
#undef M
