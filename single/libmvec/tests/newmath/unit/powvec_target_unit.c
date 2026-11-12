/* Independent target-only powvec tests. Link powvec.o and --wrap=pow.
   Include task/source and task/data. This file is not a library component. */
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <errno.h>
#include "powvec.h"
#include "powvec_unit_data.h"
extern int printf_(const char*,...);
#define printf printf_
#define CHUNK 257
#define GUARD UINT64_C(0x5aa55aa55aa55aa5)
static double x[CHUNK],z[CHUNK],y[CHUNK+2];
static unsigned failed;
static uint64_t checks,scalar_calls,forbidden_scalar_calls;
extern double __real_pow(double,double);
static uint64_t bits(double x){uint64_t b;__builtin_memcpy(&b,&x,8);return b;}
static double val(uint64_t b){double x;__builtin_memcpy(&x,&b,8);return x;}
static uint64_t ordered(uint64_t b){return b>>63?~b:b|UINT64_C(0x8000000000000000);}
static unsigned flags(void){unsigned f;__asm__ volatile("frflags %0":"=r"(f));return f;}
static void clearflags(void){__asm__ volatile("fsflags zero":::"memory");}
/* For the 115422 ordinary oracle lanes, no fallback is legal. Large-exp
   oracle groups begin after this boundary; no FP classifier adds flags here. */
