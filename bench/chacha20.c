#include "bench.h"
#if __riscv_xlen != 32
#include "../thirdparty/rvv-chacha-poly/boring.h"
#include "../thirdparty/chacha20-riscv/src/chacha20.h"

uint8_t key[32] __attribute__((aligned(16)));
uint8_t nonce[12] __attribute__((aligned(16)));
uint32_t counter;


extern void vector_chacha20(
		uint8_t *out, const uint8_t *in,
		size_t in_len, const uint8_t key[32],
		const uint8_t nonce[12], uint32_t counter);

static uint32_t
load32_le(const uint8_t *p)
{
	return ((uint32_t)p[0]) |
	       ((uint32_t)p[1] << 8) |
	       ((uint32_t)p[2] << 16) |
	       ((uint32_t)p[3] << 24);
}

static void
chacha20_riscv(uint8_t *out, const uint8_t *in, size_t n,
		const uint8_t key[32], const uint8_t nonce[12], uint32_t counter) {
	uint32_t state[16] = {
		0x61707865, 0x3320646e, 0x79622d32, 0x6b206574,
		load32_le(key + 0), load32_le(key + 4),
		load32_le(key + 8), load32_le(key + 12),
		load32_le(key + 16), load32_le(key + 20),
		load32_le(key + 24), load32_le(key + 28),
		counter,
		load32_le(nonce + 0), load32_le(nonce + 4),
		load32_le(nonce + 8),
	};
	while (n > 0) {
		chacha_buf block;
		size_t todo = n < sizeof(block.c) ? n : sizeof(block.c);

		chacha20(&block, state);
		for (size_t i = 0; i < todo; ++i)
			out[i] = in[i] ^ block.c[i];

		out += todo;
		in += todo;
		n -= todo;
		state[12]++;
	}
}

typedef void Func(uint8_t *out, const uint8_t *in, size_t n,
		const uint8_t key[32], const uint8_t nonce[12], uint32_t counter);

#include "chacha20-riscv-opt.h"

Impl impls[] = {
	{ "boring", &boring_chacha20, 0 },
	{ "rvv", &vector_chacha20, 0 },
	{ "riscv", &chacha20_riscv, 0 },
	{ "riscv_xor", &chacha20_riscv_xor, 0 },
	{ "riscv_m4", &chacha20_riscv_m4, 0 },
};

void init(void) {
	/* Independent binaries must encrypt identical data, regardless of cycle seed. */
	URand inputs = { 123, 456, 789 };
	memrand(&inputs, key, sizeof key);
	memrand(&inputs, nonce, sizeof nonce);
	memrand(&inputs, mem + MAX_MEM/2 + 16, MAX_DEST);
	counter = 0;
}

ux checksum(size_t n) {
	ux sum = 0;
	for (size_t i = 0; i < n+16; ++i)
		sum = uhash(sum) + mem[i];
	return sum;
}

BENCH_BEG(aligned) {
	memset(mem, 0, n+16);
	/* All implementations include state setup, block processing and plaintext XOR. */
	TIME f(mem, mem + MAX_MEM/2 + 16, n, key, nonce, counter);
} BENCH_END

Bench benches[] = {
	BENCH( impls, MAX_DEST - 16, "chacha20 aligned", bench_aligned )
}; BENCH_MAIN(benches)


#include "../thirdparty/rvv-chacha-poly/boring.c"
#else
void init(void) {}
Impl impls[] = {};
Bench benches[] = {};
BENCH_MAIN(benches)
#endif
