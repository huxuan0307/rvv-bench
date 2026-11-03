#include "../../nolibc.h"
#include "config.h"

#ifndef LATENCY_RUNS
#ifdef RUNS
#define LATENCY_RUNS RUNS
#else
#define LATENCY_RUNS 8
#endif
#endif

typedef ux (*LatencyFunc)(void);

extern ux latency_count;
extern char *latency_names[];
extern ux latency_kinds[];
extern LatencyFunc latency_funcs_4[];
extern LatencyFunc latency_funcs_16[];
extern LatencyFunc latency_funcs_64[];
extern ux run_latency(LatencyFunc fn, ux vtype, ux avl, void *base);

static unsigned char heap[1 + MAX_MEM + MEM_ALIGN];

static char const *const kind_names[] = {
	"vector_direct",
	"vector_to_mask_bridge",
	"mask_to_scalar_bridge",
	"mask_to_vector_bridge",
	"load_to_address_use",
	"mask_direct",
};

static ux const chain_lengths[] = { 4, 16, 64 };

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

static LatencyFunc *
functions_for(ux chain_len)
{
	if (chain_len == 4)
		return latency_funcs_4;
	if (chain_len == 16)
		return latency_funcs_16;
	return latency_funcs_64;
}

static ux
body_multiplier(ux kind)
{
	if (kind == 1 || kind == 2 || kind == 3)
		return 2;
	if (kind == 4)
		return 3;
	return 1;
}

static ux
timed_body_insns(ux chain_len, ux kind)
{
	return chain_len * body_multiplier(kind) * UNROLL * LOOP;
}

static void
run_matrix(void *base, int ta, int ma)
{
	ux vtype = (2 << 3) | (!!ta << 6) | (!!ma << 7); /* e32,m1 */
	for (ux chain_idx = 0; chain_idx < sizeof(chain_lengths)/sizeof(chain_lengths[0]); ++chain_idx) {
		ux chain_len = chain_lengths[chain_idx];
		LatencyFunc *fns = functions_for(chain_len);
		for (ux i = 0; i < latency_count; ++i) {
			ux samples[LATENCY_RUNS];
			for (ux sample = 0; sample < LATENCY_RUNS; ++sample)
				samples[sample] = run_latency(fns[i], vtype, 0, base);

			for (ux sample = 0; sample < LATENCY_RUNS; ++sample) {
				print_csv_field(latency_names[i]);
				print(",")(s,kind_names[latency_kinds[i]])(",e32,m1,VLMAX,")
					(s,ta ? "ta" : "tu")(",")(s,ma ? "ma" : "mu")
					(",")(u,WARMUP)(",")(u,UNROLL)(",")(u,LOOP)
					(",")(u,chain_len)(",")(u,sample)(",")(u,samples[sample])
					(",")(u,timed_body_insns(chain_len, latency_kinds[i]))("\n");
			}
		}
	}
}

int
main(void)
{
	uintptr_t raw = (uintptr_t)heap;
	void *base = (void*)((raw + MEM_ALIGN - 1) & ~(MEM_ALIGN - 1));
	memset(base, 0, MAX_MEM);

	print("instruction,mode,sew,lmul,vl,tail_policy,mask_policy,warmup,unroll,loop,chain_len,sample,cycles,body_insns\n");
	run_matrix(base, 1, 1);
	run_matrix(base, 0, 0);
	print_flush();
	return 0;
}
