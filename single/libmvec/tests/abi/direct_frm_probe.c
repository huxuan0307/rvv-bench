/* Observe direct entry behavior under each legal frm without an RNE wrapper.
   The results are compared between separately linked builds; no numerical
   correctness claim for directed rounding is made by this probe. Nx calls
   use incoming VL=VLMAX. The supplied probe snapshots vector state, but
   this driver checks neither the PCS register snapshot nor vl/vtype/vstart. */
#include <stddef.h>
#include <stdint.h>
#include <errno.h>

#ifndef FUNC
#error FUNC is required
#endif
#ifndef SINGLE
#error SINGLE is required
#endif
#ifndef BINARY
#error BINARY is required
#endif
#define CAT0(a,b) a##b
#define CAT(a,b) CAT0(a,b)
#define STR0(x) #x
#define STR(x) STR0(x)
#if SINGLE
typedef float real_t;
typedef uint32_t bits_t;
static const bits_t inputs[] = {
  0x3f400000U, 0x3f800000U, 0x3fc00000U, 0x40000000U,
  0x00000000U, 0x80000000U, 0xbf800000U, 0x00000001U,
  0x7f800000U, 0xff800000U, 0x7fc00001U, 0x7f800001U,
  0x3f7fffffU, 0x3f800001U, 0x00800000U, 0x7f7fffffU
};
#else
typedef double real_t;
typedef uint64_t bits_t;
static const bits_t inputs[] = {
  UINT64_C(0x3fe8000000000000), UINT64_C(0x3ff0000000000000),
  UINT64_C(0x3ff8000000000000), UINT64_C(0x4000000000000000),
  UINT64_C(0x0000000000000000), UINT64_C(0x8000000000000000),
  UINT64_C(0xbff0000000000000), UINT64_C(0x0000000000000001),
  UINT64_C(0x7ff0000000000000), UINT64_C(0xfff0000000000000),
  UINT64_C(0x7ff8000000000001), UINT64_C(0x7ff0000000000001),
  UINT64_C(0x3fefffffffffffff), UINT64_C(0x3ff0000000000001),
  UINT64_C(0x0010000000000000), UINT64_C(0x7fefffffffffffff)
};
#endif
#define NPAT (sizeof inputs / sizeof inputs[0])
#define MAX_LANES 512
#define MAX_VLENB 2048
extern int printf_(const char *, ...);
extern void NX1(void), NX2(void);
extern void ARRAY1(size_t, const real_t *, const real_t *, real_t *);
extern void ARRAY2(size_t, const real_t *, const real_t *, real_t *);
typedef void (*entry_t)(void);
typedef void (*array_t)(size_t, const real_t *, const real_t *, real_t *);
typedef void (*probe_t)(entry_t, const real_t *, const real_t *, real_t *, size_t, unsigned char *, uint64_t *);
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
static real_t x[MAX_LANES],z[MAX_LANES],y[MAX_LANES];
static unsigned char snapshot[15*MAX_VLENB];
static uint64_t state[4];
static real_t value(bits_t b) { real_t f; __builtin_memcpy(&f,&b,sizeof f); return f; }
static bits_t bits(real_t f) { bits_t b; __builtin_memcpy(&b,&f,sizeof b); return b; }
int main(void) {
  size_t vlenb;
  __asm__ volatile("csrr %0,vlenb":"=r"(vlenb));
  if (vlenb<16 || (vlenb & (vlenb-1)) || vlenb>MAX_VLENB || 4*vlenb/sizeof(real_t)+1>MAX_LANES) {
    printf_("FRM_ERROR fn=%s reason=vlen-cap\n",STR(FUNC)); return 1;
  }
  printf_("FRM_CONFIG fn=%s vlenb=%u\n",STR(FUNC),(unsigned)vlenb);
  unsigned failed=0, cases=0, values=0;
  for (unsigned iface=0; iface<2; ++iface) for (unsigned m=0; m<2; ++m) {
    size_t full=(m+1)*vlenb/sizeof(real_t);
    size_t array_lengths[]={1,full-1,full,full+1,2*full+1};
    for (unsigned rm=0; rm<5; ++rm) for (unsigned seed=0; seed<2; ++seed)
      for (unsigned pattern=0; pattern<NPAT; ++pattern)
        for (unsigned shape=0; shape<(iface?5U:1U); ++shape) {
          size_t n=iface?array_lengths[shape]:full;
          for(size_t i=0;i<n;++i) {
            x[i]=value(inputs[(pattern+i)%NPAT]);
            z[i]=value(inputs[(pattern+3*i+1)%NPAT]);
            y[i]=value((bits_t)0x5a);
          }
          unsigned flags_seed=seed?24:0;
          errno=seed?89:0;
          __asm__ volatile("fsflags %0\n\tfsrm %1"::"r"(flags_seed),"r"(rm):"memory");
          if (iface) arrays[m](n,x,z,y);
          else probes[m](entries[m],x,z,y,full,snapshot,state);
          unsigned after,flags;
          __asm__ volatile("frrm %0\n\tfrflags %1":"=r"(after),"=r"(flags)::"memory");
          int err=errno;
          __asm__ volatile("fsrmi 0":::"memory");
          ++cases; values+=(unsigned)n;
          if(after!=rm || (flags&flags_seed)!=flags_seed || (!iface && state[3]!=rm)) {
            ++failed;
            printf_("FRM_ERROR fn=%s iface=%u m=%u rm=%u seed=%u pattern=%u shape=%u after=%u flags=%u state=%llu\n",
                    STR(FUNC),iface,m+1,rm,seed,pattern,shape,after,flags,(unsigned long long)state[3]);
          }
          printf_("FRM_CASE fn=%s iface=%u m=%u rm=%u seed=%u pattern=%u shape=%u n=%u after=%u flags=%u errno=%d\n",
                  STR(FUNC),iface,m+1,rm,seed,pattern,shape,(unsigned)n,after,flags,err);
          for(size_t i=0;i<n;++i)
            printf_("FRM_VALUE fn=%s iface=%u m=%u rm=%u seed=%u pattern=%u shape=%u lane=%u x=%llu z=%llu y=%llu\n",
                    STR(FUNC),iface,m+1,rm,seed,pattern,shape,(unsigned)i,
                    (unsigned long long)bits(x[i]),(unsigned long long)bits(z[i]),(unsigned long long)bits(y[i]));
        }
  }
  printf_("FRM_COMPLETE fn=%s cases=%u values=%u failed=%u status=%s\n",STR(FUNC),cases,values,failed,failed?"fail":"pass");
  return failed?1:0;
}
