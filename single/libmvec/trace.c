/* Focused N16 trace driver; full numerical/ABI acceptance uses newmath gates. */
#include "kernel.h"
#include DATA_HEADER
#ifndef TRACE_KERNEL
#error TRACE_KERNEL required
#endif
#ifndef TRACE_K
#define TRACE_K 1
#endif
extern int printf_(const char *, ...);
static real_t x[16] __attribute__((aligned(64)));
static real_t z[16] __attribute__((aligned(64)));
static real_t y[16] __attribute__((aligned(64)));
static volatile uint64_t sink;
static inline uint64_t cycles(void) {
    uint64_t v; __asm__ volatile("rdcycle %0" : "=r"(v) :: "memory"); return v;
}
static bits_t bits(real_t v) { bits_t b; __builtin_memcpy(&b,&v,sizeof b); return b; }
static uint64_t ordered(bits_t b) {
    const bits_t sign=(bits_t)1 << (8*sizeof(bits_t)-1);
    return b&sign?(bits_t)~b:(b|sign);
}
/* Symbols identify the same measured region in disassembly and waveforms. */
__attribute__((noinline,noclone)) void trace_region(void) {
    for (unsigned k=0;k<TRACE_K;++k) TRACE_KERNEL(16,x,z,y);
}
int main(void) {
    for(unsigned i=0;i<16;++i) {
        bits_t a=input_bits[i],b=second_bits[i];
        __builtin_memcpy(&x[i],&a,sizeof a); __builtin_memcpy(&z[i],&b,sizeof b);
    }
    /* Two untimed calls warm the selected implementation only. */
    TRACE_KERNEL(16,x,z,y); TRACE_KERNEL(16,x,z,y);
    uint64_t begin=cycles(); trace_region(); uint64_t end=cycles();
    unsigned failed=0;
    for(unsigned i=0;i<16;++i) {
        uint64_t a=ordered(bits(y[i])),b=ordered(reference_bits[i]);
        if((a>b?a-b:b-a)>ORACLE_LIMIT) ++failed;
        sink^=bits(y[i]);
    }
    printf_("TRACE_RESULT n=16 k=%u begin=%llu end=%llu cycles=%llu failed=%u\n",
        TRACE_K,(unsigned long long)begin,(unsigned long long)end,
        (unsigned long long)(end-begin),failed);
    return failed?1:0;
}
