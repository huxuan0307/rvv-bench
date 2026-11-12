/* Independent MPFR512 and vector-PCS gate. Compile this translation unit
   without fast-math, autovectorization, or builtin scalar math substitution.
   The kernels are RNE-only; five-mode tests exercise the outer RNE wrapper,
   not direct non-RNE mathematical accuracy. */
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <math.h>
#include "nx_case.h"
extern int printf_(const char *,...);
#define printf printf_
#if SINGLE
typedef float real_t;
typedef uint32_t bits_t;
#define SIGN UINT32_C(0x80000000)
#define ABS UINT32_C(0x7fffffff)
#define GUARD UINT32_C(0x5aa55aa5)
#else
typedef double real_t;
typedef uint64_t bits_t;
#define SIGN UINT64_C(0x8000000000000000)
#define ABS UINT64_C(0x7fffffffffffffff)
#define GUARD UINT64_C(0x5aa55aa55aa55aa5)
#endif
#define CAT0(a,b) a##b
#define CAT(a,b) CAT0(a,b)
extern void NX1(void),NX2(void);
typedef void (*entry_t)(void);
typedef void (*probe_t)(entry_t,const real_t*,const real_t*,real_t*,size_t,unsigned char*,uint64_t*);
#if SINGLE
extern void nx_probe_f32m1(entry_t,const real_t*,const real_t*,real_t*,size_t,unsigned char*,uint64_t*);
extern void nx_probe_f32m2(entry_t,const real_t*,const real_t*,real_t*,size_t,unsigned char*,uint64_t*);
static const probe_t probes[]={nx_probe_f32m1,nx_probe_f32m2};
#else
extern void nx_probe_f64m1(entry_t,const real_t*,const real_t*,real_t*,size_t,unsigned char*,uint64_t*);
extern void nx_probe_f64m2(entry_t,const real_t*,const real_t*,real_t*,size_t,unsigned char*,uint64_t*);
static const probe_t probes[]={nx_probe_f64m1,nx_probe_f64m2};
#endif
static const entry_t entries[]={NX1,NX2};
#if BINARY
extern real_t CAT(__real_,FUNC)(real_t,real_t);
#else
extern real_t CAT(__real_,FUNC)(real_t);
#endif
static uint64_t checks,scalar_calls,forbidden_calls;
static unsigned failed;
static int forbid_calls;
static bits_t bits(real_t x){bits_t b;__builtin_memcpy(&b,&x,sizeof b);return b;}
static real_t value(bits_t b){real_t x;__builtin_memcpy(&x,&b,sizeof x);return x;}
static uint64_t ordered(bits_t b){return b&SIGN?(bits_t)~b:b|SIGN;}
static unsigned flags(void){unsigned x;__asm__ volatile("frflags %0":"=r"(x));return x;}
static void clearflags(void){__asm__ volatile("fsflags zero":::"memory");}
#if BINARY
real_t CAT(__wrap_,FUNC)(real_t x,real_t y){++scalar_calls;if(forbid_calls)++forbidden_calls;return CAT(__real_,FUNC)(x,y);}
static real_t scalar(real_t x,real_t y){return CAT(__real_,FUNC)(x,y);}
#else
real_t CAT(__wrap_,FUNC)(real_t x){++scalar_calls;if(forbid_calls)++forbidden_calls;return CAT(__real_,FUNC)(x);}
static real_t scalar(real_t x,real_t y){(void)y;return CAT(__real_,FUNC)(x);}
#endif
static int cold(bits_t b){
#if KIND == 1
 return (b&ABS)>=UINT64_C(0x419921fb00000000);
#elif KIND == 2
 return b-UINT64_C(0x0010000000000000)>=UINT64_C(0x7fe0000000000000);
#elif KIND == 3
 return b-(bits_t)0x00800000>=(bits_t)0x7f000000;
#elif KIND == 4
 return (b&ABS)>=UINT64_C(0x4080000000000000);
#elif KIND == 5
 return (b&ABS)>=(bits_t)0x42b00000;
#else
 return 0;
#endif
}
static void error(const char *kind,unsigned m,size_t i,uint64_t got,uint64_t want){if(failed++<24)printf("NX_ERROR fn=%s kind=%s lmul=%u i=%u got=%llu want=%llu\n",FN_TEXT,kind,m+1,(unsigned)i,(unsigned long long)got,(unsigned long long)want);}
#define MAX_LANES 512
#define MAX_VLENB 2048
static real_t x[MAX_LANES],z[MAX_LANES],out[MAX_LANES+2];
static bits_t want[MAX_LANES];
static unsigned char snapshot[15*MAX_VLENB];
static uint64_t state[4];
static size_t vlenb;
static const unsigned preserved[]={1,2,3,4,5,6,7,24,25,26,27,28,29,30,31};
static void invoke(unsigned m,size_t n,size_t incoming,int abi){
 for(size_t i=0;i<n+2;++i)out[i]=value(GUARD);
 probes[m](entries[m],x,z,out+1,incoming,snapshot,state);
 checks+=4;
 if(state[2])error("vstart",m,incoming,state[2],0);
 if(state[3])error("frm",m,incoming,state[3],0);
 if(bits(out[0])!=GUARD)error("output-prefix",m,0,bits(out[0]),GUARD);
 if(bits(out[n+1])!=GUARD)error("output-suffix",m,n,bits(out[n+1]),GUARD);
 if(abi)for(unsigned r=0;r<15;++r)for(size_t j=0;j<vlenb;++j){++checks;unsigned char b=(unsigned char)(preserved[r]*7+3);if(snapshot[r*vlenb+j]!=b)error("callee-saved-vector-byte",m,r*vlenb+j,snapshot[r*vlenb+j],b);}
}
static int same_or_ulp(bits_t got,bits_t ref,unsigned limit){
 bits_t a=got&ABS,b=ref&ABS;
#if SINGLE
 const bits_t inf=UINT32_C(0x7f800000);
#else
 const bits_t inf=UINT64_C(0x7ff0000000000000);
#endif
 if(b>inf)return a>inf;
 if(b==inf||b==0)return got==ref;
 uint64_t aa=ordered(got),bb=ordered(ref);return (aa>bb?aa-bb:bb-aa)<=limit;
}
extern char __tls_base[];
extern __thread unsigned char scalar_vec_tls_anchor;
extern int nx_rne_probe(void);
int nx_probe_main(void){unsigned rm;__asm__ volatile("frrm %0":"=r"(rm));return rm==0?37:99;}
int main(void){
 __asm__ volatile("csrr %0,vlenb":"=r"(vlenb));
 if(vlenb>MAX_VLENB||2*vlenb/sizeof(real_t)>MAX_LANES){printf("NX_UNSUPPORTED fn=%s reason=vlen-cap bytes=%u\n",FN_TEXT,(unsigned)vlenb);return 1;}
 uintptr_t tp;__asm__ volatile("mv %0,tp":"=r"(tp));
 checks+=3;
 if(tp!=(uintptr_t)__tls_base)error("tls-base",0,0,tp,(uintptr_t)__tls_base);
 if((uintptr_t)&errno-tp!=UNIT_ERRNO_OFFSET)error("tls-errno-offset",0,0,(uintptr_t)&errno-tp,UNIT_ERRNO_OFFSET);
 if(scalar_vec_tls_anchor!=1||(uintptr_t)&scalar_vec_tls_anchor!=tp)error("tls-anchor",0,0,(uintptr_t)&scalar_vec_tls_anchor,tp);
 printf("NX_TLS fn=%s tp=%llu base=%llu errno_offset=%llu anchor=%u vlenb=%u\n",FN_TEXT,(unsigned long long)tp,(unsigned long long)(uintptr_t)__tls_base,(unsigned long long)((uintptr_t)&errno-tp),(unsigned)scalar_vec_tls_anchor,(unsigned)vlenb);
 errno=0;real_t q=scalar((real_t)0.75,(real_t)2);errno=34;real_t q2=scalar((real_t)0.75,(real_t)2);checks+=2;
 if(bits(q)!=bits(q2)||errno!=34)error("tls-got-errno-roundtrip",0,0,bits(q2),bits(q));
 for(unsigned m=0;m<2;++m){
  size_t n=(m+1)*vlenb/sizeof(real_t);uint64_t maxulp=0;scalar_calls=forbidden_calls=0;
#if !ABI_ONLY
  for(size_t p=0;p<ORACLE_COUNT;p+=n){
   for(size_t i=0;i<n;++i){size_t j=p+i;if(j>=ORACLE_COUNT)j=ORACLE_COUNT-1;x[i]=value(ORACLE_X[j]);
#if BINARY
    z[i]=value(ORACLE_Z[j]);
#else
    z[i]=0;
#endif
   }
#if BINARY
   forbid_calls=p+n<=115422;
#else
   forbid_calls=1;for(size_t i=0;i<n;++i)if(cold(bits(x[i])))forbid_calls=0;
#endif
   size_t incoming=(p/n)%4;incoming=incoming==0?0:incoming==1?1:incoming==2?n-1:n;
   invoke(m,n,incoming,0);
   for(size_t i=0;i<n&&p+i<ORACLE_COUNT;++i){bits_t got=bits(out[i+1]),ref=ORACLE_REF[p+i];++checks;if(!same_or_ulp(got,ref,ORACLE_LIMIT))error("mpfr512",m,p+i,got,ref);uint64_t a=ordered(got),b=ordered(ref),d=a>b?a-b:b-a;if(d>maxulp)maxulp=d;}
  }
  forbid_calls=0;++checks;if(forbidden_calls)error("ordinary-scalar-fallback",m,0,forbidden_calls,0);
  printf("NX_MPFR fn=%s lmul=%u count=%u limit=%u max_ulp_raw=%llu scalar_calls=%llu forbidden_calls=%llu\n",FN_TEXT,m+1,ORACLE_COUNT,ORACLE_LIMIT,(unsigned long long)maxulp,(unsigned long long)scalar_calls,(unsigned long long)forbidden_calls);
#endif
  /* Broadcast every historical semantic case, then rotate mixed cases across
     lanes. All four incoming vl values are tested at VLMAX, including zero. */
  size_t ns=sizeof semantic/sizeof *semantic;
  for(unsigned mixed=0;mixed<2;++mixed)for(size_t s=0;s<ns;++s)for(unsigned iv=0;iv<4;++iv){
   clearflags();errno=0;
   for(size_t i=0;i<n;++i){size_t j=mixed?(s+i)%ns:s;x[i]=value(semantic[j][0]);z[i]=value(semantic[j][1]);want[i]=bits(scalar(x[i],z[i]));}
   unsigned rf=flags();int re=errno;
#if ABI_ONLY
   clearflags();errno=0;invoke(m,n,n,1);rf=flags();re=errno;for(size_t i=0;i<n;++i)want[i]=bits(out[i+1]);
#endif
   clearflags();errno=0;
   size_t incoming=iv==0?0:iv==1?1:iv==2?n-1:n;
   invoke(m,n,incoming,1);unsigned vf=flags();int ve=errno;
   checks+=2;if(vf!=rf)error("special-fflags",m,s,vf,rf);if(ve!=re)error("special-errno",m,s,ve,re);
   for(size_t i=0;i<n;++i){++checks;int ok=bits(out[i+1])==want[i];
#if KIND == 1
    bits_t ab=bits(x[i])&ABS;bits_t tiny=COS_CASE?UINT64_C(0x3e40000000000000):UINT64_C(0x3e50000000000000);
    if(ab>=tiny&&ab<UINT64_C(0x419921fb00000000))ok=same_or_ulp(bits(out[i+1]),want[i],1);
#endif
    if(!ok)error("special-value",m,s*n+i,bits(out[i+1]),want[i]);}
  }
  /* Sticky flags/errno on a normal call must survive; no clearing in wrapper. */
  for(size_t i=0;i<n;++i){x[i]=(real_t)0.75;z[i]=(real_t)2;}
  errno=89;__asm__ volatile("fsflags %0"::"r"(24):"memory");invoke(m,n,1,1);checks+=2;if((flags()&24)!=24)error("sticky-fflags",m,0,flags(),24);if(errno!=89)error("sticky-errno",m,0,errno,89);
  printf("NX_ABI fn=%s lmul=%u incoming_vl=0,1,vlmax-1,vlmax semantics=%u mixed=1 preserved=v1-v7,v24-v31 normal=1 cold=1 caller_strict=1\n",FN_TEXT,m+1,(unsigned)ns);
 }
 for(unsigned rm=0;rm<5;++rm){__asm__ volatile("fsrm %0"::"r"(rm):"memory");int r=nx_rne_probe();unsigned after;__asm__ volatile("frrm %0":"=r"(after));checks+=2;if(r!=37)error("entry-rne",0,rm,r,37);if(after!=rm)error("entry-frm-restore",0,rm,after,rm);}
 __asm__ volatile("fsrmi 0":::"memory");
 printf("NX_COMPLETE fn=%s checks=%llu failed=%u status=%s direct_modes=RNE outer_wrapper_modes=5 abi_entries=2\n",FN_TEXT,(unsigned long long)checks,failed,failed?"fail":"pass");return failed?1:0;
}
