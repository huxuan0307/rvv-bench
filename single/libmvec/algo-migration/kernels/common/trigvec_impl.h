/* IBM Accurate Mathematical Library, GNU libc s_sin.c and usncs.h.
   Copyright (C) 2001-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later

   RVV array adaptation: original IBM table and split range reduction.
   Experimental accuracy tests use RNE; non-RNE accuracy and the final
   API rounding contract remain open. The benchmark wrapper sets RNE once.
   No operation in reduce_sincos below is contracted: its compensation
   requires the same individual roundings as the scalar algorithm.
   Only local Taylor polynomials use explicit vector fused operations.
   This file is instantiated for LMUL 1/2 and sine/cosine. */
#define TV_OP_(a,t) __riscv_##a##_##t
#define TV_OP(a,t) TV_OP_(a,t)
#define FOP(a) TV_OP(a,FT)
#define UOP(a) TV_OP(a,UT)
#define MOP(a) TV_OP(a,BT)
#define FF(a,b) FOP(vfadd_vv)(a,b,vl)
#define FS(a,b) FOP(vfsub_vv)(a,b,vl)
#define FM(a,b) FOP(vfmul_vv)(a,b,vl)
#define FC(a) FOP(vfmv_v_f)(a,vl)
#define ADD(a,b) FOP(vfadd_vf)(a,b,vl)
#define SUB(a,b) FOP(vfsub_vf)(a,b,vl)
#define MUL(a,b) FOP(vfmul_vf)(a,b,vl)
#define FMA(a,b,c) FOP(vfmadd_vv)(a,b,c,vl)
#define FMAF(a,b,c) FOP(vfmadd_vf)(a,b,c,vl)
#define MERGE(a,b,m) FOP(vmerge_vvm)(a,b,m,vl)
#define ZERO(a,m) FOP(vfmerge_vfm)(a,0.0,m,vl)
#define ULT(a,b) UCMPLT(a,b,vl)
#define NOT(a) MOP(vmnot_m)(a,vl)
#define AND(a,b) MOP(vmand_mm)(a,b,vl)

#define TV_CAT_(a,b) a##b
#define TV_CAT(a,b) TV_CAT_(a,b)
#define TV_RNECORE TV_CAT(CORE,_rne)
#if (defined(SIXMATH_COLD_DISPATCH) && SIXMATH_COLD_DISPATCH) || \
    (defined(SIXMATH_TRIG_UNIFORM) && SIXMATH_TRIG_UNIFORM)
