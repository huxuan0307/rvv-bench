/* RVV widening adaptation of glibc flt-32/e_expf.c.
   Copyright (C) 2017-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later. Input LMUL is 1/2, double work 2/4. */
#define OP_(a,t) __riscv_##a##_##t
#define OP(a,t) OP_(a,t)
#define F(a) OP(a,FT)
#define D(a) OP(a,DT)
#define U(a) OP(a,UT)
#define W(a) OP(a,WT)
#define M(a) OP(a,BT)
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl){
  UV bits=FU(a);
  UV ab=U(vand_vx)(bits,UINT32_C(0x7fffffff),vl);
  BV cold=UGE(ab,UINT32_C(0x42b00000),vl);size_t nc=M(vcpop_m)(cold,vl);
  FV safe=a;if(__builtin_expect(nc!=0,0))safe=F(vfmerge_vfm)(safe,0.0f,cold,vl);
  DV xd=WIDEN(safe,vl);
  /* Match the installed scalar's fmadd/fmsub contraction, not a separate
     rounded z product: this matters at reduction half-integer boundaries. */
  DV kd=D(vfmadd_vf)(xd,expfvec_data.invln2_scaled,D(vfmv_v_f)(expfvec_data.shift,vl),vl);WV ki=DU(kd);
  kd=D(vfsub_vf)(kd,expfvec_data.shift,vl);
  DV r=D(vfmsub_vf)(xd,expfvec_data.invln2_scaled,kd,vl);
  WV idx=W(vsll_vx)(W(vand_vx)(ki,31,vl),3,vl);
  WV t=GATHER(expfvec_data.tab,idx,vl);
  t=W(vadd_vv)(t,W(vsll_vx)(ki,47,vl),vl);DV s=UD(t);
  DV q=D(vfmadd_vf)(r,expfvec_data.poly_scaled[0],D(vfmv_v_f)(expfvec_data.poly_scaled[1],vl),vl);
  DV r2=D(vfmul_vv)(r,r,vl);
  DV yy=D(vfmadd_vf)(r,expfvec_data.poly_scaled[2],D(vfmv_v_f)(1.0,vl),vl);
  yy=D(vfmadd_vv)(q,r2,yy,vl);yy=D(vfmul_vv)(yy,s,vl);
  FV result=NARROW(yy,vl);
  if(__builtin_expect(nc!=0,0)){
   float original[vl],values[vl];F(vse32_v)(original,a,vl);
   F(vse32_v)(values,result,vl);
   for(size_t i=0;i<vl;++i){uint32_t b;__builtin_memcpy(&b,&original[i],4);
    if((b&UINT32_C(0x7fffffff))>=UINT32_C(0x42b00000))values[i]=expf(original[i]);}
   result=F(vle32_v)(values,vl);
  }
  return result;
}
#include "unary_entries.inc.h"
#undef OP_
#undef OP
#undef F
#undef D
#undef U
#undef W
#undef M
