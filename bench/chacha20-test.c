#define main chacha20_bench_main
#include "chacha20.c"
#undef main

#define TEST_MAX 1025
#define GUARD 16

static int
bytes_equal(const uint8_t *a, const uint8_t *b, size_t n)
{
	for (size_t i = 0; i < n; ++i)
		if (a[i] != b[i]) return 0;
	return 1;
}

static void
fail(const char *what, const char *impl, size_t n, uint32_t initial_counter)
{
	print("ERROR: chacha20 ")(s,what)(" impl=")(s,impl)
		(" n=")(u,n)(" counter=")(u,initial_counter)("\n")(flush,);
	exit(EXIT_FAILURE);
}

static void
fail_direct(const char *lhs, const char *rhs, const uint8_t *a,
		const uint8_t *b, size_t n, uint32_t initial_counter)
{
	size_t offset = 0;
	while (offset < n && a[offset] == b[offset]) ++offset;
	print("ERROR: chacha20 direct mismatch lhs=")(s,lhs)(" rhs=")(s,rhs)
		(" n=")(u,n)(" counter=")(u,initial_counter)
		(" offset=")(u,offset)("\n")(flush,);
	exit(EXIT_FAILURE);
}

static void
check_inputs(void)
{
	uint8_t saved_key[32], saved_nonce[12];
	uint8_t *input = mem + MAX_MEM/2 + 16;
	init();
	memcpy(saved_key, key, sizeof key);
	memcpy(saved_nonce, nonce, sizeof nonce);
	memcpy(mem, input, MAX_DEST);
	randState = (URand){ 1, 2, 3 };
	memset(input, 0, MAX_DEST);
	memset(key, 0, sizeof key);
	memset(nonce, 0, sizeof nonce);
	counter = 7;
	init();
	if (!bytes_equal(key, saved_key, sizeof key) ||
	    !bytes_equal(nonce, saved_nonce, sizeof nonce) ||
	    !bytes_equal(input, mem, MAX_DEST) || counter != 0)
		fail("non-deterministic inputs", "all", MAX_DEST, counter);
}

static size_t
check_streams(void)
{
	static const size_t sizes[] = {
		0, 1, 2, 3, 15, 16, 31, 32, 63, 64, 65, 127, 128, 129,
		191, 192, 193, 255, 256, 257, 383, 384, 385,
		511, 512, 513, 1023, 1024, TEST_MAX,
	};
	static const uint32_t counters[] = { 0, 7, 0x12345678, 0xffffffe0 };
	static uint8_t input[TEST_MAX] __attribute__((aligned(16)));
	static uint8_t saved_input[TEST_MAX];
	static uint8_t expected[TEST_MAX] __attribute__((aligned(16)));
	static uint8_t output[GUARD + TEST_MAX + GUARD] __attribute__((aligned(16)));
	uint8_t test_key[32] __attribute__((aligned(16)));
	uint8_t test_nonce[12] __attribute__((aligned(16)));
	uint8_t saved_key[32], saved_nonce[12];
	size_t cases = 0;

	for (unsigned pattern = 0; pattern < 2; ++pattern) {
		for (size_t i = 0; i < sizeof test_key; ++i)
			test_key[i] = saved_key[i] = (uint8_t)(i * 13 + pattern * 17);
		for (size_t i = 0; i < sizeof test_nonce; ++i)
			test_nonce[i] = saved_nonce[i] = (uint8_t)(i * 11 + pattern * 23);
		for (size_t i = 0; i < sizeof input; ++i)
			input[i] = saved_input[i] = pattern ? (uint8_t)(i * 37 + 11) : 0;
		for (size_t c = 0; c < ARR_LEN(counters); ++c) {
			for (size_t s = 0; s < ARR_LEN(sizes); ++s) {
				size_t n = sizes[s];
				boring_chacha20(expected, input, n, test_key, test_nonce, counters[c]);
				for (size_t j = 0; j < ARR_LEN(impls); ++j) {
					Func *f = impls[j].func;
					memset(output, 0xa5, sizeof output);
					f(output + GUARD, input, n, test_key, test_nonce, counters[c]);
					if (!bytes_equal(output + GUARD, expected, n))
						fail("output mismatch", impls[j].name, n, counters[c]);
					for (size_t i = 0; i < sizeof output; ++i)
						if ((i < GUARD || i >= GUARD + n) && output[i] != 0xa5)
							fail("guard overwrite", impls[j].name, n, counters[c]);
					if (!bytes_equal(input, saved_input, sizeof input) ||
					    !bytes_equal(test_key, saved_key, sizeof test_key) ||
					    !bytes_equal(test_nonce, saved_nonce, sizeof test_nonce))
						fail("input overwrite", impls[j].name, n, counters[c]);
					++cases;
				}
			}
		}
	}
	return cases;
}

