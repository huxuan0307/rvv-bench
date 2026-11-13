/* RVV adaptation of glibc sysdeps/ieee754/dbl-64/e_pow.c.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later

   Dedicated 128-bin log with a compensated tail, compensated z*log(x),
   then the original 128-bin exp. Explicit FMA placement matches the frozen
   RISC-V scalar build (including contraction of InvLn2N*x + Shift).
   RNE is required; neither frm nor vxrm is read or written here. */
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
#define SPLAT(a) FOP(vfmv_v_f)(a,vl)
#define POW_CAT_(a,b) a##b
#define POW_CAT(a,b) POW_CAT_(a,b)

#ifdef SIXMATH_COLD_DISPATCH
static __attribute__((noinline,noclone,riscv_vector_cc)) FV POW_CAT(CORE,_cold)(FV original_x, FV original_z, size_t vl)
#else
static __attribute__((always_inline)) inline FV CORE(FV original_x, FV original_z, size_t vl)
#endif
{
        UV ix=REINTERPRET_FU(original_x);
        UV iz=UOP(vand_vx)(REINTERPRET_FU(original_z),UINT64_C(0x7fffffffffffffff),vl);
        /* Integer-only classification leaves signaling NaNs untouched.
           All negative/zero/subnormal/nonfinite bases, |z|<2^-65 and |z|>=2^63
           are cold scalar lanes. Positive normal bases and ordinary exponents
           never execute a scalar call unless the final exp needs its large
           argument (overflow/underflow/subnormal-result) specialcase. */
        BV normal_x=UCMPLT(UOP(vsub_vx)(ix,UINT64_C(0x0010000000000000),vl),
                             UINT64_C(0x7fe0000000000000),vl);
        BV normal_z=UCMPLT(UOP(vsub_vx)(iz,UINT64_C(0x3be0000000000000),vl),
                             UINT64_C(0x0800000000000000),vl);
        BV special=MOP(vmnot_m)(MOP(vmand_mm)(normal_x,normal_z,vl),vl);
        size_t nspecial=MOP(vcpop_m)(special,vl);
        FV safe_x=original_x, safe_z=original_z;
        if (__builtin_expect(nspecial!=0,0)) {
            /* Neutral (1,1) is flag-clean in every subsequent operation. */
            safe_x=FOP(vfmerge_vfm)(safe_x,1.0,special,vl);
            safe_z=FOP(vfmerge_vfm)(safe_z,1.0,special,vl);
        }
        ix=REINTERPRET_FU(safe_x);
        UV tmpbits=UOP(vsub_vx)(ix,UINT64_C(0x3fe6955500000000),vl);
        UV index=UOP(vand_vx)(UOP(vsrl_vx)(tmpbits,45,vl),127,vl);
        UV byte_index=UOP(vsll_vx)(index,5,vl);
        FV invc=GATHER(&powvec_log_data.tab[0].invc,byte_index,vl);
        FV logc=GATHER(&powvec_log_data.tab[0].logc,byte_index,vl);
        FV logctail=GATHER(&powvec_log_data.tab[0].logctail,byte_index,vl);
        UV reduced=UOP(vsub_vv)(ix,UOP(vand_vx)(tmpbits,UINT64_C(0xfff0000000000000),vl),vl);
        IV k=IOP(vsra_vx)(REINTERPRET_UI(tmpbits),52,vl);
        FV kd=CONVERT(k,vl);
        FV reduced_x=REINTERPRET_UF(reduced);
        FV r=FMA(reduced_x,invc,SPLAT(-1.0));
        FV t1=FMAF(kd,powvec_log_data.ln2hi,logc);
        FV t2=FA(t1,r);
        FV lo1=FMAF(kd,powvec_log_data.ln2lo,logctail);
        FV lo2=FA(FS(t1,t2),r);
        FV ar=MUL(r,powvec_log_data.poly[0]);
        FV ar2=FM(r,ar);
        FV ar3=FM(r,ar2);
        FV hi=FA(t2,ar2);
        FV lo3=FMA(ar,r,FOP(vfneg_v)(ar2,vl));
        FV lo4=FA(FS(t2,hi),ar2);
        /* Keep source association and scalar compiler contractions; this is
           not a Horner rewrite of log1p nor a rounded standalone log call. */
        FV p=FMAF(r,powvec_log_data.poly[6],SPLAT(powvec_log_data.poly[5]));
        FV q=FMAF(r,powvec_log_data.poly[4],SPLAT(powvec_log_data.poly[3]));
        p=FMA(ar2,p,q);
        q=FMAF(r,powvec_log_data.poly[2],SPLAT(powvec_log_data.poly[1]));
        p=FMA(ar2,p,q);
        FV lo=FA(FA(FA(lo1,lo2),lo3),lo4);
        lo=FMA(ar3,p,lo);
        FV loghi=FA(hi,lo);
        FV logtail=FA(FS(hi,loghi),lo);
        FV ehi=FM(safe_z,loghi);
        FV elo=FMA(safe_z,logtail,FMA(safe_z,loghi,FOP(vfneg_v)(ehi,vl)));

        UV abs_ehi=UOP(vand_vx)(REINTERPRET_FU(ehi),UINT64_C(0x7fffffffffffffff),vl);
        BV tiny=UCMPLT(abs_ehi,UINT64_C(0x3c90000000000000),vl);
        BV large=MOP(vmnot_m)(UCMPLT(abs_ehi,UINT64_C(0x4080000000000000),vl),vl);
        special=MOP(vmor_mm)(special,large,vl);
        nspecial=MOP(vcpop_m)(special,vl);
        size_t ntiny=MOP(vcpop_m)(tiny,vl);
        FV tiny_result;
        if (__builtin_expect(ntiny!=0,0)) {
            /* exp_inline returns 1+ehi for |ehi|<2^-54. Mask all other
               lanes before addition, avoiding spurious NX on exact powers. */
            FV tiny_ehi=FOP(vmerge_vvm)(SPLAT(0.0),ehi,tiny,vl);
            tiny_result=ADD(tiny_ehi,1.0);
        }
        BV exp_neutral=MOP(vmor_mm)(tiny,large,vl);
        if (__builtin_expect(ntiny!=0 || nspecial!=0,0)) {
            ehi=FOP(vfmerge_vfm)(ehi,0.0,exp_neutral,vl);
            elo=FOP(vfmerge_vfm)(elo,0.0,exp_neutral,vl);
        }
        /* The scalar RISC-V compiler fuses InvLn2N*ehi+Shift. Splitting
           these operations changes reduction ties and breaks bit agreement. */
        FV kdexp=FMAF(ehi,powvec_exp_data.invln2N,SPLAT(powvec_exp_data.shift));
        UV ki=REINTERPRET_FU(kdexp);
        kdexp=SUB(kdexp,powvec_exp_data.shift);
        r=FMAF(kdexp,powvec_exp_data.negln2hiN,ehi);
        r=FMAF(kdexp,powvec_exp_data.negln2loN,r);
        r=FA(r,elo);
        UV exp_index=UOP(vsll_vx)(UOP(vand_vx)(ki,127,vl),4,vl);
        UV tailbits=UGATHER(&powvec_exp_data.tab[0],exp_index,vl);
        FV tail=REINTERPRET_UF(tailbits);
        UV scale_bits=UGATHER(&powvec_exp_data.tab[1],exp_index,vl);
        scale_bits=UOP(vadd_vv)(scale_bits,UOP(vsll_vx)(ki,45,vl),vl);
        FV scale=REINTERPRET_UF(scale_bits);
        FV r2=FM(r,r);
        p=FMAF(r,powvec_exp_data.poly[1],SPLAT(powvec_exp_data.poly[0]));
        q=FMAF(r,powvec_exp_data.poly[3],SPLAT(powvec_exp_data.poly[2]));
        FV tmp=FMA(r2,p,FA(tail,r));
        tmp=FMA(FM(r2,r2),q,tmp);
        FV result=FMA(scale,tmp,scale);
        if (__builtin_expect(ntiny!=0,0))
            result=FOP(vmerge_vvm)(result,tiny_result,tiny,vl);
        if (__builtin_expect(nspecial!=0,0)) {
            /* Save originals before output stores, including y==x/y==z.
               The cold loop invokes pow only for marked lanes. Bit masks,
               not FP comparisons, preserve sNaN and signed-zero semantics.
               In lane order the scalar calls contribute sticky exceptions
               and errno exactly as ordinary scalar pow calls would. */
            double saved_x[vl],saved_z[vl],values[vl];
            uint64_t saved_special[vl];
            FOP(vse64_v)(saved_x,original_x,vl);
            FOP(vse64_v)(saved_z,original_z,vl);
            UV mark=UOP(vmerge_vxm)(UOP(vmv_v_x)(0,vl),1,special,vl);
            UOP(vse64_v)(saved_special,mark,vl);
            FOP(vse64_v)(values,result,vl);
            for (size_t lane=0;lane<vl;++lane)
                if (saved_special[lane])
                    values[lane]=pow(saved_x[lane],saved_z[lane]);
            result=FOP(vle64_v)(values,vl);
        }
    return result;
}
#ifdef SIXMATH_COLD_DISPATCH
static __attribute__((always_inline)) inline FV CORE(FV original_x, FV original_z, size_t vl)
{
        UV ix=REINTERPRET_FU(original_x);
        UV iz=UOP(vand_vx)(REINTERPRET_FU(original_z),UINT64_C(0x7fffffffffffffff),vl);
        BV normal_x=UCMPLT(UOP(vsub_vx)(ix,UINT64_C(0x0010000000000000),vl),
                             UINT64_C(0x7fe0000000000000),vl);
        BV normal_z=UCMPLT(UOP(vsub_vx)(iz,UINT64_C(0x3be0000000000000),vl),
                             UINT64_C(0x0800000000000000),vl);
        BV special=MOP(vmnot_m)(MOP(vmand_mm)(normal_x,normal_z,vl),vl);
        if (__builtin_expect(MOP(vcpop_m)(special,vl)!=0,0))
            return POW_CAT(CORE,_cold)(original_x,original_z,vl);
#ifdef SIXMATH_HOT_LIFETIME
        __asm__ volatile("" : "+vr"(original_x), "+vr"(original_z));
#endif
        ix=REINTERPRET_FU(original_x);
        UV tmpbits=UOP(vsub_vx)(ix,UINT64_C(0x3fe6955500000000),vl);
        UV index=UOP(vand_vx)(UOP(vsrl_vx)(tmpbits,45,vl),127,vl);
        UV byte_index=UOP(vsll_vx)(index,5,vl);
        FV invc=GATHER(&powvec_log_data.tab[0].invc,byte_index,vl);
        FV logc=GATHER(&powvec_log_data.tab[0].logc,byte_index,vl);
        FV logctail=GATHER(&powvec_log_data.tab[0].logctail,byte_index,vl);
        UV reduced=UOP(vsub_vv)(ix,UOP(vand_vx)(tmpbits,UINT64_C(0xfff0000000000000),vl),vl);
        IV k=IOP(vsra_vx)(REINTERPRET_UI(tmpbits),52,vl);
        FV kd=CONVERT(k,vl);
        FV reduced_x=REINTERPRET_UF(reduced);
        FV r=FMA(reduced_x,invc,SPLAT(-1.0));
        FV t1=FMAF(kd,powvec_log_data.ln2hi,logc);
        FV t2=FA(t1,r);
        FV lo1=FMAF(kd,powvec_log_data.ln2lo,logctail);
        FV lo2=FA(FS(t1,t2),r);
        FV ar=MUL(r,powvec_log_data.poly[0]);
        FV ar2=FM(r,ar);
        FV ar3=FM(r,ar2);
        FV hi=FA(t2,ar2);
        FV lo3=FMA(ar,r,FOP(vfneg_v)(ar2,vl));
        FV lo4=FA(FS(t2,hi),ar2);
        /* Keep source association and scalar compiler contractions; this is
           not a Horner rewrite of log1p nor a rounded standalone log call. */
        FV p=FMAF(r,powvec_log_data.poly[6],SPLAT(powvec_log_data.poly[5]));
        FV q=FMAF(r,powvec_log_data.poly[4],SPLAT(powvec_log_data.poly[3]));
        p=FMA(ar2,p,q);
        q=FMAF(r,powvec_log_data.poly[2],SPLAT(powvec_log_data.poly[1]));
        p=FMA(ar2,p,q);
        FV lo=FA(FA(FA(lo1,lo2),lo3),lo4);
        lo=FMA(ar3,p,lo);
        FV loghi=FA(hi,lo);
        FV logtail=FA(FS(hi,loghi),lo);
        FV ehi=FM(original_z,loghi);
        UV abs_ehi=UOP(vand_vx)(REINTERPRET_FU(ehi),UINT64_C(0x7fffffffffffffff),vl);
        BV large=MOP(vmnot_m)(UCMPLT(abs_ehi,UINT64_C(0x4080000000000000),vl),vl);
        if (__builtin_expect(MOP(vcpop_m)(large,vl)!=0,0))
            return POW_CAT(CORE,_cold)(original_x,original_z,vl);
        FV elo=FMA(original_z,logtail,FMA(original_z,loghi,FOP(vfneg_v)(ehi,vl)));
        BV tiny=UCMPLT(abs_ehi,UINT64_C(0x3c90000000000000),vl);
        size_t ntiny=MOP(vcpop_m)(tiny,vl);
        FV tiny_result;
        if (__builtin_expect(ntiny!=0,0)) {
            FV tiny_ehi=FOP(vmerge_vvm)(SPLAT(0.0),ehi,tiny,vl);
            tiny_result=ADD(tiny_ehi,1.0);
            ehi=FOP(vfmerge_vfm)(ehi,0.0,tiny,vl);
            elo=FOP(vfmerge_vfm)(elo,0.0,tiny,vl);
        }
        /* The scalar RISC-V compiler fuses InvLn2N*ehi+Shift. Splitting
           these operations changes reduction ties and breaks bit agreement. */
        FV kdexp=FMAF(ehi,powvec_exp_data.invln2N,SPLAT(powvec_exp_data.shift));
        UV ki=REINTERPRET_FU(kdexp);
        kdexp=SUB(kdexp,powvec_exp_data.shift);
        r=FMAF(kdexp,powvec_exp_data.negln2hiN,ehi);
        r=FMAF(kdexp,powvec_exp_data.negln2loN,r);
        r=FA(r,elo);
        UV exp_index=UOP(vsll_vx)(UOP(vand_vx)(ki,127,vl),4,vl);
        UV tailbits=UGATHER(&powvec_exp_data.tab[0],exp_index,vl);
        FV tail=REINTERPRET_UF(tailbits);
        UV scale_bits=UGATHER(&powvec_exp_data.tab[1],exp_index,vl);
        scale_bits=UOP(vadd_vv)(scale_bits,UOP(vsll_vx)(ki,45,vl),vl);
        FV scale=REINTERPRET_UF(scale_bits);
        FV r2=FM(r,r);
        p=FMAF(r,powvec_exp_data.poly[1],SPLAT(powvec_exp_data.poly[0]));
        q=FMAF(r,powvec_exp_data.poly[3],SPLAT(powvec_exp_data.poly[2]));
        FV tmp=FMA(r2,p,FA(tail,r));
        tmp=FMA(FM(r2,r2),q,tmp);
        FV result=FMA(scale,tmp,scale);
        if (__builtin_expect(ntiny!=0,0))
            result=FOP(vmerge_vvm)(result,tiny_result,tiny,vl);
        return result;
}
#endif
#include "binary_entries.inc.h"
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
#undef SPLAT
#undef POW_CAT
#undef POW_CAT_