static __attribute__((noinline,noclone,riscv_vector_cc)) FV TV_CAT(CORE,_cold)(FV original, size_t vl)
{
        UV ub=REINTERPRET_FU(original);
        UV ab=UOP(vand_vx)(ub,UINT64_C(0x7fffffffffffffff),vl);
        BV cold=NOT(ULT(ab,UINT64_C(0x419921fb00000000)));
        const size_t ncold=MOP(vcpop_m)(cold,vl);
#if TRIG_COS
        BV tiny=ULT(ab,UINT64_C(0x3e40000000000000));
#else
        BV tiny=ULT(ab,UINT64_C(0x3e50000000000000));
#endif
        /* Sanitize before FP arithmetic: sNaN, huge and tiny lanes must not
           cause spurious NV, OF or NX in any inactive mathematical branch. */
        FV safe=ZERO(original,MOP(vmor_mm)(cold,tiny,vl));
        FV ax=FOP(vfabs_v)(safe,vl);
        BV direct=ULT(ab,UINT64_C(0x3feb600000000000));
        BV middle=AND(NOT(direct),ULT(ab,UINT64_C(0x400368fd00000000)));
        BV reduced=AND(NOT(ULT(ab,UINT64_C(0x400368fd00000000))),NOT(cold));

        /* IBM reduce_sincos: 136-bit split pi/2 reduction. Ordinary small
           lanes are zero here, avoiding a large+small artificial NX. */
        FV rx=MERGE(FC(0.0),safe,reduced);
        FV t=ADD(MUL(rx,0x1.45f306dc9c883p-1),0x1.8p52);
        FV xn=SUB(t,0x1.8p52);
        UV quadrant=UOP(vand_vx)(REINTERPRET_FU(t),3,vl);
        FV ry=FS(FS(rx,MUL(xn,0x1.921fb58000000p0)),MUL(xn,-0x1.dde973c000000p-27));
        FV t1=MUL(xn,-0x1.cb3b398000000p-55);
        FV t2=FS(ry,t1);
        FV da=FS(FS(ry,t2),t1);
        t1=MUL(xn,-0x1.d747f23e32ed7p-83);
        FV a=FS(t2,t1);
        da=FF(da,FS(FS(t2,a),t1));
#if TRIG_COS
        quadrant=UOP(vadd_vx)(quadrant,1,vl);
#endif
        /* The scalar's near-pi/2 branch has its own 106-bit residual. */
        FV mx=MERGE(FC(0x1.921fb54442d18p0),ax,middle);
        FV ma=FOP(vfrsub_vf)(mx,0x1.921fb54442d18p0,vl);
#if TRIG_COS
        FV mhi=ADD(ma,0x1.1a62633145c07p-54);
        FV mlo=ADD(FS(ma,mhi),0x1.1a62633145c07p-54);
        a=MERGE(a,mhi,middle);
        da=MERGE(da,mlo,middle);
#else
        a=MERGE(a,ma,middle);
        da=MERGE(da,FC(0x1.1a62633145c07p-54),middle);
#endif
        a=MERGE(a,safe,direct);
        da=ZERO(da,direct);
        BV odd=UCMPNE(UOP(vand_vx)(quadrant,1,vl),0,vl);
#if TRIG_COS
        BV use_cos=MOP(vmor_mm)(AND(odd,reduced),direct,vl);
#else
        BV use_cos=MOP(vmor_mm)(AND(odd,reduced),middle,vl);
#endif
        /* Shared 4-way indexed gather is the original IBM sincostab lookup,
           not the unrelated table-free RVV polynomial implementation. */
        FV aa=FOP(vfabs_v)(a,vl);
        FV grid=ADD(aa,0x1.8p45);
        UV index=UOP(vsll_vx)(UOP(vand_vx)(REINTERPRET_FU(grid),0xffffffff,vl),5,vl);
        FV sn=GATHER(trigvec_sincostab,index,vl);
        FV ssn=GATHER(trigvec_sincostab+1,index,vl);
        FV cs=GATHER(trigvec_sincostab+2,index,vl);
        FV ccs=GATHER(trigvec_sincostab+3,index,vl);
        FV delta=FS(aa,SUB(grid,0x1.8p45));
        /* For do_cos dx changes sign for a<0. For do_sin the scalar a<=0
           distinction affects only exact-zero, where dx is also zero. */
        BV negative=UCMPLT(UOP(vxor_vx)(REINTERPRET_FU(a),UINT64_C(0x8000000000000000),vl),UINT64_C(0x8000000000000000),vl);
        FV ad=MERGE(da,FOP(vfneg_v)(da,vl),negative);
        FV local=FF(delta,MERGE(FC(0.0),ad,use_cos));
        FV xx=FM(local,local);
        FV ps=FMAF(xx,8.33333214285722277379541354343671E-03,FC(-1.66666666666664880952546298448555E-01));
        FV pc=FMAF(xx,1.38888874007937613028114285595617E-03,FC(-4.16666666666664434524222570944589E-02));
        pc=FMA(xx,pc,FC(0.5));
        FV s=FM(FM(local,xx),ps);
        s=FF(local,FF(s,MERGE(ad,FC(0.0),use_cos)));
        FV c=FF(FM(xx,pc),FM(local,MERGE(ad,FC(0.0),use_cos)));
        FV sincor=FF(FS(FF(ssn,FM(s,ccs)),FM(sn,c)),FM(cs,s));
        FV coscor=FS(FS(FS(ccs,FM(s,ssn)),FM(cs,c)),FM(sn,s));
        FV sinval=FOP(vfsgnj_vv)(FF(sn,sincor),a,vl);
        FV result=MERGE(sinval,FF(cs,coscor),use_cos);

        /* The independent do_sin small-angle Taylor path is essential for
           relative accuracy near a multiple of pi. Mask other arguments to
           zero before evaluation, not merely the final result. */
        BV small=AND(NOT(use_cos),FCMPLT(aa,0.126,vl));
        FV ta=MERGE(FC(0.0),a,small);
        FV td=MERGE(FC(0.0),da,small);
        FV tx=FM(ta,ta);
        FV p=FMAF(tx,-0x1.addffc2fcdf59p-26,FC(0x1.71de27b9a7ed9p-19));
        p=FMA(tx,p,FC(-0x1.a01a019db08b8p-13));
        p=FMA(tx,p,FC(0x1.1111111110ecep-7));
        p=FMA(tx,p,FC(-0x1.5555555555555p-3));
        FV correction=FF(FM(FS(FM(p,ta),MUL(td,0.5)),tx),td);
        result=MERGE(result,FF(ta,correction),small);
        BV flip=AND(UCMPNE(UOP(vand_vx)(quadrant,2,vl),0,vl),reduced);
#if !TRIG_COS
        /* The middle sine branch is positive do_cos followed by copysign. */
        result=MERGE(result,FOP(vfsgnj_vv)(result,original,vl),middle);
#endif
        result=MERGE(result,FOP(vfneg_v)(result,vl),flip);
#if TRIG_COS
        result=MERGE(result,FC(1.0),tiny);
#else
        result=MERGE(result,original,tiny);
        /* scalar math_check_force_underflow(x) squares only subnormals.
           This also preserves the scalar's UF|NX behavior for nonzero x. */
        BV subnormal=ULT(ab,UINT64_C(0x0010000000000000));
        if(__builtin_expect(MOP(vcpop_m)(subnormal,vl)!=0,0)) {
            FV uf=MERGE(FC(0.0),original,subnormal);
            uf=FM(uf,uf);
            __asm__ volatile("" : : "vr"(uf) : "memory");
        }
#endif
        if(__builtin_expect(ncold!=0,0)) {
            /* Save original lanes even for exact in-place arrays. Scalar libm
               supplies Payne-Hanek reduction, NaN quieting and Inf EDOM. */
            double saved[vl], values[vl];
            FOP(vse64_v)(saved,original,vl);
            FOP(vse64_v)(values,result,vl);
            for(size_t lane=0;lane<vl;++lane) {
                uint64_t b; __builtin_memcpy(&b,saved+lane,sizeof b);
                if((b&UINT64_C(0x7fffffffffffffff))>=UINT64_C(0x419921fb00000000))
                    values[lane]=SCALAR(saved[lane]);
            }
            result=FOP(vle64_v)(values,vl);
        }
    return result;
}
static __attribute__((always_inline)) inline FV TV_RNECORE(FV original, size_t vl)
{
#if TRIG_COS && defined(SIXMATH_HOT_LIFETIME) && SIXMATH_HOT_LIFETIME
        __asm__ volatile("" : "+vr"(original));
#endif
        UV ub=REINTERPRET_FU(original);
        UV ab=UOP(vand_vx)(ub,UINT64_C(0x7fffffffffffffff),vl);
        BV cold=NOT(ULT(ab,UINT64_C(0x419921fb00000000)));
#if TRIG_COS
        BV tiny=ULT(ab,UINT64_C(0x3e40000000000000));
#else
        BV tiny=ULT(ab,UINT64_C(0x3e50000000000000));
#endif
        BV anycold=MOP(vmor_mm)(cold,tiny,vl);
        if (__builtin_expect(MOP(vfirst_m)(anycold,vl)>=0,0))
            return TV_CAT(CORE,_cold)(original,vl);
#if !TRIG_COS && defined(SIXMATH_HOT_LIFETIME) && SIXMATH_HOT_LIFETIME
        __asm__ volatile("" : "+vr"(original));
#endif
        FV safe=original;
        FV ax=FOP(vfabs_v)(safe,vl);
        BV direct=ULT(ab,UINT64_C(0x3feb600000000000));
        BV middle=AND(NOT(direct),ULT(ab,UINT64_C(0x400368fd00000000)));
        BV reduced=NOT(ULT(ab,UINT64_C(0x400368fd00000000)));
#if defined(SIXMATH_TRIG_UNIFORM) && SIXMATH_TRIG_UNIFORM
        FV a, da;
        UV quadrant;
        /* Non-direct lanes decide whether the original reduction is needed. */
        if (MOP(vfirst_m)(NOT(direct),vl)<0) {
            a=safe;
            da=FC(0.0);
            quadrant=UOP(vmv_v_x)(0,vl);
        } else {
#endif

        /* IBM reduce_sincos: 136-bit split pi/2 reduction. Ordinary small
           lanes are zero here, avoiding a large+small artificial NX. */
        FV rx=MERGE(FC(0.0),safe,reduced);
        FV t=ADD(MUL(rx,0x1.45f306dc9c883p-1),0x1.8p52);
        FV xn=SUB(t,0x1.8p52);
#if defined(SIXMATH_TRIG_UNIFORM) && SIXMATH_TRIG_UNIFORM
        quadrant=
#else
        UV quadrant=
#endif
        UOP(vand_vx)(REINTERPRET_FU(t),3,vl);
        FV ry=FS(FS(rx,MUL(xn,0x1.921fb58000000p0)),MUL(xn,-0x1.dde973c000000p-27));
        FV t1=MUL(xn,-0x1.cb3b398000000p-55);
        FV t2=FS(ry,t1);
#if defined(SIXMATH_TRIG_UNIFORM) && SIXMATH_TRIG_UNIFORM
        da=
#else
        FV da=
#endif
        FS(FS(ry,t2),t1);
        t1=MUL(xn,-0x1.d747f23e32ed7p-83);
#if defined(SIXMATH_TRIG_UNIFORM) && SIXMATH_TRIG_UNIFORM
        a=
#else
        FV a=
#endif
        FS(t2,t1);
        da=FF(da,FS(FS(t2,a),t1));
#if TRIG_COS
        quadrant=UOP(vadd_vx)(quadrant,1,vl);
#endif
        /* The scalar's near-pi/2 branch has its own 106-bit residual. */
        FV mx=MERGE(FC(0x1.921fb54442d18p0),ax,middle);
        FV ma=FOP(vfrsub_vf)(mx,0x1.921fb54442d18p0,vl);
#if TRIG_COS
        FV mhi=ADD(ma,0x1.1a62633145c07p-54);
        FV mlo=ADD(FS(ma,mhi),0x1.1a62633145c07p-54);
        a=MERGE(a,mhi,middle);
        da=MERGE(da,mlo,middle);
#else
        a=MERGE(a,ma,middle);
        da=MERGE(da,FC(0x1.1a62633145c07p-54),middle);
#endif
        a=MERGE(a,safe,direct);
        da=ZERO(da,direct);
#if defined(SIXMATH_TRIG_UNIFORM) && SIXMATH_TRIG_UNIFORM
        }
#endif
        BV odd=UCMPNE(UOP(vand_vx)(quadrant,1,vl),0,vl);
#if TRIG_COS
        BV use_cos=MOP(vmor_mm)(AND(odd,reduced),direct,vl);
#else
        BV use_cos=MOP(vmor_mm)(AND(odd,reduced),middle,vl);
#endif
        /* Shared 4-way indexed gather is the original IBM sincostab lookup,
           not the unrelated table-free RVV polynomial implementation. */
        FV aa=FOP(vfabs_v)(a,vl);
        FV grid=ADD(aa,0x1.8p45);
        UV index=UOP(vsll_vx)(UOP(vand_vx)(REINTERPRET_FU(grid),0xffffffff,vl),5,vl);
        FV sn=GATHER(trigvec_sincostab,index,vl);
        FV ssn=GATHER(trigvec_sincostab+1,index,vl);
        FV cs=GATHER(trigvec_sincostab+2,index,vl);
        FV ccs=GATHER(trigvec_sincostab+3,index,vl);
        FV delta=FS(aa,SUB(grid,0x1.8p45));
        /* For do_cos dx changes sign for a<0. For do_sin the scalar a<=0
           distinction affects only exact-zero, where dx is also zero. */
        BV negative=UCMPLT(UOP(vxor_vx)(REINTERPRET_FU(a),UINT64_C(0x8000000000000000),vl),UINT64_C(0x8000000000000000),vl);
        FV ad=MERGE(da,FOP(vfneg_v)(da,vl),negative);
        FV local=FF(delta,MERGE(FC(0.0),ad,use_cos));
        FV xx=FM(local,local);
        FV ps=FMAF(xx,8.33333214285722277379541354343671E-03,FC(-1.66666666666664880952546298448555E-01));
        FV pc=FMAF(xx,1.38888874007937613028114285595617E-03,FC(-4.16666666666664434524222570944589E-02));
        pc=FMA(xx,pc,FC(0.5));
        FV s=FM(FM(local,xx),ps);
        s=FF(local,FF(s,MERGE(ad,FC(0.0),use_cos)));
        FV c=FF(FM(xx,pc),FM(local,MERGE(ad,FC(0.0),use_cos)));
        FV sincor=FF(FS(FF(ssn,FM(s,ccs)),FM(sn,c)),FM(cs,s));
        FV coscor=FS(FS(FS(ccs,FM(s,ssn)),FM(cs,c)),FM(sn,s));
        FV sinval=FOP(vfsgnj_vv)(FF(sn,sincor),a,vl);
        FV result=MERGE(sinval,FF(cs,coscor),use_cos);

        /* The independent do_sin small-angle Taylor path is essential for
           relative accuracy near a multiple of pi. Mask other arguments to
           zero before evaluation, not merely the final result. */
        BV small=AND(NOT(use_cos),FCMPLT(aa,0.126,vl));
        FV ta=MERGE(FC(0.0),a,small);
        FV td=MERGE(FC(0.0),da,small);
        FV tx=FM(ta,ta);
        FV p=FMAF(tx,-0x1.addffc2fcdf59p-26,FC(0x1.71de27b9a7ed9p-19));
        p=FMA(tx,p,FC(-0x1.a01a019db08b8p-13));
        p=FMA(tx,p,FC(0x1.1111111110ecep-7));
        p=FMA(tx,p,FC(-0x1.5555555555555p-3));
        FV correction=FF(FM(FS(FM(p,ta),MUL(td,0.5)),tx),td);
        result=MERGE(result,FF(ta,correction),small);
        BV flip=AND(UCMPNE(UOP(vand_vx)(quadrant,2,vl),0,vl),reduced);
#if !TRIG_COS
        /* The middle sine branch is positive do_cos followed by copysign. */
        result=MERGE(result,FOP(vfsgnj_vv)(result,original,vl),middle);
#endif
        result=MERGE(result,FOP(vfneg_v)(result,vl),flip);
    return result;
}
#else
static __attribute__((always_inline)) inline FV TV_RNECORE(FV original, size_t vl)
{
        UV ub=REINTERPRET_FU(original);
        UV ab=UOP(vand_vx)(ub,UINT64_C(0x7fffffffffffffff),vl);
        BV cold=NOT(ULT(ab,UINT64_C(0x419921fb00000000)));
        const size_t ncold=MOP(vcpop_m)(cold,vl);
#if TRIG_COS
        BV tiny=ULT(ab,UINT64_C(0x3e40000000000000));
#else
        BV tiny=ULT(ab,UINT64_C(0x3e50000000000000));
#endif
        /* Sanitize before FP arithmetic: sNaN, huge and tiny lanes must not
           cause spurious NV, OF or NX in any inactive mathematical branch. */
        FV safe=ZERO(original,MOP(vmor_mm)(cold,tiny,vl));
        FV ax=FOP(vfabs_v)(safe,vl);
        BV direct=ULT(ab,UINT64_C(0x3feb600000000000));
        BV middle=AND(NOT(direct),ULT(ab,UINT64_C(0x400368fd00000000)));
        BV reduced=AND(NOT(ULT(ab,UINT64_C(0x400368fd00000000))),NOT(cold));

        /* IBM reduce_sincos: 136-bit split pi/2 reduction. Ordinary small
           lanes are zero here, avoiding a large+small artificial NX. */
        FV rx=MERGE(FC(0.0),safe,reduced);
        FV t=ADD(MUL(rx,0x1.45f306dc9c883p-1),0x1.8p52);
        FV xn=SUB(t,0x1.8p52);
        UV quadrant=UOP(vand_vx)(REINTERPRET_FU(t),3,vl);
        FV ry=FS(FS(rx,MUL(xn,0x1.921fb58000000p0)),MUL(xn,-0x1.dde973c000000p-27));
        FV t1=MUL(xn,-0x1.cb3b398000000p-55);
        FV t2=FS(ry,t1);
        FV da=FS(FS(ry,t2),t1);
        t1=MUL(xn,-0x1.d747f23e32ed7p-83);
        FV a=FS(t2,t1);
        da=FF(da,FS(FS(t2,a),t1));
#if TRIG_COS
        quadrant=UOP(vadd_vx)(quadrant,1,vl);
#endif
        /* The scalar's near-pi/2 branch has its own 106-bit residual. */
        FV mx=MERGE(FC(0x1.921fb54442d18p0),ax,middle);
        FV ma=FOP(vfrsub_vf)(mx,0x1.921fb54442d18p0,vl);
#if TRIG_COS
        FV mhi=ADD(ma,0x1.1a62633145c07p-54);
        FV mlo=ADD(FS(ma,mhi),0x1.1a62633145c07p-54);
        a=MERGE(a,mhi,middle);
        da=MERGE(da,mlo,middle);
#else
        a=MERGE(a,ma,middle);
        da=MERGE(da,FC(0x1.1a62633145c07p-54),middle);
#endif
        a=MERGE(a,safe,direct);
        da=ZERO(da,direct);
        BV odd=UCMPNE(UOP(vand_vx)(quadrant,1,vl),0,vl);
#if TRIG_COS
        BV use_cos=MOP(vmor_mm)(AND(odd,reduced),direct,vl);
#else
        BV use_cos=MOP(vmor_mm)(AND(odd,reduced),middle,vl);
#endif
        /* Shared 4-way indexed gather is the original IBM sincostab lookup,
           not the unrelated table-free RVV polynomial implementation. */
        FV aa=FOP(vfabs_v)(a,vl);
        FV grid=ADD(aa,0x1.8p45);
        UV index=UOP(vsll_vx)(UOP(vand_vx)(REINTERPRET_FU(grid),0xffffffff,vl),5,vl);
        FV sn=GATHER(trigvec_sincostab,index,vl);
        FV ssn=GATHER(trigvec_sincostab+1,index,vl);
        FV cs=GATHER(trigvec_sincostab+2,index,vl);
        FV ccs=GATHER(trigvec_sincostab+3,index,vl);
        FV delta=FS(aa,SUB(grid,0x1.8p45));
        /* For do_cos dx changes sign for a<0. For do_sin the scalar a<=0
           distinction affects only exact-zero, where dx is also zero. */
        BV negative=UCMPLT(UOP(vxor_vx)(REINTERPRET_FU(a),UINT64_C(0x8000000000000000),vl),UINT64_C(0x8000000000000000),vl);
        FV ad=MERGE(da,FOP(vfneg_v)(da,vl),negative);
        FV local=FF(delta,MERGE(FC(0.0),ad,use_cos));
        FV xx=FM(local,local);
        FV ps=FMAF(xx,8.33333214285722277379541354343671E-03,FC(-1.66666666666664880952546298448555E-01));
        FV pc=FMAF(xx,1.38888874007937613028114285595617E-03,FC(-4.16666666666664434524222570944589E-02));
        pc=FMA(xx,pc,FC(0.5));
        FV s=FM(FM(local,xx),ps);
        s=FF(local,FF(s,MERGE(ad,FC(0.0),use_cos)));
        FV c=FF(FM(xx,pc),FM(local,MERGE(ad,FC(0.0),use_cos)));
        FV sincor=FF(FS(FF(ssn,FM(s,ccs)),FM(sn,c)),FM(cs,s));
        FV coscor=FS(FS(FS(ccs,FM(s,ssn)),FM(cs,c)),FM(sn,s));
        FV sinval=FOP(vfsgnj_vv)(FF(sn,sincor),a,vl);
        FV result=MERGE(sinval,FF(cs,coscor),use_cos);

        /* The independent do_sin small-angle Taylor path is essential for
           relative accuracy near a multiple of pi. Mask other arguments to
           zero before evaluation, not merely the final result. */
        BV small=AND(NOT(use_cos),FCMPLT(aa,0.126,vl));
        FV ta=MERGE(FC(0.0),a,small);
        FV td=MERGE(FC(0.0),da,small);
        FV tx=FM(ta,ta);
        FV p=FMAF(tx,-0x1.addffc2fcdf59p-26,FC(0x1.71de27b9a7ed9p-19));
        p=FMA(tx,p,FC(-0x1.a01a019db08b8p-13));
        p=FMA(tx,p,FC(0x1.1111111110ecep-7));
        p=FMA(tx,p,FC(-0x1.5555555555555p-3));
        FV correction=FF(FM(FS(FM(p,ta),MUL(td,0.5)),tx),td);
        result=MERGE(result,FF(ta,correction),small);
        BV flip=AND(UCMPNE(UOP(vand_vx)(quadrant,2,vl),0,vl),reduced);
#if !TRIG_COS
        /* The middle sine branch is positive do_cos followed by copysign. */
        result=MERGE(result,FOP(vfsgnj_vv)(result,original,vl),middle);
#endif
        result=MERGE(result,FOP(vfneg_v)(result,vl),flip);
#if TRIG_COS
        result=MERGE(result,FC(1.0),tiny);
#else
        result=MERGE(result,original,tiny);
        /* scalar math_check_force_underflow(x) squares only subnormals.
           This also preserves the scalar's UF|NX behavior for nonzero x. */
        BV subnormal=ULT(ab,UINT64_C(0x0010000000000000));
        if(__builtin_expect(MOP(vcpop_m)(subnormal,vl)!=0,0)) {
            FV uf=MERGE(FC(0.0),original,subnormal);
            uf=FM(uf,uf);
            __asm__ volatile("" : : "vr"(uf) : "memory");
        }
#endif
        if(__builtin_expect(ncold!=0,0)) {
            /* Save original lanes even for exact in-place arrays. Scalar libm
               supplies Payne-Hanek reduction, NaN quieting and Inf EDOM. */
            double saved[vl], values[vl];
            FOP(vse64_v)(saved,original,vl);
            FOP(vse64_v)(values,result,vl);
            for(size_t lane=0;lane<vl;++lane) {
                uint64_t b; __builtin_memcpy(&b,saved+lane,sizeof b);
                if((b&UINT64_C(0x7fffffffffffffff))>=UINT64_C(0x419921fb00000000))
                    values[lane]=SCALAR(saved[lane]);
            }
            result=FOP(vle64_v)(values,vl);
        }
    return result;
}
#endif
/* Both magic-constant rounding steps and compensation require RNE.  Change
   only frm, not fflags, and restore the caller's mode after all arithmetic. */
static __attribute__((always_inline)) inline FV CORE(FV original, size_t vl)
{
        unsigned long oldfrm;
        __asm__ volatile ("frrm %0" : "=r" (oldfrm) : : "memory");
        if (__builtin_expect(oldfrm != 0, 0))
            __asm__ volatile ("fsrmi 0" : : : "memory");
        FV result = TV_RNECORE(original, vl);
        if (__builtin_expect(oldfrm != 0, 0))
            __asm__ volatile ("fsrm %0" : : "r" (oldfrm) : "memory");
        return result;
}
#undef TV_RNECORE
#undef TV_CAT
#undef TV_CAT_
#include "unary_entries.inc.h"
#undef TV_OP_
#undef TV_OP
#undef FOP
#undef UOP
#undef MOP
#undef FF
#undef FS
#undef FM
#undef FC
#undef ADD
#undef SUB
#undef MUL
#undef FMA
#undef FMAF
#undef MERGE
#undef ZERO
#undef ULT
#undef NOT
#undef AND
