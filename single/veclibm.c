/* RVV throughput smoke tests; accuracy checks live in veclibm-accuracy/. */
#include <riscv_vector.h>
#include "../autovec/av_runtime.h"

#ifndef VECLIBM_N
#define VECLIBM_N 4096
#endif
#ifndef VECLIBM_REPEATS
#define VECLIBM_REPEATS 3
#endif
#if VECLIBM_N < 1 || VECLIBM_REPEATS < 1
#error "VECLIBM_N and VECLIBM_REPEATS must be positive"
#endif

static double veclibm_in[VECLIBM_N];
static double veclibm_out[VECLIBM_N];
static volatile uint64_t result_sink;

static uint64_t instret(void)
{
    uint64_t n;
    __asm__ volatile("rdinstret %0" : "=r"(n) :: "memory");
    return n;
}

static void rvvlm_sqrt(size_t n, const double *x, double *y)
{
    for (size_t vl; n; n -= vl, x += vl, y += vl) {
        vl = __riscv_vsetvl_e64m8(n);
        __riscv_vse64(y, __riscv_vfsqrt(__riscv_vle64_v_f64m8(x, vl), vl), vl);
    }
}

#define APPLY(X) \
X(exp) X(exp2) X(expm1) X(log) X(log10) X(log2) X(log1p) \
X(sqrt) X(cbrt) \
X(sin) X(cos) X(tan) X(asin) X(acos) X(atan) \
X(sinh) X(cosh) X(tanh) X(asinh) X(acosh) X(atanh) \
X(erf) X(erfc) X(tgamma) X(lgamma)

#ifdef TEST_FUNC
#undef APPLY
#define APPLY_(X, f) X(f)
#define APPLY(X) APPLY_(X, TEST_FUNC)
#endif

#define DECLARE(f) void rvvlm_##f(size_t, const double *, double *);
APPLY(DECLARE)

/* The scalar baseline is deliberately not auto-vectorized.  Both paths use
 * real libm / veclibm, never lm_stubs.c. */
#define DEFINE(f) \
    __attribute__((optimize("no-tree-vectorize"), noinline)) \
    static void lm_##f(size_t n, const double *x, double *y) { \
        for (size_t i = 0; i < n; ++i) y[i] = f(x[i]); \
    }
APPLY(DEFINE)

struct Func {
    void (*rvvlm)(size_t, const double *, double *);
    void (*lm)(size_t, const double *, double *);
    const char *name;
};
static struct Func funcs[] = {
#define ENTRY(f) {rvvlm_##f, lm_##f, #f},
APPLY(ENTRY)
};

/* Observe every output outside the timed region, including non-finite values.
 * This is a liveness/smoke check, not an ULP accuracy test. */
static uint64_t checksum(void)
{
    uint64_t hash = UINT64_C(1469598103934665603);
    for (size_t i = 0; i < VECLIBM_N; ++i) {
        uint64_t bits;
        memcpy(&bits, &veclibm_out[i], sizeof bits);
        hash = (hash ^ bits) * UINT64_C(1099511628211);
    }
    result_sink = hash;
    return hash;
}

int main(void)
{
    uint64_t vlenb;
    __asm__ volatile("csrr %0, vlenb" : "=r"(vlenb));
    printf("VECLIBM_CONFIG n=%u repeats=%u vlen_bits=%llu baseline=static-libm\n",
           VECLIBM_N, VECLIBM_REPEATS, (unsigned long long)(8 * vlenb));
    for (size_t fidx = 0; fidx < sizeof funcs / sizeof *funcs; ++fidx) {
        struct Func f = funcs[fidx];
        uint64_t state = UINT64_C(123456789);
        for (size_t i = 0; i < VECLIBM_N; ++i) {
            state = state * UINT64_C(6364136223846793005) + 1;
            /* Finite, deterministic, valid for every function's real domain. */
            double x = 0.125 + (state >> 11) * (0.75 / 9007199254740992.0);
            veclibm_in[i] = strcmp(f.name, "acosh") ? x : x + 1.0;
        }
        for (unsigned mode = 0; mode < 2; ++mode) {
            void (*fn)(size_t, const double *, double *) = mode ? f.rvvlm : f.lm;
            for (unsigned rep = 0; rep < VECLIBM_REPEATS; ++rep) {
                uint64_t ibeg = instret(), cbeg = av_ticks();
                fn(VECLIBM_N, veclibm_in, veclibm_out);
                uint64_t cycles = av_ticks() - cbeg, insns = instret() - ibeg;
                printf("VECLIBM_SAMPLE function=%s mode=%s sample=%u n=%u cycles=%llu insns=%llu checksum=%llu\n",
                       f.name, mode ? "rvvlm" : "libm", rep, VECLIBM_N,
                       (unsigned long long)cycles, (unsigned long long)insns,
                       (unsigned long long)checksum());
            }
        }
        printf("VECLIBM_FUNCTION_COMPLETE function=%s\n", f.name);
    }
    printf("VECLIBM_COMPLETE functions=%u\n", (unsigned)(sizeof funcs / sizeof *funcs));
    return 0;
}
