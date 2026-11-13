/* Independent MPFR, tail, fallback, special flags/errno and RNE contract tests. */
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <errno.h>
#include DATA_HEADER
extern int printf_(const char*,...);
#define printf printf_
#define CAT_(a,b) a##b
#define CAT(a,b) CAT_(a,b)
#define STR_(x) #x
#define STR(x) STR_(x)
#if SINGLE
 typedef float real_t;typedef uint32_t bits_t;
 #define SIGN UINT32_C(0x80000000)
 #define ABS UINT32_C(0x7fffffff)
 #define GUARD UINT32_C(0x5aa55aa5)
#else
 typedef double real_t;typedef uint64_t bits_t;
 #define SIGN UINT64_C(0x8000000000000000)
 #define ABS UINT64_C(0x7fffffffffffffff)
 #define GUARD UINT64_C(0x5aa55aa55aa55aa5)
#endif
extern void VEC1(size_t,const real_t*,const real_t*,real_t*);
extern void VEC2(size_t,const real_t*,const real_t*,real_t*);
extern real_t CAT(__real_,FUNC)(real_t);
static bits_t bits(real_t x){bits_t b;__builtin_memcpy(&b,&x,sizeof b);return b;}
static real_t value(bits_t b){real_t x;__builtin_memcpy(&x,&b,sizeof x);return x;}
static int cold(bits_t b){
#if LOG_INPUT
 return b-UINT64_C(0x0010000000000000)>=UINT64_C(0x7fe0000000000000);
#elif SINGLE
 return (b&ABS)>=(bits_t)0x42b00000;
#else
 return (b&ABS)>=UINT64_C(0x4080000000000000);
#endif
}
static uint64_t fallback,unexpected,checks;static unsigned failed;
real_t CAT(__wrap_,FUNC)(real_t x){++fallback;if(!cold(bits(x)))++unexpected;return CAT(__real_,FUNC)(x);}
static unsigned flags(void){unsigned f;__asm__ volatile("frflags %0":"=r"(f));return f;}
static void clearflags(void){__asm__ volatile("fsflags zero":::"memory");}
static uint64_t ordered(bits_t b){return b&SIGN?(bits_t)~b:b|SIGN;}
static void error(const char*k,unsigned m,size_t i,uint64_t got,uint64_t want){if(failed++<12)printf("UNIT_ERROR fn=%s kind=%s mode=%u i=%u got=%llu want=%llu\n",STR(FUNC),k,m,(unsigned)i,(unsigned long long)got,(unsigned long long)want);}
#define CHUNK 257
static real_t x[CHUNK],y[CHUNK+3];
typedef void(*fn_t)(size_t,const real_t*,const real_t*,real_t*);
static const fn_t fn[2]={VEC1,VEC2};
static const size_t lengths[]={1,2,3,4,5,7,8,9,15,16,17,31,65,129,257};
#if SINGLE
static const bits_t special[]={0,0x80000000U,1,2,0x007fffffU,0x00800000U,0x3f800000U,0x3f7fffffU,0x3f800001U,0xbf800000U,0x7f800000U,0xff800000U,0x7fc00001U,0x7f800001U,0xffc00123U,0xff800123U,0x42afffffU,0x42b00000U,0x42b00001U,0xc2afffffU,0xc2b00000U,0x42b17217U,0x42b17218U,0xc2cff1b4U,0x7f7fffffU};
#else
static const bits_t special[]={
 0x0000000000000000ULL,0x8000000000000000ULL,0xbff0000000000000ULL,
 0xfff0000000000000ULL,0x7ff0000000000000ULL,0x7ff8000000000001ULL,
 0x7ff0000000000001ULL,0xfff8000000001234ULL,0xfff0000000001234ULL,
 1ULL,2ULL,0x000fffffffffffffULL,0x3ff0000000000000ULL,
 0x3fefffffffffffffULL,0x3ff0000000000001ULL,
 0x3fedffffffffffffULL,0x3fee000000000000ULL,0x3fee000000000001ULL,
  0x3ff108ffffffffffULL,0x3ff1090000000000ULL,0x3ff1090000000001ULL,
  0x0010000000000000ULL,
 0x7fefffffffffffffULL,0x4000000000000000ULL};
