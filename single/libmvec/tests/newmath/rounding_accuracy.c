/* Direct-entry rounding observation. One executable per function; see README.
   The corpus call grouping intentionally matches the historical gate.  The
   semantic probes observe historical input patterns but are not an ABI gate. */
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include "rounding_case.h"
extern int printf_(const char *, ...);
extern void _putc(char);
#if SINGLE
typedef float real_t;
typedef uint32_t bits_t;
#define GUARD UINT32_C(0x5aa55aa5)
#else
typedef double real_t;
typedef uint64_t bits_t;
#define GUARD UINT64_C(0x5aa55aa55aa55aa5)
#endif
#define MAX_VLENB 2048
#define MAX_LANES 512
#define CAP 512
#ifndef ERRNO_TLS_OFFSET
#error ERRNO_TLS_OFFSET must come from the final ELF TLS symbol table
#endif
extern char __tls_base[];
extern __thread unsigned char scalar_vec_tls_anchor;
extern void NX1(void), NX2(void);
extern void ARRAY1(size_t,const real_t*,const real_t*,real_t*);
extern void ARRAY2(size_t,const real_t*,const real_t*,real_t*);
typedef void (*entry_t)(void);
typedef void (*array_t)(size_t,const real_t*,const real_t*,real_t*);
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
static const array_t arrays[]={ARRAY1,ARRAY2};
static real_t x[CAP],z[CAP],y[CAP+2];
static bits_t input_x[CAP],input_z[CAP];
static unsigned char snapshot[15*MAX_VLENB];
static uint64_t state[4];
static real_t val(bits_t b){real_t r;__builtin_memcpy(&r,&b,sizeof r);return r;}
static bits_t bits(real_t r){bits_t b;__builtin_memcpy(&b,&r,sizeof b);return b;}
/* Fixed-width hex written directly to serial: no per-lane printf formatting. */
static void hex(bits_t b){static const char d[]="0123456789abcdef";for(int i=(int)(2*sizeof b)-1;i>=0;--i)_putc(d[(b>>(i*4))&15]);}
static void put(char c){_putc(c);}
static unsigned errors;
static uint64_t groups,lanes;
static void call(unsigned iface,unsigned m,unsigned rm,char kind,size_t id,size_t n,size_t emit,size_t incoming){
 for(size_t i=0;i<n;++i){input_x[i]=bits(x[i]);input_z[i]=bits(z[i]);}
 for(size_t i=0;i<n+2;++i)y[i]=val(GUARD);
 errno=0;
 /* Volatile asm memory clobbers, no LTO, no fast-math or builtin math.
    nx_probe reads vl/vtype/vstart then frm, with no intervening call or FP. */
 __asm__ volatile("fsflags zero\n\tfsrm %0"::"r"(rm):"memory");
 if(iface) arrays[m](n,x,BINARY?z:0,y+1);
 else probes[m](entries[m],x,z,y+1,incoming,snapshot,state);
 unsigned after,flags;
 __asm__ volatile("frrm %0\n\tfrflags %1":"=r"(after),"=r"(flags)::"memory");
 int err=errno;
 __asm__ volatile("fsrmi 0":::"memory");
 if(after!=rm||(!iface && state[3]!=rm)||bits(y[0])!=GUARD||bits(y[n+1])!=GUARD){
  ++errors;printf_("RA_ERROR kind=state iface=%u m=%u rm=%u id=%u after=%u probe=%llu\n",iface,m+1,rm,(unsigned)id,after,(unsigned long long)state[3]);
 }
 /* One line per original call; encode all n lanes, including Nx padding,
    while emit counts only genuine samples. */
 printf_("RA_GROUP %u %u %u %c %u %u %u %u %u %d ",iface,m+1,rm,kind,(unsigned)id,(unsigned)n,(unsigned)emit,(unsigned)incoming,after,err);
 printf_("%u ",flags);
 for(size_t i=0;i<n;++i){hex(input_x[i]);if(BINARY)hex(input_z[i]);hex(bits(y[i+1]));}
 put('\n');++groups;lanes+=emit;
}
static size_t array_length(size_t p,size_t li){
#if KIND == 1
 (void)li;return ORACLE_COUNT-p<257?ORACLE_COUNT-p:257;
#elif BINARY
 (void)li;size_t n=ORACLE_COUNT-p<257?ORACLE_COUNT-p:257;
 if(p<115422 && p+n>115422)n=115422-p;return n;
#else
 static const unsigned lengths[]={1,2,3,4,5,7,8,9,15,16,17,31,65,129,257};
 size_t n=lengths[li%15];return n>ORACLE_COUNT-p?ORACLE_COUNT-p:n;
#endif
}
int main(void){
 uintptr_t tp,base=(uintptr_t)__tls_base,anchor=(uintptr_t)&scalar_vec_tls_anchor;
 __asm__ volatile("mv %0,tp":"=r"(tp));
 uintptr_t errno_addr=(uintptr_t)&errno;
 if(tp!=base || anchor!=base || scalar_vec_tls_anchor!=1 || errno_addr!=base+(uintptr_t)ERRNO_TLS_OFFSET){
  printf_("RA_ERROR kind=tls tp=%llu base=%llu anchor=%llu errno=%llu expected=%llu\n",(unsigned long long)tp,(unsigned long long)base,(unsigned long long)anchor,(unsigned long long)errno_addr,(unsigned long long)(base+(uintptr_t)ERRNO_TLS_OFFSET));return 1;
 }
 printf_("RA_TLS %s %llu %llu %llu %u %llu %llu\n",FN_TEXT,(unsigned long long)tp,(unsigned long long)base,(unsigned long long)anchor,(unsigned)scalar_vec_tls_anchor,(unsigned long long)(errno_addr-base),(unsigned long long)ERRNO_TLS_OFFSET);
 size_t vlenb;__asm__ volatile("csrr %0,vlenb":"=r"(vlenb));
 if(vlenb<16 || (vlenb&(vlenb-1)) || vlenb>MAX_VLENB || 2*vlenb/sizeof(real_t)>MAX_LANES){printf_("RA_ERROR kind=vlen\n");return 1;}
 printf_("RA_CONFIG %s %u %u %u %u\n",FN_TEXT,(unsigned)vlenb,(unsigned)ORACLE_COUNT,(unsigned)(sizeof semantic/sizeof *semantic),ORACLE_LIMIT);
 for(unsigned iface=0;iface<2;++iface)for(unsigned m=0;m<2;++m){
  size_t full=(m+1)*vlenb/sizeof(real_t);
  for(unsigned rm=0;rm<5;++rm){
   size_t li=0;
   for(size_t p=0;p<ORACLE_COUNT;){
    size_t n=iface?array_length(p,li):full,emit=n;
    if(emit>ORACLE_COUNT-p)emit=ORACLE_COUNT-p;
    for(size_t i=0;i<n;++i){size_t j=p+i;if(j>=ORACLE_COUNT)j=ORACLE_COUNT-1;x[i]=val(ORACLE_X[j]);
#if BINARY
     z[i]=val(ORACLE_Z[j]);
#else
     z[i]=val(0);
#endif
    }
    size_t incoming=iface?0:li%4==0?0:li%4==1?1:li%4==2?full-1:full;
    call(iface,m,rm,'C',p,n,emit,incoming);p+=emit;++li;
   }
   /* Nx uses the historic broadcast/rotation and all incoming VL values.
      Array uses isolated and one mixed call, not in-place or tail tests. */
   size_t ns=sizeof semantic/sizeof *semantic;
   if(!iface){
    for(unsigned mixed=0;mixed<2;++mixed)for(size_t s=0;s<ns;++s)for(unsigned iv=0;iv<4;++iv){
     for(size_t i=0;i<full;++i){size_t j=mixed?(s+i)%ns:s;x[i]=val(semantic[j][0]);z[i]=val(semantic[j][1]);}
     size_t incoming=iv==0?0:iv==1?1:iv==2?full-1:full;
     call(0,m,rm,'S',((size_t)mixed*ns+s)*4+iv,full,full,incoming);
    }
   }else{
    for(size_t s=0;s<ns;++s){x[0]=val(semantic[s][0]);z[0]=val(semantic[s][1]);call(1,m,rm,'S',s,1,1,0);}
    for(size_t s=0;s<ns;++s){x[s]=val(semantic[s][0]);z[s]=val(semantic[s][1]);}call(1,m,rm,'S',ns,ns,ns,0);
   }
  }
 }
 printf_("RA_COMPLETE %s %llu %llu %u\n",FN_TEXT,(unsigned long long)groups,(unsigned long long)lanes,errors);
 return errors?1:0;
}
