#include "config.h"
#include "../nolibc.h"

#ifndef BENCH_NEXT
#  define BENCH_NEXT(previous, current) NEXT(previous, current)
#endif

#define MX(f,F) f(F##_m1) f(F##_m2) f(F##_m4) f(F##_m8)
#define STR(x) STR_(x)
#define STR_(x) #x

#if defined(__clang__) || defined(__GNUC__) || defined(__INTEL_COMPILER)

# define BENCH_CLOBBER() ({__asm__ volatile("":::"memory");})
# define BENCH_VOLATILE(x) ({__asm__ volatile("" : "+g"(x) : "g"(x) : "memory");})
# define BENCH_VOLATILE_REG(x) ({__asm__ volatile("" : "+r"(x) : "r"(x) : "memory");})
# define BENCH_VOLATILE_MEM(x) ({__asm__ volatile("" : "+m"(x) : "m"(x) : "memory");})

#define BENCH_MAY_ALIAS __attribute__((__may_alias__))

#else

# define BENCH_CLOBBER() 1
# define BENCH_CLOBBER_WITH(x) (bench__use_ptr(&(x)), BENCH_CLOBBER())
# define BENCH_CLOBBER_WITH_REG(x) (bench__use_ptr(&(x)), BENCH_CLOBBER())
# define BENCH_CLOBBER_WITH_MEM(x) (bench__use_ptr(&(x)), BENCH_CLOBBER())
static void bench_use_ptr(char const volatile *x) {}

#define BENCH_MAY_ALIAS

#endif


static int
compare_ux(void const *a, void const *b)
{
	ux A = *(ux*)a, B = *(ux*)b;
	return A < B ? -1 : A > B ? 1 : 0;
}

static URand randState = { 123, 456, 789 };
static ux bench_urand(void) { return urand(&randState); }
static float bench_urandf(void) { return urandf(&randState); }
static void bench_memrand(void *ptr, size_t n) { return memrand(&randState, ptr, n); }

typedef struct {
	char const *name; void *func; int skipCheck;
} Impl;
typedef struct {
	Impl *impls;
	size_t nImpls;
	size_t N;
	char const *name;
	ux (*func)(void *, size_t);
} Bench;

static unsigned char *mem = 0;

void bench_main(void);
ux checksum(size_t n);
void init(void);

#define MEM_ALIGN 4096
#if __STDC_HOSTED__ && !defined(CUSTOM_HOST)
# include <stdlib.h>
#else
extern unsigned char _heap_start[];
#endif


int
main(void)
{

#if __STDC_HOSTED__ && !defined(CUSTOM_HOST)
	void *ptr = mem = malloc(MAX_MEM + MEM_ALIGN);
#else
	mem = _heap_start;
#endif
	mem = (unsigned char*)(((uintptr_t)mem + MEM_ALIGN-1) & ~(MEM_ALIGN-1));

	size_t x;
	randState.x ^= rv_cycles()*7;
	randState.y += rv_cycles() ^ ((uintptr_t)&x + 666*(uintptr_t)mem);

	/* initialize memory */
	bench_memrand(mem, MAX_MEM);

	init();
	bench_main();
	print_flush();
#if __STDC_HOSTED__ && !defined(CUSTOM_HOST)
	free(ptr);
#endif
	return 0;
}

static int
bench_streq(char const *a, char const *b)
{
	while (*a && *a == *b) ++a, ++b;
	return *a == *b;
}

static Impl *
find_impl(Impl *impls, size_t n, char const *name)
{
	for (size_t i = 0; i < n; ++i)
		if (bench_streq(impls[i].name, name)) return impls + i;
	return 0;
}

static int
impl_selected(Impl const *i)
{
#ifdef ONLY_IMPL
	return bench_streq(i->name, STR(ONLY_IMPL));
#else
	(void)i;
	return 1;
#endif
}