#endif
extern char __tls_base[];
extern __thread unsigned char scalar_vec_tls_anchor;
extern int rne_probe(void);int probe_main(void){unsigned rm;__asm__ volatile("frrm %0":"=r"(rm));return rm==0?37:99;}
int main(void){
 uintptr_t tp;__asm__ volatile("mv %0,tp":"=r"(tp));
 printf("UNIT_TLS fn=%s base=%llu tp=%llu errno_addr=%llu errno_offset=%llu anchor_addr=%llu anchor_value=%u\n",STR(FUNC),(unsigned long long)(uintptr_t)__tls_base,(unsigned long long)tp,(unsigned long long)(uintptr_t)&errno,(unsigned long long)((uintptr_t)&errno-tp),(unsigned long long)(uintptr_t)&scalar_vec_tls_anchor,(unsigned)scalar_vec_tls_anchor);
 checks+=3;if(tp!=(uintptr_t)__tls_base)error("tls-base",0,0,tp,(uintptr_t)__tls_base);
 if((uintptr_t)&errno-tp!=UNIT_ERRNO_OFFSET)error("tls-symbol-offset",0,0,(uintptr_t)&errno-tp,UNIT_ERRNO_OFFSET);
 if(scalar_vec_tls_anchor!=1)error("tls-anchor",0,0,scalar_vec_tls_anchor,1);
 /* After a nonzero errno write, scalar calls must still see an intact GOT
    and the runtime's TLS errno address. This catches empty-.tdata tp bugs. */
 real_t ordinary=(real_t)0.5;errno=0;real_t sr=CAT(__real_,FUNC)(ordinary);
 errno=34;real_t sr2=CAT(__real_,FUNC)(ordinary);checks+=2;
 if(bits(sr)!=bits(sr2))error("tls-got-roundtrip",0,0,bits(sr2),bits(sr));
 if(errno!=34)error("tls-errno-roundtrip",0,0,errno,34);
 for(unsigned m=0;m<2;++m){
  fallback=unexpected=0;uint64_t expectedfb=0,maxulp=0,nonzero=0;unsigned li=0;
  fn[m](0,0,0,0);
  for(size_t p=0;p<UNIT_COUNT;){size_t n=lengths[li++%(sizeof lengths/sizeof *lengths)];if(n>UNIT_COUNT-p)n=UNIT_COUNT-p;
   for(size_t i=0;i<n;++i){x[i]=value(unit_input[p+i]);if(cold(unit_input[p+i]))++expectedfb;}
   for(size_t i=0;i<CHUNK+3;++i)y[i]=value(GUARD);
   fn[m](n,x,0,y+1);
   for(size_t i=0;i<n;++i){uint64_t a=ordered(bits(y[i+1])),b=ordered(unit_reference[p+i]),d=a>b?a-b:b-a;++checks;
    if(d>maxulp)maxulp=d;if(d)++nonzero;if(d>UNIT_LIMIT)error("mpfr",m,p+i,bits(y[i+1]),unit_reference[p+i]);}
   for(size_t i=0;i<CHUNK+3;++i)if(i==0||i>n){++checks;if(bits(y[i])!=GUARD)error("canary",m,p+i,bits(y[i]),GUARD);}
   p+=n;
  }
  checks+=2;if(unexpected)error("hot-scalar-fallback",m,0,unexpected,0);if(fallback!=expectedfb)error("fallback-count",m,0,fallback,expectedfb);
  printf("UNIT_MPFR fn=%s mode=m%u count=%u max_ulp=%llu nonzero=%llu limit=%u fallback=%llu expected_fallback=%llu hot_fallback=%llu\n",STR(FUNC),m+1,UNIT_COUNT,(unsigned long long)maxulp,(unsigned long long)nonzero,UNIT_LIMIT,(unsigned long long)fallback,(unsigned long long)expectedfb,(unsigned long long)unexpected);
  unsigned ns=sizeof special/sizeof *special;bits_t want[sizeof special/sizeof *special];
  for(unsigned s=0;s<ns;++s){real_t in=value(special[s]);clearflags();errno=0;real_t ref=CAT(__real_,FUNC)(in);unsigned rf=flags();int re=errno;
   clearflags();errno=0;real_t out=0;fn[m](1,&in,0,&out);unsigned vf=flags();int ve=errno;checks+=3;
   if(bits(ref)!=bits(out))error("special-value",m,s,bits(out),bits(ref));if(vf!=rf)error("special-flags",m,s,vf,rf);if(ve!=re)error("special-errno",m,s,ve,re);}
  clearflags();errno=0;for(unsigned s=0;s<ns;++s){x[s]=value(special[s]);want[s]=bits(CAT(__real_,FUNC)(x[s]));}unsigned rf=flags();int re=errno;
  clearflags();errno=0;fn[m](ns,x,0,y+1);unsigned vf=flags();int ve=errno;checks+=2;
  if(vf!=rf)error("mixed-flags",m,0,vf,rf);if(ve!=re)error("mixed-errno",m,0,ve,re);
  for(unsigned s=0;s<ns;++s){++checks;if(bits(y[s+1])!=want[s])error("mixed-value",m,s,bits(y[s+1]),want[s]);}
  for(unsigned s=0;s<ns;++s)x[s]=value(special[s]);fn[m](ns,x,0,x);
  for(unsigned s=0;s<ns;++s){++checks;if(bits(x[s])!=want[s])error("inplace",m,s,bits(x[s]),want[s]);}
  printf("UNIT_SEMANTICS fn=%s mode=m%u isolated=%u mixed=%u inplace=%u failed=%u\n",STR(FUNC),m+1,ns,ns,ns,failed);
 }
 for(unsigned rm=0;rm<5;++rm){__asm__ volatile("fsrm %0"::"r"(rm):"memory");int r=rne_probe();unsigned after;__asm__ volatile("frrm %0":"=r"(after));checks+=2;if(r!=37||after!=rm)error("rne-wrapper",0,rm,r,after);}
 __asm__ volatile("fsrmi 0":::"memory");printf("UNIT_COMPLETE fn=%s checks=%llu failed=%u status=%s\n",STR(FUNC),(unsigned long long)checks,failed,failed?"fail":"pass");return failed?1:0;
}
