/* RVV array adaptation of glibc sysdeps/ieee754/dbl-64/e_log.c.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   This template is instantiated with LMUL 1 and 2. All mathematical
   operations are lane-local; indexed vector loads select the 128 bins. */
#define CAT2_(a,b) a##b
#define CAT2(a,b) CAT2_(a,b)
#define OP_(a,t) __riscv_##a##_##t
#define OP(a,t) OP_(a,t)
#define FOP(a) OP(a,FT)
#define UOP(a) OP(a,UT)
#define IOP(a) OP(a,IT)
#define MOP(a) OP(a,BT)
#define FM(a,b) FOP(vfmul_vv)(a,b,vl)
#define FS(a,b) FOP(vfsub_vv)(a,b,vl)
#define FA(a,b) FOP(vfadd_vv)(a,b,vl)
#define MUL(a,b) FOP(vfmul_vf)(a,b,vl)
#define ADD(a,b) FOP(vfadd_vf)(a,b,vl)
#define SUB(a,b) FOP(vfsub_vf)(a,b,vl)
#define FMA(a,b,c) FOP(vfmadd_vv)(a,b,c,vl)
#define FMAF(a,b,c) FOP(vfmadd_vf)(a,b,c,vl)

#ifdef LOGVEC_BASELINE
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl)
#elif defined(LOGVEC_M1_ARRAY_FULL)
/* Reuse the baseline body for the m1 array and the Nx cold wrapper. */
static __attribute__((always_inline)) inline FV CAT2(CORE,_full)(FV a, size_t vl)
#else
static __attribute__((noinline,noclone,riscv_vector_cc)) FV CAT2(CORE,_cold)(FV a, size_t vl)
#endif
{
        UV bits=REINTERPRET_FU(a);
        /* Unsigned interval classification also rejects every negative lane.
           No floating-point operation touches signaling NaNs before fallback. */
        UV cls=UOP(vsub_vx)(bits,UINT64_C(0x0010000000000000),vl);
        BV special=UCMPLT(cls,UINT64_C(0x7fe0000000000000),vl);
        special=MOP(vmnot_m)(special,vl);
        size_t nspecial=MOP(vcpop_m)(special,vl);
        FV safe=a;
        if (__builtin_expect(nspecial!=0,0))
            safe=FOP(vfmerge_vfm)(safe,1.0,special,vl);
        UV ix=REINTERPRET_FU(safe);
        UV delta=UOP(vsub_vx)(ix,UINT64_C(0x3fee000000000000),vl);
        BV near=UCMPLT(delta,UINT64_C(0x0003090000000000),vl);
        size_t nnear=MOP(vcpop_m)(near,vl);
        UV tmp=UOP(vsub_vx)(ix,UINT64_C(0x3fe6000000000000),vl);
        UV index=UOP(vand_vx)(UOP(vsrl_vx)(tmp,45,vl),127,vl);
        UV byte_index=UOP(vsll_vx)(index,4,vl);
        FV invc=GATHER(&logvec_data.tab[0].invc,byte_index,vl);
        FV logc=GATHER(&logvec_data.tab[0].logc,byte_index,vl);
        UV iz=UOP(vsub_vv)(ix,UOP(vand_vx)(tmp,UINT64_C(0xfff0000000000000),vl),vl);
        IV k=IOP(vsra_vx)(REINTERPRET_UI(tmp),52,vl);
        FV kd=CONVERT(k,vl);
        FV z=REINTERPRET_UF(iz);
        if (__builtin_expect(nnear!=0,0)) {
            /* The table approximation is not the near-one algorithm. Make
               those lanes exact zero throughout this chain: merely replacing
               them by x=1 would still raise spurious NX from table reduction.
               This also keeps fallback-only +inf/zero/sNaN lanes flag-clean. */
            z=FOP(vfmerge_vfm)(z,1.0,near,vl);
            invc=FOP(vfmerge_vfm)(invc,1.0,near,vl);
            logc=FOP(vfmerge_vfm)(logc,0.0,near,vl);
            kd=FOP(vfmerge_vfm)(kd,0.0,near,vl);
        }
        FV r=FMA(z,invc,FOP(vfmv_v_f)(-1.0,vl));
        FV w=FMAF(kd,logvec_data.ln2hi,logc);
        FV hi=FA(w,r);
        FV lo=FMAF(kd,logvec_data.ln2lo,FA(FS(w,hi),r));
        FV r2=FM(r,r);
        FV p=FMAF(r,logvec_data.poly[4],FOP(vfmv_v_f)(logvec_data.poly[3],vl));
        FV q=FMAF(r,logvec_data.poly[2],FOP(vfmv_v_f)(logvec_data.poly[1],vl));
        p=FMA(r2,p,q);
        lo=FMAF(r2,logvec_data.poly[0],lo);
        FV result=FA(FMA(FM(r,r2),p,lo),hi);

        if (__builtin_expect(nnear!=0,0)) {
            /* Replace non-near lanes by 1 before the degree-12 expression:
               huge normal inputs must not overflow this cold-path polynomial. */
            FV near_a=FOP(vmerge_vvm)(FOP(vfmv_v_f)(1.0,vl),safe,near,vl);
            FV t=SUB(near_a,1.0),t2=FM(t,t),t3=FM(t,t2);
            const double *b=logvec_data.poly1;
            FV c=FMAF(t,b[8],FOP(vfmv_v_f)(b[7],vl));
            c=FMAF(t2,b[9],c);
            c=FMAF(t3,b[10],c);
            FV d=FMAF(t,b[5],FOP(vfmv_v_f)(b[4],vl));
            d=FMAF(t2,b[6],d);
            d=FMA(t3,c,d);
            c=FMAF(t,b[2],FOP(vfmv_v_f)(b[1],vl));
            c=FMAF(t2,b[3],c);
            c=FMA(t3,d,c);
            FV ny=FM(t3,c);
            FV tw=MUL(t,0x1p27);
            FV thi=FS(FA(t,tw),tw),tlo=FS(t,thi);
            tw=MUL(FM(thi,thi),b[0]);
            FV nhi=FA(t,tw),nlo=FA(FS(t,nhi),tw);
            nlo=FMA(MUL(tlo,b[0]),FA(thi,t),nlo);
            ny=FA(FA(ny,nlo),nhi);
            /* With RNE, t == 0 produces +0, as required for log(1). */
            result=FOP(vmerge_vvm)(result,ny,near,vl);
        }
        if (__builtin_expect(nspecial!=0,0)) {
            /* Rare lanes alone call scalar libm. Save the original vector before
               stores so exact in-place operation remains valid. This VLA exists
               only on this branch, never on the ordinary N16 timed path. */
            double original[vl], values[vl];
            FOP(vse64_v)(original,a,vl);
            FOP(vse64_v)(values,result,vl);
            for(size_t lane=0;lane<vl;++lane) {
                uint64_t b; __builtin_memcpy(&b,&original[lane],sizeof b);
                if(b-UINT64_C(0x0010000000000000)>=UINT64_C(0x7fe0000000000000))
                    values[lane]=log(original[lane]);
            }
            result=FOP(vle64_v)(values,vl);
        }
    return result;
}
#ifdef LOGVEC_M1_ARRAY_FULL
static __attribute__((noinline,noclone,riscv_vector_cc)) FV
CAT2(CORE,_cold)(FV a, size_t vl)
{
    return CAT2(CORE,_full)(a,vl);
}
#endif
#ifndef LOGVEC_BASELINE
/* Dispatch before floating-point operations so cold lanes retain exact flags. */
static __attribute__((always_inline)) inline FV CORE(FV a, size_t vl)
{
        UV bits=REINTERPRET_FU(a);
        /* Unsigned interval classification also rejects every negative lane.
           No floating-point operation touches signaling NaNs before fallback. */
        UV cls=UOP(vsub_vx)(bits,UINT64_C(0x0010000000000000),vl);
        BV special=UCMPLT(cls,UINT64_C(0x7fe0000000000000),vl);
        special=MOP(vmnot_m)(special,vl);
        UV ix=bits;
        UV delta=UOP(vsub_vx)(ix,UINT64_C(0x3fee000000000000),vl);
        BV near=UCMPLT(delta,UINT64_C(0x0003090000000000),vl);
        /* Only any-lane membership matters here, not the two population counts. */
        BV cold=MOP(vmor_mm)(special,near,vl);
        long first=MOP(vfirst_m)(cold,vl);
        if (__builtin_expect(first>=0,0))
            return CAT2(CORE,_cold)(a,vl);
#ifdef LOGVEC_HOT_LIFETIME
        /* End dispatch temporaries before allocating destructive hot operands. */
        __asm__ volatile("" : "+vr"(a));
        ix=REINTERPRET_FU(a);
#endif
        UV tmp=UOP(vsub_vx)(ix,UINT64_C(0x3fe6000000000000),vl);
        UV index=UOP(vand_vx)(UOP(vsrl_vx)(tmp,45,vl),127,vl);
        UV byte_index=UOP(vsll_vx)(index,4,vl);
        FV invc=GATHER(&logvec_data.tab[0].invc,byte_index,vl);
        FV logc=GATHER(&logvec_data.tab[0].logc,byte_index,vl);
        UV iz=UOP(vsub_vv)(ix,UOP(vand_vx)(tmp,UINT64_C(0xfff0000000000000),vl),vl);
        IV k=IOP(vsra_vx)(REINTERPRET_UI(tmp),52,vl);
        FV kd=CONVERT(k,vl);
        FV z=REINTERPRET_UF(iz);
        FV r=FMA(z,invc,FOP(vfmv_v_f)(-1.0,vl));
        FV w=FMAF(kd,logvec_data.ln2hi,logc);
        FV hi=FA(w,r);
        FV lo=FMAF(kd,logvec_data.ln2lo,FA(FS(w,hi),r));
        FV r2=FM(r,r);
        FV p=FMAF(r,logvec_data.poly[4],FOP(vfmv_v_f)(logvec_data.poly[3],vl));
        FV q=FMAF(r,logvec_data.poly[2],FOP(vfmv_v_f)(logvec_data.poly[1],vl));
        p=FMA(r2,p,q);
        lo=FMAF(r2,logvec_data.poly[0],lo);
        FV result=FA(FMA(FM(r,r2),p,lo),hi);

    return result;
}
#endif
#ifdef LOGVEC_M1_ARRAY_FULL
#define LOGVEC_M1_ARRAY_CORE CAT2(CORE,_full)
#endif
#include "unary_entries.inc.h"
#ifdef LOGVEC_M1_ARRAY_FULL
#undef LOGVEC_M1_ARRAY_CORE
#endif
#undef CAT2_
#undef CAT2
#undef OP_
#undef OP
#undef FOP
#undef UOP
#undef IOP
#undef MOP
#undef FM
#undef FS
#undef FA
#undef MUL
#undef ADD
#undef SUB
#undef FMA
#undef FMAF
