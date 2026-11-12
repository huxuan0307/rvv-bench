/* Independent MPFR512, scalar semantics, fallback and tail verification.
   Original benchmark tolerance: 1 ULP, never increased here. */
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <errno.h>
#include "trigvec_data.h"
#include "trigvec-unit-data.h"
extern int printf_(const char*,...);
#define printf printf_
#define CHUNK 257
#define GUARD UINT64_C(0x5aa55aa55aa55aa5)
static double x[CHUNK],y[CHUNK+2];
static uint64_t checks,fallback[2],ordinary[2];
static unsigned failed;
static uint64_t bits(double x){uint64_t b;__builtin_memcpy(&b,&x,8);return b;}
static double val(uint64_t b){double x;__builtin_memcpy(&x,&b,8);return x;}
static uint64_t ordered(uint64_t b){return b>>63?~b:b|UINT64_C(0x8000000000000000);}
static int special_value_ok(unsigned f,uint64_t input,uint64_t got,uint64_t want){
 uint64_t ab=input&UINT64_C(0x7fffffffffffffff);
 if(ab>=UINT64_C(0x419921fb00000000)||ab<(f?UINT64_C(0x3e40000000000000):UINT64_C(0x3e50000000000000)))return got==want;
 uint64_t a=ordered(got),b=ordered(want),d=a>b?a-b:b-a;
 return d<=1;
}
extern double __real_sin(double),__real_cos(double);
double __wrap_sin(double a){++fallback[0];if((bits(a)&UINT64_C(0x7fffffffffffffff))<UINT64_C(0x419921fb00000000))++ordinary[0];return __real_sin(a);}
double __wrap_cos(double a){++fallback[1];if((bits(a)&UINT64_C(0x7fffffffffffffff))<UINT64_C(0x419921fb00000000))++ordinary[1];return __real_cos(a);}
static unsigned flags(void){unsigned f;__asm__ volatile("frflags %0":"=r"(f));return f;}
static void clearflags(void){__asm__ volatile("fsflags zero":::"memory");}
static void error(const char *kind,unsigned f,unsigned m,unsigned i,uint64_t got,uint64_t want){
 if(failed++<24)printf("TRIG_ERROR kind=%s function=%u mode=%u i=%u got=%llu want=%llu\n",kind,f,m,i,(unsigned long long)got,(unsigned long long)want);
}
typedef void (*fn_t)(size_t,const double*,const double*,double*);
static const fn_t fn[2][2]={{sinvec_m1,sinvec_m2},{cosvec_m1,cosvec_m2}};
static double (*const scalar[2])(double)={__real_sin,__real_cos};
static const uint64_t *const reference[2]={trig_unit_sin,trig_unit_cos};
static const uint64_t special[]={
 0x0000000000000000ULL,0x8000000000000000ULL,
 0x7ff0000000000000ULL,0xfff0000000000000ULL,
 0x7ff8000000000001ULL,0x7ff0000000000001ULL,
 0xfff8000000001234ULL,0xfff0000000001234ULL,
 1ULL,2ULL,0x000fffffffffffffULL,0x8000000000000001ULL,
 0x800fffffffffffffULL,0x0010000000000000ULL,
 0x3e3fffffffffffffULL,0x3e40000000000000ULL,0x3e4fffffffffffffULL,
 0x3e50000000000000ULL,0x3feb600000000000ULL,0x400368fd00000000ULL,
 0x419921faffffffffULL,0x419921fb00000000ULL,0x7fefffffffffffffULL,
 0xffefffffffffffffULL,0x3ff0000000000000ULL,0x4000000000000000ULL,
 0x3ff921fb54442d18ULL,0x400921fb54442d18ULL};
