#define main chacha20_benchmark_entry
#include "chacha20.c"
#undef main

#define PROFILE_MAX 1048576

static uint8_t profile_input[PROFILE_MAX] __attribute__((aligned(16)));
static uint8_t profile_output[PROFILE_MAX] __attribute__((aligned(16)));
static uint8_t profile_expected[PROFILE_MAX] __attribute__((aligned(16)));

static void
empty_stream(uint8_t *out, const uint8_t *in, size_t n,
		const uint8_t key[32], const uint8_t nonce[12], uint32_t counter)
{
	__asm__ volatile("" ::: "memory");
}

__attribute__((noinline, noclone))
static ux
measure_stream(Func *f, size_t n)
{
	ux begin, end;
	__asm__ volatile("rdinstret %0" : "=r"(begin) :: "memory");
	f(profile_output, profile_input, n, key, nonce, counter);
	__asm__ volatile("rdinstret %0" : "=r"(end) :: "memory");
	return end - begin;
}

int
main(void)
{
	static const size_t sizes[] = {
		1, 63, 64, 65, 127, 128, 255, 256, 257, 511, 512,
		1024, 4096, 65536, 262144, PROFILE_MAX,
	};
	mem = (uint8_t*)(((uintptr_t)_heap_start + MEM_ALIGN-1) & ~(MEM_ALIGN-1));
	init();
	URand inputs = { 123, 456, 789 };
	memrand(&inputs, profile_input, sizeof profile_input);
	ux vlenb;
	__asm__ volatile("csrr %0,vlenb" : "=r"(vlenb));
	print("PROFILE,impl,bytes,raw,empty,body,vlen_bits\n")(flush,);
	for (size_t s = 0; s < ARR_LEN(sizes); ++s) {
		size_t n = sizes[s];
		ux empty = measure_stream(empty_stream, n);
		boring_chacha20(profile_expected, profile_input, n, key, nonce, counter);
		ux original = 0, xor_only = 0, optimized = 0;
		for (size_t j = 0; j < ARR_LEN(impls); ++j) {
			Func *f = impls[j].func;
			ux raw = measure_stream(f, n);
			ux body = raw - empty + 1;
			for (size_t i = 0; i < n; ++i) {
				if (profile_output[i] != profile_expected[i]) {
					print("ERROR: profile output ")(s,impls[j].name)(" n=")(u,n)("\n")(flush,);
					return 1;
				}
			}
			if (f == chacha20_riscv) original = body;
			if (f == chacha20_riscv_xor) xor_only = body;
			if (f == chacha20_riscv_m4) optimized = body;
			print("PROFILE,")(s,impls[j].name)(",")(u,n)(",")(u,raw)
				(",")(u,empty)(",")(u,body)(",")(u,vlenb * 8)("\n")(flush,);
		}
		if (n >= 256 && !(optimized < xor_only && xor_only < original)) {
			print("ERROR: instruction regression n=")(u,n)("\n")(flush,);
			return 1;
		}
	}
	print("OK: chacha20 instruction profile\n")(flush,);
	return 0;
}