static size_t
check_three_way(void)
{
	static const size_t sizes[] = {
		0, 1, 63, 64, 65, 127, 128, 255, 256, 257,
		511, 512, 513, 1024, 4096, 65536, 1048576,
	};
	static const uint32_t counters[] = {
		0, 7, 0x12345678, 0xfffffffc, 0xffffffe0,
	};
	static uint8_t input[1048576] __attribute__((aligned(16)));
	static uint8_t output_riscv[1048576] __attribute__((aligned(16)));
	static uint8_t output_rvv[1048576] __attribute__((aligned(16)));
	static uint8_t output_m4[1048576] __attribute__((aligned(16)));
	uint8_t test_key[32] __attribute__((aligned(16)));
	uint8_t test_nonce[12] __attribute__((aligned(16)));
	size_t cases = 0;

	for (unsigned pattern = 0; pattern < 2; ++pattern) {
		for (size_t i = 0; i < sizeof test_key; ++i)
			test_key[i] = (uint8_t)(i * 13 + pattern * 17 + 1);
		for (size_t i = 0; i < sizeof test_nonce; ++i)
			test_nonce[i] = (uint8_t)(i * 11 + pattern * 23 + 3);
		for (size_t i = 0; i < sizeof input; ++i)
			input[i] = pattern ? (uint8_t)(i * 37 + 11) : 0;

		for (size_t c = 0; c < ARR_LEN(counters); ++c) {
			for (size_t s = 0; s < ARR_LEN(sizes); ++s) {
				size_t n = sizes[s];
				uint32_t initial_counter = counters[c];

				chacha20_riscv(output_riscv, input, n, test_key,
						test_nonce, initial_counter);
				vector_chacha20(output_rvv, input, n, test_key,
						test_nonce, initial_counter);
				chacha20_riscv_m4(output_m4, input, n, test_key,
						test_nonce, initial_counter);

				if (!bytes_equal(output_riscv, output_rvv, n))
					fail_direct("riscv", "rvv", output_riscv,
							output_rvv, n, initial_counter);
				if (!bytes_equal(output_riscv, output_m4, n))
					fail_direct("riscv", "riscv_m4", output_riscv,
							output_m4, n, initial_counter);
				++cases;
			}
		}
	}
	return cases;
}

