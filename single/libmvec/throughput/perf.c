/* 香山 array-call measurement. Print only after all timed samples are saved. */
#include "kernel.h"
#include "../../../autovec/av_runtime.h"
#include DATA_HEADER
#define STR1(x) #x
#define STR(x) STR1(x)
#ifdef SINGLE_MODE
#define CASE_NAME "perf-" STR(FUNC) "-" STR(SINGLE_MODE)
#else
#define CASE_NAME "perf-" STR(FUNC)
#endif
#define GUARD ((bits_t)0x5aa55aa55aa55aa5ULL)
#define ROUNDS 10
#ifndef N16_TRIM
#define N16_TRIM 0
#endif
/* Measurement repeats per rdcycle interval. Each repeat runs the whole K-call
   kernel, so one interval covers TIMING_REPEAT rounds; reporting divides by it.
   R is not a workload dimension and must stay identical across compared runs. */
#ifndef TIMING_REPEAT
#define TIMING_REPEAT 1
#endif
#if TIMING_REPEAT < 1 || TIMING_REPEAT > 64
#error "TIMING_REPEAT must be in [1, 64]"
#endif
#if N16_TRIM
#define MODES 3
#define CONTROLS 0
#else
/* HARNESS_MODES selects the original six-mode matrix or its LMUL4/8
   extension.  build.py sets it per case from the vector symbols actually
   provided by the linked math archive, so a partial kernel set never
   references a missing kernel. */
#ifndef HARNESS_MODES
#define HARNESS_MODES 6
#endif
#if !defined(SINGLE_MODE) && HARNESS_MODES != 6 && HARNESS_MODES != 8 && HARNESS_MODES != 10
#error "HARNESS_MODES must be 6, 8 or 10"
#endif
#define MODES HARNESS_MODES
#define CONTROLS 3
#endif
#define MAX_RECORDS (3*3*ROUNDS*(MODES+CONTROLS))
#ifndef FORMAT_SMOKE
#define FORMAT_SMOKE 0
#endif
#ifndef N16_ONLY
#define N16_ONLY 0
#endif
#if N16_TRIM
#define WORK_COUNT 17
#define PROFILE_NAME "n16-trim"
#elif FORMAT_SMOKE
#define WORK_COUNT 32
#define PROFILE_NAME "format-smoke"
#elif defined(GRID_LENGTHS)
#define WORK_COUNT DATA_COUNT
#define PROFILE_NAME "grid"
#else
#define WORK_COUNT DATA_COUNT
#if N16_ONLY
#define PROFILE_NAME "n16"
#else
#define PROFILE_NAME "full"
#endif
#endif
_Static_assert(WORK_COUNT<=DATA_COUNT, "input corpus too short");
#if defined(GRID_LENGTHS) && (FORMAT_SMOKE || N16_TRIM)
#error Custom grids require the full mode matrix and full input corpus
#endif
static real_t x[WORK_COUNT] __attribute__((aligned(64)));
static real_t z[WORK_COUNT] __attribute__((aligned(64)));
#define GUARD_ELEMENTS (64/sizeof(real_t))
static struct { real_t before[GUARD_ELEMENTS]; real_t y[WORK_COUNT]; real_t after[GUARD_ELEMENTS]; } output __attribute__((aligned(64)));
static volatile uint64_t result_sink;
static uint64_t total_checks, failed;
#ifdef SINGLE_MODE
static const kernel_fn funcs[MODES]={SINGLE_MODE};
static const char *names[MODES+CONTROLS]={STR(SINGLE_MODE),"rdcycle","empty","copy"};
#elif N16_TRIM
static const kernel_fn funcs[MODES]={scalar_fast,direct_m1,direct_m2};
static const char *names[MODES]={"scalar_fast","direct_m1","direct_m2"};
#elif HARNESS_MODES == 8
static const kernel_fn funcs[MODES]={scalar_strict,scalar_fast,auto_m1,auto_m2,direct_m1,direct_m2,direct_m4,direct_m8};
static const char *names[MODES+CONTROLS]={"scalar_strict","scalar_fast","auto_m1","auto_m2","direct_m1","direct_m2","direct_m4","direct_m8","rdcycle","empty","copy"};
#elif HARNESS_MODES == 10
/* The compiler-generated LMUL4/LMUL8 columns follow the direct ones so the
   eight-mode prefix stays byte-comparable with the earlier runs. */
static const kernel_fn funcs[MODES]={scalar_strict,scalar_fast,auto_m1,auto_m2,direct_m1,direct_m2,direct_m4,direct_m8,auto_m4,auto_m8};
static const char *names[MODES+CONTROLS]={"scalar_strict","scalar_fast","auto_m1","auto_m2","direct_m1","direct_m2","direct_m4","direct_m8","auto_m4","auto_m8","rdcycle","empty","copy"};
#else
static const kernel_fn funcs[MODES]={scalar_strict,scalar_fast,auto_m1,auto_m2,direct_m1,direct_m2};
static const char *names[MODES+CONTROLS]={"scalar_strict","scalar_fast","auto_m1","auto_m2","direct_m1","direct_m2","rdcycle","empty","copy"};
#endif
/* Per-mode LMUL of the Nx/array entry; only used to probe VLMAX-adjacent
   lengths.  auto_* keep their compile-time -mrvv-max-lmul value. */