static int calls_forbidden;
double __wrap_pow(double a,double b){
    ++scalar_calls;if(calls_forbidden)++forbidden_scalar_calls;
    return __real_pow(a,b);
}
static void error(const char *kind,unsigned m,unsigned i,uint64_t got,uint64_t want){
    if(failed++<20)printf("POWVEC_ERROR kind=%s mode=%u i=%u got=%llu want=%llu\n",kind,m,i,(unsigned long long)got,(unsigned long long)want);
}
typedef void(*fn_t)(size_t,const double*,const double*,double*);
static const fn_t fn[2]={powvec_m1,powvec_m2};
static const uint64_t sem[][2]={
    {0,0xbff0000000000000ULL},{0x8000000000000000ULL,0xc008000000000000ULL},
    {0xbff0000000000000ULL,0x3fe0000000000000ULL},{0xc000000000000000ULL,0x4008000000000000ULL},
    {0x7ff0000000000001ULL,0},{0x3ff0000000000000ULL,0x7ff0000000000001ULL},
    {0x7ff0000000000001ULL,0x4000000000000000ULL},{0x7ff8000000000001ULL,0x4000000000000000ULL},
    {0xfff8000000001234ULL,0x3ff0000000000000ULL},{1,0x3ff0000000000000ULL},
    {0x3ff0000000000000ULL,0x3ff0000000000000ULL},{0x3ff0000000000000ULL,0x3be0000000000000ULL},
    {0x3ff0000000000000ULL,0x43e0000000000000ULL},{0x4000000000000000ULL,0x4090000000000000ULL},
    {0x4000000000000000ULL,0xc090cc0000000000ULL},{0x4000000000000000ULL,0xc090c80000000000ULL},
    {0x0010000000000000ULL,0x3ff0000000000000ULL},{0x7fefffffffffffffULL,0x3ff0000000000000ULL},
    {0x4000000000000000ULL,0x3ff0000000000000ULL},{0x4000000000000000ULL,0x4000000000000000ULL},
    {0x4010000000000000ULL,0x3fe0000000000000ULL},{0x3ff8000000000000ULL,0x3be0000000000000ULL},
    {0x7ff0000000000000ULL,0x4000000000000000ULL},{0xfff0000000000000ULL,0x4008000000000000ULL},
    {0x8000000000000000ULL,0x4008000000000000ULL},{0x7fefffffffffffffULL,0x3fe0000000000000ULL},
    {0x3fefffffffffffffULL,0x4330000000000000ULL},{0x3ff0000000000001ULL,0x4340000000000000ULL},
    {0xbff0000000000000ULL,0x433fffffffffffffULL},{0xbff0000000000000ULL,0x4340000000000000ULL}
};
extern int powvec_rne_probe(void);
int powvec_probe_main(void){unsigned rm;__asm__ volatile("frrm %0":"=r"(rm));return rm==0?37:99;}
int main(void){
    unsigned frm;__asm__ volatile("frrm %0":"=r"(frm));
    if(frm){printf("POWVEC_ERROR kind=rounding-contract\n");return 1;}
    /* Check actual addresses before writing errno. The integration linker
       owns TLS layout; a nonempty .tdata anchor changes errno's TLS offset.
       Build must provide the errno symbol offset taken from its final ELF. */
    extern char __tls_base[];
    uintptr_t tp;__asm__ volatile("mv %0,tp":"=r"(tp));
    uintptr_t eb=(uintptr_t)&errno,tb=(uintptr_t)__tls_base;
    printf("POWVEC_TLS base=%llu tp=%llu errno_address=%llu\n",(unsigned long long)tb,(unsigned long long)tp,(unsigned long long)eb);
#ifndef POWVEC_ERRNO_TLS_OFFSET
    /* A missing relocation-derived expectation must not risk a GOT write. */
    error("tls-offset-macro-required",0,0,eb,tb);
    printf("POWVEC_COMPLETE checks=%llu failed=%u status=fail\n",(unsigned long long)checks,failed);
    return 1;
#else
    checks+=2;
    uintptr_t expected_errno=tb+(uintptr_t)POWVEC_ERRNO_TLS_OFFSET;
    if(tp!=tb || eb!=expected_errno){
        error("tls-layout",0,0,eb,expected_errno);
        printf("POWVEC_COMPLETE checks=%llu failed=%u status=fail\n",(unsigned long long)checks,failed);
        return 1;
    }
#endif
    /* Nonzero errno must not overwrite a table GOT entry. Exercise both
       scalar pow and scalar exp after the write and require preservation. */
    double (*volatile scalar_exp)(double)=exp;
    errno=0;double exp_ref=scalar_exp(0.5);
    errno=ERANGE;double pow_after_errno=__real_pow(1.5,2.0);
    double exp_after_errno=scalar_exp(0.5);
    checks+=3;
    if(bits(pow_after_errno)!=bits(2.25))error("errno-pow-table",0,0,bits(pow_after_errno),bits(2.25));
    if(bits(exp_after_errno)!=bits(exp_ref))error("errno-exp-table",0,0,bits(exp_after_errno),bits(exp_ref));
    if(errno!=ERANGE)error("errno-normal-preserve",0,0,errno,ERANGE);
    for(unsigned m=0;m<2;++m){
        uint64_t maxulp=0,nonzero=0;scalar_calls=forbidden_scalar_calls=0;
        /* Split exactly at the normal-domain group boundary for wrapping. */
        for(size_t p=0;p<POWVEC_UNIT_COUNT;){
            size_t n=POWVEC_UNIT_COUNT-p;if(n>CHUNK)n=CHUNK;
            if(p<115422 && p+n>115422)n=115422-p;
            calls_forbidden=p<115422;
            for(size_t i=0;i<n;++i){x[i]=val(powvec_unit_x[p+i]);z[i]=val(powvec_unit_z[p+i]);}
            for(size_t i=0;i<CHUNK+2;++i)y[i]=val(GUARD);
            fn[m](n,x,z,y+1);
            for(size_t i=0;i<n;++i){
                uint64_t a=bits(y[i+1]),b=powvec_unit_reference[p+i];
                uint64_t aa=a&UINT64_C(0x7fffffffffffffff),bb=b&UINT64_C(0x7fffffffffffffff);
                ++checks;
                if(bb>UINT64_C(0x7ff0000000000000)){
                    if(aa<=UINT64_C(0x7ff0000000000000))error("mpfr-nan",m,p+i,a,b);
                }else if(bb==0 || bb==UINT64_C(0x7ff0000000000000)){
                    if(a!=b)error("mpfr-special",m,p+i,a,b);
                }else{
                    uint64_t oa=ordered(a),ob=ordered(b),d=oa>ob?oa-ob:ob-oa;
                    if(d>maxulp)maxulp=d;
                    if(d)++nonzero;
                    if(d>POWVEC_UNIT_ULP_LIMIT)error("mpfr",m,p+i,a,b);
                }
            }
            ++checks;if(bits(y[0])!=GUARD || bits(y[n+1])!=GUARD)error("canary",m,p,bits(y[n+1]),GUARD);
            p+=n;
        }
        calls_forbidden=0;
        ++checks;if(forbidden_scalar_calls)error("normal-scalar-fallback",m,0,forbidden_scalar_calls,0);
        printf("POWVEC_MPFR mode=m%u count=%u max_ulp=%llu nonzero_ulp=%llu limit=%u scalar_fallback=%llu normal_fallback=%llu\n",m+1,POWVEC_UNIT_COUNT,(unsigned long long)maxulp,(unsigned long long)nonzero,POWVEC_UNIT_ULP_LIMIT,(unsigned long long)scalar_calls,(unsigned long long)forbidden_scalar_calls);
        unsigned ns=sizeof sem/sizeof *sem;
        for(unsigned s=0;s<ns;++s){
            double a=val(sem[s][0]),b=val(sem[s][1]);clearflags();errno=0;
            double ref=__real_pow(a,b);unsigned rf=flags();int re=errno;
            clearflags();errno=0;double out=0;fn[m](1,&a,&b,&out);unsigned vf=flags();int ve=errno;
            checks+=3;
            if(bits(out)!=bits(ref))error("scalar-value",m,s,bits(out),bits(ref));
            if(vf!=rf)error("scalar-fflags",m,s,vf,rf);
            if(ve!=re)error("scalar-errno",m,s,ve,re);
        }
        uint64_t want[sizeof sem/sizeof *sem];
        clearflags();errno=0;
        for(unsigned s=0;s<ns;++s){x[s]=val(sem[s][0]);z[s]=val(sem[s][1]);want[s]=bits(__real_pow(x[s],z[s]));}
        unsigned rf=flags();int re=errno;
        clearflags();errno=0;fn[m](ns,x,z,y+1);unsigned vf=flags();int ve=errno;
        checks+=2;if(vf!=rf)error("mixed-fflags",m,0,vf,rf);if(ve!=re)error("mixed-errno",m,0,ve,re);
        for(unsigned s=0;s<ns;++s){++checks;if(bits(y[s+1])!=want[s])error("mixed-value",m,s,bits(y[s+1]),want[s]);}
        /* n=0 accepts null pointers; every tail up to ns runs vector code. */
        fn[m](0,0,0,0);
        for(unsigned n=1;n<=ns;++n){
            for(unsigned s=0;s<n;++s){x[s]=val(sem[s][0]);z[s]=val(sem[s][1]);}
            fn[m](n,x,z,x);
            for(unsigned s=0;s<n;++s){++checks;if(bits(x[s])!=want[s])error("inplace-x",m,s,bits(x[s]),want[s]);}
            for(unsigned s=0;s<n;++s){x[s]=val(sem[s][0]);z[s]=val(sem[s][1]);}
            fn[m](n,x,z,z);
            for(unsigned s=0;s<n;++s){++checks;if(bits(z[s])!=want[s])error("inplace-z",m,s,bits(z[s]),want[s]);}
        }
        printf("POWVEC_SEMANTICS mode=m%u isolated=%u mixed=%u tails=%u failed=%u\n",m+1,ns,ns,ns,failed);
    }
    for(unsigned rm=0;rm<5;++rm){__asm__ volatile("fsrm %0"::"r"(rm):"memory");int r=powvec_rne_probe();unsigned after;__asm__ volatile("frrm %0":"=r"(after));checks+=2;if(r!=37||after!=rm)error("rne-wrapper",0,rm,r,after);}
    __asm__ volatile("fsrmi 0":::"memory");
    printf("POWVEC_RNE modes=5 entered_rne=1 restored=1\n");
    printf("POWVEC_COMPLETE checks=%llu failed=%u status=%s\n",(unsigned long long)checks,failed,failed?"fail":"pass");
    return failed?1:0;
}
