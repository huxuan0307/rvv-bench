#include "../../nolibc.h"
#include "config.h"

static ux seed = 123456;

typedef ux (*BenchFunc)(void);
extern void *aligned, *misaligned, *memory_base;
extern ux bench_is_memory;
extern char bench_names;
extern ux bench_types;
extern BenchFunc bench_mf8, bench_mf4, bench_mf2, bench_m1, bench_m2, bench_m4, bench_m8;
static BenchFunc *benches[] = { &bench_mf8, &bench_mf4, &bench_mf2, &bench_m1, &bench_m2, &bench_m4, &bench_m8 };

extern ux run_bench(ux (*bench)(void), ux type, ux vl, ux seed);


static int
compare_ux(void const *a, void const *b)
{
	return (*(ux*)a > *(ux*)b) - (*(ux*)a < *(ux*)b);
}

static void
print_csv_field(char const *s)
{
	print("\"");
	char const *start = s;
	for (; *s; ++s) {
		if (*s == '"') {
			print_raw(start, s - start);
			print("\"\"");
			start = s + 1;
		}
	}
	print_raw(start, s - start);
	print("\"");
}

static void
print_csv_header(void)
{
	char const *lmuls[] = { "mf8", "mf4", "mf2", "m1", "m2", "m4", "m8" };
	print("instruction,vl,tail_policy,mask_policy,address_alignment");
	for (ux sew = 8; sew <= 64; sew <<= 1)
	for (ux lmul_idx = 0; lmul_idx < 7; ++lmul_idx)
		print(",e")(u,sew)(s,lmuls[lmul_idx]);
	print("\n");
}


static void
run_all_types(char const *name, ux bIdx, ux vl, int ta, int ma, char const *address_alignment)
{
	ux arr[RUNS];

	ux mask = bIdx[&bench_types];

	ux lmuls[] = { 5, 6, 7, 0, 1, 2, 3 };

	if (mask == T_GSH || mask == T_AES || mask == T_AES2 || mask == T_SHA)
		vl = 4;

	print_csv_field(name);
	print(",");
	if (vl == 0)
		print("VLMAX");
	else
		print(u,vl);
	print(",")(s,ta ? "ta" : "tu")(",")(s,ma ? "ma" : "mu")
	     (",")(s,address_alignment);

	for (ux sew = 0; sew < 4; ++sew)
	for (ux lmul_idx = 0; lmul_idx < 7; ++lmul_idx) {
		ux lmul = lmuls[lmul_idx];
		ux vtype = lmul | (sew<<3) | (!!ta << 6) | (!!ma << 7);

		if (!(mask >> (lmul_idx*4 + sew) & 1)) {
			print(",");
			continue;
		}

		ux lmul_val = 1 << lmul_idx; // fixed-point, denum 8
		ux sew_val = 1 << (sew + 3);
		// > For a given supported fractional LMUL setting,
		// > implementations must support SEW settings between SEWMIN
		// > and LMUL * ELEN, inclusive.
		if (sew_val * 8 > lmul_val * __riscv_v_elen) {
			print(",");
			continue;
		}

		ux emul = lmul_idx;
		/* Unit/strided memory data groups use EEW/SEW * LMUL.
		 * Segment and whole-register forms keep their separate group rules. */
		char const *width = 0;
		if (name[0] == 'v' && (name[1] == 'l' || name[1] == 's')) {
			if (name[2] == 'e') width = name + 3;
			else if (name[2] == 's' && name[3] == 'e') width = name + 4;
		}
		if (width && *width >= '0' && *width <= '9') {
			ux eew = 0;
			while (*width >= '0' && *width <= '9')
				eew = eew * 10 + (*width++ - '0');
			ux eew_log = 0;
			for (ux bits = eew; bits > 8; bits >>= 1) ++eew_log;
			int group = (int)lmul_idx + (int)eew_log - (int)sew;
			if (group < 0 || group > 6) { print(","); continue; }
			emul = (ux)group;
		}
		if (mask == T_W || mask == T_FW || mask == T_N || mask == T_FN)
			emul += 1;
		if (mask == T_ei16 && sew == 0)
			emul = emul < 7 ? emul+1 : 7;
		if (mask == T_m1)
			emul = 4; // m2
		BenchFunc bench = benches[emul][bIdx];

		for (ux i = 0; i < RUNS; ++i) {
			arr[i] = run_bench(bench, vtype, vl, seed);
			if (~arr[i] == 0) goto skip;
			seed = seed*7 + 13;
		}
#if RUNS > 4
		qsort(arr, RUNS, sizeof *arr, compare_ux);
		ux sum = 0, count = 0;
		for (ux i = RUNS * 0.2f; i < RUNS * 0.8f; ++i, ++count)
			sum += arr[i];
#else
		ux sum = 0, count = RUNS;
		for (ux i = 0; i < RUNS; ++i)
			sum += arr[i];
#endif
		print(",")(fn,2,sum * 1.0f/(UNROLL*LOOP*count*8));
		continue;
skip:
		print(",");
	}
	print("\n")(flush,);
}

#if __STDC_HOSTED__ && !defined(CUSTOM_HOST)
# include <stdlib.h>
#else
static unsigned char heap[1 + MAX_MEM + MEM_ALIGN];
#endif

int
main(void)
{
	size_t x;
	seed += rv_cycles();
	seed ^= (uintptr_t)&x;

#if __STDC_HOSTED__ && !defined(CUSTOM_HOST)
	aligned = malloc(MAX_MEM + MEM_ALIGN);
#else
	aligned = heap;
#endif
	aligned = (unsigned char*)(((uintptr_t)aligned + MEM_ALIGN-1) & ~(MEM_ALIGN-1));
	memset(aligned, 33, MAX_MEM);
	/* +15 selects address % 16 == 15 and deliberately breaks natural
	 * alignment for EEW > 8. Targets must support misaligned accesses. */
	misaligned = (unsigned char*)aligned + 15;
	memory_base = aligned;


	print_csv_header();
	ux vlarr[] = { 0, 1 };
	for (ux i = 0; i < 2; ++i) {
		for (ux j = 4; j--; ) {
			char const *name = &bench_names;
			for (ux bIdx = 0; *name; ++bIdx) {
				if ((&bench_is_memory)[bIdx]) {
					memory_base = aligned;
					run_all_types(name, bIdx, vlarr[i], j >> 1, j & 1, "aligned16");
					memory_base = misaligned;
					run_all_types(name, bIdx, vlarr[i], j >> 1, j & 1, "unaligned16");
				} else {
					run_all_types(name, bIdx, vlarr[i], j >> 1, j & 1, "n/a");
				}
				while (*name++);
			}
		}
	}
	print_flush();
	return 0;
}