static size_t
check_rfc(void)
{
	/* RFC 8439 section 2.3.2 and appendix A.1, test vector 4. */
	static const uint8_t answers[2][64] = {
		{ 0x10,0xf1,0xe7,0xe4,0xd1,0x3b,0x59,0x15,0x50,0x0f,0xdd,0x1f,0xa3,0x20,0x71,0xc4,
		  0xc7,0xd1,0xf4,0xc7,0x33,0xc0,0x68,0x03,0x04,0x22,0xaa,0x9a,0xc3,0xd4,0x6c,0x4e,
		  0xd2,0x82,0x64,0x46,0x07,0x9f,0xaa,0x09,0x14,0xc2,0xd7,0x05,0xd9,0x8b,0x02,0xa2,
		  0xb5,0x12,0x9c,0xd1,0xde,0x16,0x4e,0xb9,0xcb,0xd0,0x83,0xe8,0xa2,0x50,0x3c,0x4e },
		{ 0x72,0xd5,0x4d,0xfb,0xf1,0x2e,0xc4,0x4b,0x36,0x26,0x92,0xdf,0x94,0x13,0x7f,0x32,
		  0x8f,0xea,0x8d,0xa7,0x39,0x90,0x26,0x5e,0xc1,0xbb,0xbe,0xa1,0xae,0x9a,0xf0,0xca,
		  0x13,0xb2,0x5a,0xa2,0x6c,0xb4,0xa6,0x48,0xcb,0x9b,0x9d,0x1b,0xe6,0x5b,0x2c,0x09,
		  0x24,0xa6,0x6c,0x54,0xd5,0x45,0xec,0x1b,0x73,0x74,0xf4,0x87,0x2e,0x99,0xf0,0x96 },
	};
	uint8_t k[32] __attribute__((aligned(16)));
	uint8_t iv[12] __attribute__((aligned(16)));
	uint8_t input[256] __attribute__((aligned(16))) = { 0 };
	uint8_t output[256] __attribute__((aligned(16)));
	size_t cases = 0;
	for (unsigned vector = 0; vector < 2; ++vector) {
		memset(k, 0, sizeof k);
		memset(iv, 0, sizeof iv);
		if (!vector) {
			for (size_t i = 0; i < sizeof k; ++i) k[i] = (uint8_t)i;
			iv[3] = 9;
			iv[7] = 0x4a;
		} else k[1] = 0xff;
		uint32_t target_counter = vector + 1;
		for (size_t j = 0; j < ARR_LEN(impls); ++j) {
			Func *f = impls[j].func;
			f(output, input, 64, k, iv, target_counter);
			if (!bytes_equal(output, answers[vector], 64))
				fail("RFC block", impls[j].name, 64, target_counter);
			f(output, input, 256, k, iv, 0);
			if (!bytes_equal(output + 64 * target_counter, answers[vector], 64))
				fail("RFC batch block", impls[j].name, 256, 0);
			cases += 2;
		}
	}
	return cases;
}

static size_t
check_counter_end(void)
{
	static const struct { size_t n; uint32_t counter; } tests[] = {
		{ 64, 0xffffffff }, { 256, 0xfffffffc }, { 257, 0xfffffffb },
		{ 512, 0xfffffff8 }, { 513, 0xfffffff7 },
	};
	uint8_t input[513] __attribute__((aligned(16))) = { 0 };
	uint8_t expected[513] __attribute__((aligned(16)));
	uint8_t output[GUARD + 513 + GUARD] __attribute__((aligned(16)));
	size_t cases = 0;
	for (size_t t = 0; t < ARR_LEN(tests); ++t) {
		size_t n = tests[t].n;
		uint32_t c = tests[t].counter;
		boring_chacha20(expected, input, n, key, nonce, c);
		for (size_t j = 0; j < ARR_LEN(impls); ++j) {
			Func *f = impls[j].func;
			memset(output, 0xa5, sizeof output);
			f(output + GUARD, input, n, key, nonce, c);
			if (!bytes_equal(output + GUARD, expected, n))
				fail("last counter", impls[j].name, n, c);
			for (size_t i = 0; i < sizeof output; ++i)
				if ((i < GUARD || i >= GUARD + n) && output[i] != 0xa5)
					fail("last counter guard", impls[j].name, n, c);
			++cases;
		}
	}
	return cases;
}

