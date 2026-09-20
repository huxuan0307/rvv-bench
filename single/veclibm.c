#include <riscv_vector.h>
#include <math.h>
#include <stdio.h>

#include "../nolibc.h"

#ifndef VECLIBM_N
#define VECLIBM_N (1024*128)
#endif

/* Bare-metal: enable vector extension in mstatus */
void reset_vector(void) {
	unsigned long mstatus;
	__asm__ volatile ("csrr %0, mstatus" : "=r"(mstatus));
	mstatus |= (0x3UL << 9);  /* VS = Dirty, enables vector instructions */
	mstatus |= (0x3UL << 13); /* FS = Dirty, enables FPU */
	__asm__ volatile ("csrw mstatus, %0" :: "r"(mstatus));
}

/* Bare-metal stubs for newlib libm dependencies */
int   __errno;
struct _reent;
static char impure_storage[1024];
struct _reent *_impure_ptr = (struct _reent *)impure_storage;

#ifndef CUSTOM_HOST
static double veclibm_in[VECLIBM_N];
static double veclibm_out[VECLIBM_N];
#endif

static void
rvvlm_sqrt(size_t x_len, const double *x, double *y)
{
    for (size_t vl; x_len > 0; x_len -= vl, x += vl, y += vl) {
        vl = __riscv_vsetvl_e64m8(x_len);
        vfloat64m8_t v = __riscv_vle64_v_f64m8(x, vl);
        __riscv_vse64(y, __riscv_vfsqrt(v, vl), vl);
    }
}

#define APPLY(X) \
X(exp) X(exp2) X(expm1) X(log) X(log10) X(log2) X(log1p) \
X(sqrt) X(cbrt) \
X(sin) X(cos) X(tan) X(asin) X(acos) X(atan) \
X(sinh) X(cosh) X(tanh) X(asinh) X(acosh) X(atanh) \
X(erf) X(erfc) X(tgamma) X(lgamma)

/* -DTEST_FUNC=exp  selects a single function for standalone testing */
#ifdef TEST_FUNC
#undef APPLY
#define APPLY_(X, f) X(f)
#define APPLY(X) APPLY_(X, TEST_FUNC)
#endif

#define DECLARE(f) void rvvlm_##f(size_t x_len, const double *x, double *y);
APPLY(DECLARE)

#define DEFINE(f) \
	static void lm_##f(size_t x_len, const double *x, double *y) { \
		for (size_t i = 0; i < x_len; ++i) y[i] = f(x[i]); \
	}
APPLY(DEFINE)
struct Func {
	void (*rvvlm)(size_t, const double*, double*);
	void (*lm)(size_t, const double*, double*);
	const char *name;
};

struct Func funcs[] = {
#define ENTRY(f) { rvvlm_##f, lm_##f, #f },
APPLY(ENTRY)
};

int
main(void)
{
	reset_vector();
#ifdef CUSTOM_HOST
	double *in = malloc(VECLIBM_N*sizeof *in), *out = malloc(VECLIBM_N*sizeof *out);
#else
	double *in  = veclibm_in;
	double *out = veclibm_out;
#endif
	URand r = {123, (uintptr_t)&in, (uintptr_t)&out};

	for (size_t i = 0; i < VECLIBM_N; ++i)
		in[i] = (urand(&r) >> (64 - 53)) * (1.0 / (1ull << 53));

	for (size_t i = 0; i < sizeof funcs / sizeof *funcs; ++i) {
		size_t beg, end;
		struct Func f = funcs[i];
		printf("%s libm: ", f.name);
		for (size_t i = 0; i < 3; ++i) {
			__asm__ volatile ("csrr %0, cycle" : "=r"(beg));
			f.lm(VECLIBM_N, in, out);
			__asm__ volatile ("csrr %0, cycle" : "=r"(end));
			printf(" %f", ((double)VECLIBM_N) / (end-beg));
		}
		printf(" elements/cycle\n%s rvvlm:", f.name);
		for (size_t i = 0; i < 3; ++i) {
			__asm__ volatile ("csrr %0, cycle" : "=r"(beg));
			f.rvvlm(VECLIBM_N, in, out);
			__asm__ volatile ("csrr %0, cycle" : "=r"(end));
			printf(" %f", ((double)VECLIBM_N) / (end-beg));
		}
		printf(" elements/cycle\n");
	}
#ifdef CUSTOM_HOST
	free(in);
	free(out);
#endif
	return 0;
}