#ifdef SINGLE_MODE
static const unsigned mode_lmul[MODES]={SINGLE_LMUL};
#elif N16_TRIM
static const unsigned mode_lmul[MODES]={1,1,2};
#elif HARNESS_MODES == 8
static const unsigned mode_lmul[MODES]={1,1,1,2,1,2,4,8};
#elif HARNESS_MODES == 10
static const unsigned mode_lmul[MODES]={1,1,1,2,1,2,4,8,4,8};
#else
static const unsigned mode_lmul[MODES]={1,1,1,2,1,2};
#endif
struct record { uint64_t begin,end,checksum; unsigned n,k,mode,iteration,order; };
static struct record records[MAX_RECORDS];
static unsigned record_count;
static inline uint64_t cycle(void) {
    uint64_t c; __asm__ volatile("rdcycle %0" : "=r"(c) :: "memory"); return c;
}
static bits_t getbits(real_t x) { bits_t b; __builtin_memcpy(&b,&x,sizeof b); return b; }
static real_t frombits(bits_t b) { real_t x; __builtin_memcpy(&x,&b,sizeof x); return x; }
static uint64_t ordered(bits_t b) {
    const bits_t sign=(bits_t)1 << (8*sizeof(bits_t)-1);
    return b&sign ? (bits_t)~b : (b|sign);
}
static uint64_t checksum(size_t n) {
    uint64_t h=1469598103934665603ULL;
    for (size_t i=0;i<n;++i) h=(h^getbits(output.y[i]))*1099511628211ULL;
    result_sink=h; return h;
}
static void fail(const char *kind,unsigned mode,size_t n,size_t i,uint64_t got,uint64_t want) {
    if(failed++<8) printf("LIBMVEC_ERROR case=%s kind=%s mode=%s n=%u i=%u got=%llu want=%llu\n",
        CASE_NAME,kind,names[mode],(unsigned)n,(unsigned)i,(unsigned long long)got,(unsigned long long)want);
}
static void prepare_output(size_t n) {
    for(size_t i=0;i<GUARD_ELEMENTS;++i) output.before[i]=output.after[i]=frombits(GUARD);
    for(size_t i=0;i<WORK_COUNT;++i) output.y[i]=frombits(GUARD);
    (void)n;
}
static void verify(unsigned mode,size_t n) {
    for(size_t i=0;i<GUARD_ELEMENTS;++i) {
        ++total_checks;
        if(getbits(output.before[i])!=GUARD || getbits(output.after[i])!=GUARD)
            fail("outer-canary",mode,n,i,getbits(output.before[i]),GUARD);
    }
    for(size_t i=0;i<WORK_COUNT;++i) {
        ++total_checks; bits_t got=getbits(output.y[i]);
        if(i>=n) { if(got!=GUARD) fail("tail-canary",mode,n,i,got,GUARD); continue; }
#if !N16_TRIM
        if(mode==MODES) { if(got!=GUARD) fail("rdcycle-store",mode,n,i,got,GUARD); continue; }
        if(mode==MODES+1) { if(got!=GUARD) fail("empty-store",mode,n,i,got,GUARD); continue; }
        if(mode==MODES+2) { if(got!=input_bits[i]) fail("copy",mode,n,i,got,input_bits[i]); continue; }
#endif
        uint64_t a=ordered(got),b=ordered(reference_bits[i]);
        uint64_t distance=a>b?a-b:b-a;
        if(distance>ORACLE_LIMIT) fail("oracle",mode,n,i,got,reference_bits[i]);
    }
}
static void record_sample(unsigned mode,unsigned n,unsigned k,unsigned iteration,unsigned order) {
    prepare_output(n);
    uint64_t begin,end;
#if N16_TRIM
    {
        kernel_fn fn=funcs[mode];
#else
    if(mode==MODES) { begin=cycle(); end=cycle(); }
    else {
        kernel_fn fn=mode<MODES?funcs[mode]:(mode==MODES+1?empty_kernel:copy_kernel);
#endif
        begin=cycle();
        for(unsigned rr=0;rr<TIMING_REPEAT;++rr)
            for(unsigned j=0;j<k;++j) fn(n,x,z,output.y);
        end=cycle();
    }
    struct record *r=&records[record_count++];
    *r=(struct record){begin,end,checksum(n),n,k,mode,iteration,order};
    verify(mode,n);
}
/* Reporting is separate from all timed samples. Ten columns encode round order. */
__attribute__((noinline,noclone)) static int print_csv(uint64_t vlenb) {
    const unsigned width=MODES+CONTROLS, block=ROUNDS*(MODES+CONTROLS);
    if(record_count%block) { printf("LIBMVEC_ERROR case=%s kind=record-count\n",CASE_NAME); return 1; }
    for(unsigned base=0;base<record_count;base+=block)
        for(unsigned m=0;m<width;++m) {
            uint64_t hash=records[base+m].checksum;
            for(unsigned r=0;r<ROUNDS;++r) {
                unsigned order=(m+width-r%width)%width;
                const struct record *v=&records[base+r*width+order];
                if(v->mode!=m || v->iteration!=r || v->order!=order ||
                   v->n!=records[base].n || v->k!=records[base].k) {
                    printf("LIBMVEC_ERROR case=%s kind=record-layout\n",CASE_NAME); return 1;
                }
                if(v->checksum!=hash) fail("checksum-round",m,v->n,r,v->checksum,hash);
            }
        }
    if(failed) return 1;
    printf("LIBMVEC_CSV,3,%s,%llu,123456789,%s,%u\n",STR(FUNC),(unsigned long long)(vlenb*8),PROFILE_NAME,(unsigned)TIMING_REPEAT);
#ifdef SINGLE_MODE
    printf("M," STR(SINGLE_MODE) ",rdcycle,empty,copy\n");
#elif N16_TRIM
    printf("M,fast,direct1,direct2\n");
#elif HARNESS_MODES == 8
    printf("M,strict,fast,auto1,auto2,direct1,direct2,direct4,direct8,rdcycle,empty,copy\n");
#elif HARNESS_MODES == 10
    printf("M,strict,fast,auto1,auto2,direct1,direct2,direct4,direct8,auto4,auto8,rdcycle,empty,copy\n");
#else
    printf("M,strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy\n");
#endif
    printf("m,hash,b0,c0,b1,c1,b2,c2,b3,c3,b4,c4,b5,c5,b6,c6,b7,c7,b8,c8,b9,c9\n");
    for(unsigned base=0;base<record_count;base+=block) {
        printf("B,%u,%u\n",records[base].n,records[base].k);
        for(unsigned m=0;m<width;++m) {
            printf("D,%u,%llu",m,(unsigned long long)records[base+m].checksum);
            for(unsigned r=0;r<ROUNDS;++r) {
                unsigned order=(m+width-r%width)%width;
                const struct record *v=&records[base+r*width+order];
                printf(",%llu,%llu",(unsigned long long)v->begin,(unsigned long long)(v->end-v->begin));
            }
            printf("\n");
        }
    }
    printf("T,%llu,%llu,%u\n",(unsigned long long)total_checks,(unsigned long long)failed,record_count);
    printf("E,%s,pass\n",CASE_NAME);
    return 0;
}
int main(void) {
    uint64_t vlenb; __asm__ volatile("csrr %0, vlenb":"=r"(vlenb));
    if(vlenb<16) { printf("LIBMVEC_ERROR case=%s kind=vlen\n",CASE_NAME); return 1; }
    if(((uintptr_t)x| (uintptr_t)z | (uintptr_t)output.y)&63) {
        printf("LIBMVEC_ERROR case=%s kind=alignment\n",CASE_NAME); return 1;
    }
    for (size_t i=0;i<WORK_COUNT;++i) {
        x[i]=frombits(input_bits[i]);
#if BINARY_INPUT
        z[i]=frombits(second_bits[i]);
#endif
    }
    /* Array adapters: zero, short, odd and VLMAX-adjacent lengths, never Nx short calls. */
    const size_t lengths_check[]={0,1,2,3,4,5,7,8,9,15,16,17,31,32,33,127,128,129,1023,1024};
    for(unsigned m=0;m<MODES;++m) {
        for(size_t c=0;c<sizeof lengths_check/sizeof *lengths_check;++c) {
            size_t n=lengths_check[c]; if(n>WORK_COUNT) continue;
            prepare_output(n); funcs[m](n,x,z,output.y); verify(m,n);
        }
        const size_t vl=(vlenb/sizeof(real_t))*mode_lmul[m];
        for(size_t n=vl-1;n<=vl+1 && n<=WORK_COUNT;++n) {
            prepare_output(n); funcs[m](n,x,z,output.y); verify(m,n);
        }
    }
    if(failed) return 1;
#ifdef GRID_LENGTHS
    /* The build driver validates both lists (at most three entries each). */
    const unsigned lengths[]={GRID_LENGTHS};
    const unsigned batches[]={GRID_BATCHES};
#else
#if FORMAT_SMOKE
    const unsigned lengths[]={16};
    const unsigned batches[]={1};
#else
#if N16_ONLY
    const unsigned lengths[]={16};
#else
    const unsigned lengths[]={16,128,1024};
#endif
#if PILOT_BATCH
    const unsigned batches[]={1,4,16};
#else
    const unsigned batches[]={1,4};
#endif
#endif
#endif
    for(unsigned ni=0;ni<sizeof lengths/sizeof *lengths;++ni)
        for(unsigned ki=0;ki<sizeof batches/sizeof *batches;++ki)
            for(unsigned r=0;r<ROUNDS;++r)
                for(unsigned order=0;order<MODES+CONTROLS;++order) {
                    unsigned mode=(order+r)%(MODES+CONTROLS);
                    record_sample(mode,lengths[ni],batches[ki],r,order);
                }
    return print_csv(vlenb);
}