extern int trigvec_rne_probe(void);
int trigvec_probe_main(void){unsigned rm;__asm__ volatile("frrm %0":"=r"(rm));return rm==0?37:99;}
extern char __tls_base[];
extern __thread unsigned char scalar_vec_tls_anchor;
int main(void){
 uintptr_t tp;__asm__ volatile("mv %0,tp":"=r"(tp));
 ++checks;if(tp!=(uintptr_t)__tls_base)error("tls-base",0,0,0,(uintptr_t)__tls_base,tp);
 ++checks;if(scalar_vec_tls_anchor!=1||(uintptr_t)&scalar_vec_tls_anchor!=tp)error("tls-anchor",0,0,0,(uintptr_t)&scalar_vec_tls_anchor,tp);
 printf("TRIG_TLS tp=%llu errno=%llu base=%llu offset=%llu\n",(unsigned long long)tp,(unsigned long long)(uintptr_t)&errno,(unsigned long long)(uintptr_t)__tls_base,(unsigned long long)((uintptr_t)&errno-tp));
 for(unsigned f=0;f<2;++f)for(unsigned m=0;m<2;++m){
  /* A nonzero errno store must not corrupt any ordinary scalar/GOT access. */
  errno=34;double probe=scalar[f](0.75);++checks;if(errno!=34||fabs(probe-(f?0.7316888688738209:0.6816387600233341))>0x1p-51)error("errno-before-scalar",f,m,0,bits(probe),34);
  errno=73;double probe_in=0.75,probe_out=0;fn[f][m](1,&probe_in,0,&probe_out);++checks;if(errno!=73||fabs(probe_out-probe)>0x1p-51)error("errno-before-vector",f,m,0,bits(probe_out),73);
  uint64_t maxulp=0,nonzero=0;fallback[f]=ordinary[f]=0;
  for(size_t pos=0;pos<TRIG_UNIT_COUNT;pos+=CHUNK){
   size_t n=TRIG_UNIT_COUNT-pos;if(n>CHUNK)n=CHUNK;
   for(size_t i=0;i<n;++i)x[i]=val(trig_unit_input[pos+i]);
   for(size_t i=0;i<CHUNK+2;++i)y[i]=val(GUARD);
   fn[f][m](n,x,0,y+1);
   for(size_t i=0;i<n;++i){
    uint64_t a=ordered(bits(y[i+1])),b=ordered(reference[f][pos+i]),d=a>b?a-b:b-a;
    ++checks;if(d>maxulp)maxulp=d;if(d)++nonzero;
    if(d>TRIG_UNIT_ULP_LIMIT)error("mpfr",f,m,pos+i,bits(y[i+1]),reference[f][pos+i]);
   }
   ++checks;if(bits(y[0])!=GUARD||bits(y[n+1])!=GUARD)error("canary",f,m,pos,bits(y[n+1]),GUARD);
  }
  ++checks;if(ordinary[f])error("ordinary-fallback",f,m,0,ordinary[f],0);
  printf("TRIG_MPFR function=%u mode=m%u count=%u max_ulp=%llu nonzero_ulp=%llu limit=%u fallback=%llu ordinary_fallback=%llu\n",f,m+1,TRIG_UNIT_COUNT,(unsigned long long)maxulp,(unsigned long long)nonzero,TRIG_UNIT_ULP_LIMIT,(unsigned long long)fallback[f],(unsigned long long)ordinary[f]);
  const unsigned ns=sizeof special/sizeof *special;
  uint64_t want[sizeof special/sizeof *special];
  for(unsigned s=0;s<ns;++s){
   double in=val(special[s]);clearflags();errno=0;
   double ref=scalar[f](in);unsigned rf=flags();int re=errno;
   clearflags();errno=0;double out=0;fn[f][m](1,&in,0,&out);unsigned vf=flags();int ve=errno;
   checks+=3;
   uint64_t rb=bits(ref),vb=bits(out);
   if(!special_value_ok(f,special[s],vb,rb))error("scalar-value",f,m,s,vb,rb);
   if(vf!=rf)error("scalar-fflags",f,m,s,vf,rf);
   if(ve!=re)error("scalar-errno",f,m,s,ve,re);
  }
  clearflags();errno=0;
  for(unsigned s=0;s<ns;++s){x[s]=val(special[s]);want[s]=bits(scalar[f](x[s]));}
  unsigned rf=flags();int re=errno;
  clearflags();errno=0;fn[f][m](ns,x,0,y+1);unsigned vf=flags();int ve=errno;
  checks+=2;if(vf!=rf)error("mixed-fflags",f,m,0,vf,rf);if(ve!=re)error("mixed-errno",f,m,0,ve,re);
  for(unsigned s=0;s<ns;++s){++checks;if(!special_value_ok(f,special[s],bits(y[s+1]),want[s]))error("mixed-value",f,m,s,bits(y[s+1]),want[s]);}
  for(unsigned s=0;s<ns;++s)x[s]=val(special[s]);
  fn[f][m](ns,x,0,x);
  for(unsigned s=0;s<ns;++s){++checks;if(!special_value_ok(f,special[s],bits(x[s]),want[s]))error("inplace",f,m,s,bits(x[s]),want[s]);}
  /* Every short tail around LMUL's VLMAX, including n=0, preserves guards. */
  for(unsigned n=0;n<=65;++n){
   for(unsigned i=0;i<n;++i)x[i]=val(trig_unit_input[32768+i]);
   for(unsigned i=0;i<CHUNK+2;++i)y[i]=val(GUARD);
   fn[f][m](n,x,0,y+1);
   checks+=2;if(bits(y[0])!=GUARD||bits(y[n+1])!=GUARD)error("tail-canary",f,m,n,bits(y[n+1]),GUARD);
   for(unsigned i=0;i<n;++i){++checks;uint64_t a=ordered(bits(y[i+1])),b=ordered(reference[f][32768+i]),d=a>b?a-b:b-a;if(d>1)error("tail-value",f,m,n,bits(y[i+1]),reference[f][32768+i]);}
  }
  /* Ordinary calls and n=0 preserve prior sticky flags and errno. */
  double sticky_in=0.75,sticky_out=0;errno=89;
  __asm__ volatile("fsflags %0"::"r"(24):"memory");
  fn[f][m](1,&sticky_in,0,&sticky_out);unsigned sticky=flags();checks+=2;
  if((sticky&24)!=24)error("sticky-flags",f,m,0,sticky,24);
  if(errno!=89)error("sticky-errno",f,m,0,errno,89);
  clearflags();errno=91;fn[f][m](0,0,0,0);checks+=2;
  if(flags()!=0)error("empty-flags",f,m,0,flags(),0);
  if(errno!=91)error("empty-errno",f,m,0,errno,91);
  printf("TRIG_SEMANTICS function=%u mode=m%u isolated=%u mixed=%u tails=66 failed=%u\n",f,m+1,ns,ns,failed);
 }
 for(unsigned rm=0;rm<5;++rm){
  __asm__ volatile("fsrm %0"::"r"(rm):"memory");int ret=trigvec_rne_probe();unsigned after;
  __asm__ volatile("frrm %0":"=r"(after));checks+=2;
  if(ret!=37)error("rne-entry",0,rm,0,ret,37);
  if(after!=rm)error("rne-restore",0,rm,0,after,rm);
 }
 __asm__ volatile("fsrmi 0":::"memory");
 printf("TRIG_RNE_WRAPPER modes=5 restore=pass\n");
 printf("TRIG_COMPLETE checks=%llu failed=%u status=%s\n",(unsigned long long)checks,failed,failed?"fail":"pass");
 return failed?1:0;
}