static size_t
bench_next_size(size_t *previous, size_t current)
{
	size_t next = BENCH_NEXT(*previous, current);
	*previous = current;
	return next;
}

static size_t
bench_time(size_t n, Impl impl, Bench bench, ux *arr)
{
	ux total = 0;
	size_t nrep = 0;
	for (; nrep < MAX_REPEATS; ++nrep) {
		total += arr[nrep] = bench.func(impl.func, n);
		if (nrep + 1 >= MIN_REPEATS && total > STOP_CYCLES) {
			++nrep;
			break;
		}
	}
	return nrep;
}

static int
bench_check_one(Impl *sel, Impl *ref, Bench *b, size_t n)
{
	if (!sel || !ref)
		return 1;
	if (sel == ref || sel->skipCheck) {
		(void)b->func(sel->func, n);
		return 1;
	}
	URand seed = randState;
	(void)b->func(sel->func, n);
	ux si = checksum(n);
	randState = seed;
	(void)b->func(ref->func, n);
	ux s0 = checksum(n);
	if (si != s0) {
		print("ERROR: ")(s,sel->name)(" in ")(s,b->name)(" at ")(u,n)(flush,);
		return 0;
	}
	return 1;
}

static void
bench_run(Bench *benches, size_t nBenches)
{
	for (Bench *b = benches; b != benches + nBenches; ++b) {
#ifdef ONLY_IMPL
		if (!find_impl(b->impls, b->nImpls, STR(ONLY_IMPL))) {
			print("ERROR: missing impl ")(s,STR(ONLY_IMPL))(flush,);
			return;
		}
#endif

#if CHECK
		Impl *ref = find_impl(b->impls, b->nImpls, STR(REF_IMPL));
		if (!ref) {
			print("ERROR: missing ref ")(s,STR(REF_IMPL))(flush,);
			return;
		}
		print("check: \"")(s,b->name)("\"\n")(flush,);
		for (size_t previous = 1, n = 1; n < b->N;
		     n = bench_next_size(&previous, n)) {
			for (Impl *i = b->impls; i != b->impls + b->nImpls; ++i) {
				if (!impl_selected(i)) continue;
				if (!bench_check_one(i, ref, b, n)) return;
			}
		}
		print("OK: \"")(s,b->name)("\"\n")(flush,);
#else
		print("{\ntitle: \"")(s,b->name)("\",\n");
		print("labels: [\"0\",");
		for (Impl *i = b->impls; i != b->impls + b->nImpls; ++i)
			if (impl_selected(i)) print("\"")(s,i->name)("\",");
		print("],\n");

		print("data: [\n[");
		for (size_t previous = 1, n = 1; n < b->N;
		     n = bench_next_size(&previous, n))
			print(u,n)(",");
		print("],\n")(flush,);

		print("repeats: [\n");
		for (Impl *i = b->impls; i != b->impls + b->nImpls; ++i) {
			if (!impl_selected(i)) continue;
			static ux arr[MAX_REPEATS];
			print("[\n");
			for (size_t previous = 1, n = 1; n < b->N;
			     n = bench_next_size(&previous, n)) {
				size_t nrep = bench_time(n, *i, *b, arr);
				print("[");
				for (size_t r = 0; r < nrep; ++r)
					print(u,arr[r])(",");
				print("],\n")(flush,);
			}
			print("],\n");
		}
		print("]\n},\n");
#endif
	}
}

#define TIME \
	for (ux beg = rv_cycles(), _once = 1; _once; \
	       _cycles += rv_cycles() - beg, _once = 0)

#define BENCH_BEG(name) \
	ux bench_##name(void *_func, size_t n) { \
		Func *f = _func; ux _cycles = 0;
#define BENCH_END return _cycles; }

#define BENCH(impls, ...) { impls, ARR_LEN(impls), __VA_ARGS__ }

#define BENCH_MAIN(benches) \
	void bench_main(void) { \
		bench_run(benches, ARR_LEN(benches)); \
	}