static size_t
check_alias_alignment(void)
{
	static const size_t sizes[] = { 0, 1, 63, 64, 65, 255, 256, 257, 511, 512, 513 };
	uint8_t input[517] __attribute__((aligned(16)));
	uint8_t saved[517], expected[513];
	uint8_t output[GUARD + 517 + GUARD] __attribute__((aligned(16)));
	uint8_t k[33] __attribute__((aligned(16)));
	uint8_t iv[13] __attribute__((aligned(16)));
	size_t cases = 0;
	for (size_t i = 0; i < sizeof input; ++i) input[i] = saved[i] = (uint8_t)(i * 19 + 7);
	for (size_t i = 0; i < sizeof k; ++i) k[i] = (uint8_t)(i * 13 + 1);
	for (size_t i = 0; i < sizeof iv; ++i) iv[i] = (uint8_t)(i * 17 + 3);
	for (unsigned mode = 0; mode < 3; ++mode) {
		const uint8_t *kptr = k + (mode == 2);
		const uint8_t *ivptr = iv + (mode == 2);
		const uint8_t *in = input + (mode == 1);
		for (size_t s = 0; s < ARR_LEN(sizes); ++s) {
			size_t n = sizes[s];
			boring_chacha20(expected, in, n, kptr, ivptr, 7);
			for (size_t j = 0; j < ARR_LEN(impls); ++j) {
				Func *f = impls[j].func;
				if (f != chacha20_riscv_xor && f != chacha20_riscv_m4) continue;
				memset(output, 0xa5, sizeof output);
				uint8_t *out = output + GUARD + (mode == 1 ? 3 : 0);
				if (mode == 0) memcpy(out, in, n);
				f(out, mode == 0 ? out : in, n, kptr, ivptr, 7);
				if (!bytes_equal(out, expected, n) || !bytes_equal(input, saved, sizeof input))
					fail("alias/alignment", impls[j].name, n, 7);
				for (size_t i = 0; i < sizeof output; ++i)
					if ((output + i < out || output + i >= out + n) && output[i] != 0xa5)
						fail("alias/alignment guard", impls[j].name, n, 7);
				++cases;
			}
		}
	}
	return cases;
}

static size_t
check_long(void)
{
	static const size_t sizes[] = { 65536, 262144, 1048576 };
	static uint8_t input[1048576] __attribute__((aligned(16)));
	static uint8_t expected[1048576] __attribute__((aligned(16)));
	static uint8_t output[GUARD + 1048576 + GUARD] __attribute__((aligned(16)));
	size_t cases = 0;
	for (size_t i = 0; i < sizeof input; ++i) input[i] = (uint8_t)(i * 29 + 11);
	for (size_t s = 0; s < ARR_LEN(sizes); ++s) {
		size_t n = sizes[s];
		boring_chacha20(expected, input, n, key, nonce, 7);
		for (size_t j = 0; j < ARR_LEN(impls); ++j) {
			Func *f = impls[j].func;
			memset(output, 0xa5, GUARD);
			memset(output + GUARD + n, 0xa5, GUARD);
			f(output + GUARD, input, n, key, nonce, 7);
			if (!bytes_equal(output + GUARD, expected, n))
				fail("long output", impls[j].name, n, 7);
			for (size_t i = 0; i < GUARD; ++i)
				if (output[i] != 0xa5 || output[GUARD + n + i] != 0xa5)
					fail("long guard", impls[j].name, n, 7);
			for (size_t i = 0; i < n; ++i)
				if (input[i] != (uint8_t)(i * 29 + 11))
					fail("long input overwrite", impls[j].name, n, 7);
			++cases;
		}
	}
	return cases;
}

int
main(void)
{
	mem = (unsigned char*)(((uintptr_t)_heap_start + MEM_ALIGN-1) & ~(MEM_ALIGN-1));
	check_inputs();
	size_t direct_cases = check_three_way();
	size_t cases = check_streams();
	cases += check_rfc();
	cases += check_counter_end();
	cases += check_alias_alignment();
	cases += check_long();
	print("OK: chacha20 direct riscv==rvv==riscv_m4 cases=")(u,direct_cases)
		("\n")(flush,);
	print("OK: chacha20 stream API cases=")(u,cases)("\n")(flush,);
	return 0;
}
