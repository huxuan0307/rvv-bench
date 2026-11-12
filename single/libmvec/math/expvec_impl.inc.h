/* RVV adaptation of glibc dbl-64/e_exp.c, Copyright (C) 2018-2026 FSF.
   SPDX-License-Identifier: LGPL-2.1-or-later. RNE-only array entry. */
#define VOP_(a,t) __riscv_##a##_##t
#define VOP(a,t) VOP_(a,t)
#define F(a) VOP(a,FT)
#define U(a) VOP(a,UT)
#define M(a) VOP(a,BT)
#define MUL(a,b) F(vfmul_vv)(a,b,vl)
#define ADD(a,b) F(vfadd_vv)(a,b,vl)
#define FMAC(a,b,c) F(vfmadd_vv)(a,b,c,vl)
#define FMAF(a,b,c) F(vfmadd_vf)(a,b,c,vl)
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl){
  UV bits=FU(a);
  UV ab=U(vand_vx)(bits,UINT64_C(0x7fffffffffffffff),vl);
  BV cold=UGE(ab,UINT64_C(0x4080000000000000),vl);
  BV tiny=ULT(ab,UINT64_C(0x3c90000000000000),vl);
  size_t nc=M(vcpop_m)(cold,vl),nt=M(vcpop_m)(tiny,vl);
  FV work=a;
  if(__builtin_expect(nc!=0,0))work=F(vfmerge_vfm)(work,0.0,cold,vl);
  if(__builtin_expect(nt!=0,0))work=F(vfmerge_vfm)(work,0.0,tiny,vl);
  /* The installed scalar ELF fuses InvLn2N*x+Shift into one fmadd. */
  FV kd=FMAF(work,expvec_data.invln2N,F(vfmv_v_f)(expvec_data.shift,vl));UV ki=FU(kd);
  kd=F(vfsub_vf)(kd,expvec_data.shift,vl);
  FV r=FMAF(kd,expvec_data.negln2hiN,work);
  r=FMAF(kd,expvec_data.negln2loN,r);
  UV idx=U(vsll_vx)(U(vand_vx)(ki,127,vl),4,vl);
  UV tailbits=GATHER(expvec_data.tab,idx,vl);
  UV scale=GATHER(expvec_data.tab+1,idx,vl);
  scale=U(vadd_vv)(scale,U(vsll_vx)(ki,45,vl),vl);
  FV tail=UF(tailbits),s=UF(scale),r2=MUL(r,r);
  FV left=FMAF(r,expvec_data.poly[1],F(vfmv_v_f)(expvec_data.poly[0],vl));
  FV right=FMAF(r,expvec_data.poly[3],F(vfmv_v_f)(expvec_data.poly[2],vl));
  FV tmp=FMAC(r2,left,ADD(tail,r));tmp=FMAC(MUL(r2,r2),right,tmp);
  FV result=FMAC(s,tmp,s);
  if(__builtin_expect(nt!=0,0)){
   FV tx=F(vmerge_vvm)(F(vfmv_v_f)(0.0,vl),a,tiny,vl);
   tx=F(vfadd_vf)(tx,1.0,vl);result=F(vmerge_vvm)(result,tx,tiny,vl);
  }
  if(__builtin_expect(nc!=0,0)){
   double original[vl],values[vl];F(vse64_v)(original,a,vl);
   F(vse64_v)(values,result,vl);
   for(size_t i=0;i<vl;++i){uint64_t b;__builtin_memcpy(&b,&original[i],8);
    if((b&UINT64_C(0x7fffffffffffffff))>=UINT64_C(0x4080000000000000))values[i]=exp(original[i]);}
   result=F(vle64_v)(values,vl);
  }
  return result;
}
#include "unary_entries.inc.h"
#undef VOP_
#undef VOP
#undef F
#undef U
#undef M
#undef MUL
#undef ADD
#undef FMAC
#undef FMAF
