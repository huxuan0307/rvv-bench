#ifndef RVV_BENCH_CHACHA20_RISCV_OPT_H
#define RVV_BENCH_CHACHA20_RISCV_OPT_H

#include <riscv_vector.h>

extern void riscv_chacha20_m4_blocks(uint8_t *out, const uint8_t *in, size_t n,
		const uint8_t key[32], const uint8_t nonce[12], uint32_t counter);

static void
chacha20_riscv_xor(uint8_t *out, const uint8_t *in, size_t n,
		const uint8_t key[32], const uint8_t nonce[12], uint32_t counter)
{
	uint32_t state[16] = {
		0x61707865, 0x3320646e, 0x79622d32, 0x6b206574,
		load32_le(key + 0), load32_le(key + 4),
		load32_le(key + 8), load32_le(key + 12),
		load32_le(key + 16), load32_le(key + 20),
		load32_le(key + 24), load32_le(key + 28), counter,
		load32_le(nonce + 0), load32_le(nonce + 4), load32_le(nonce + 8),
	};
	while (n > 0) {
		chacha_buf block;
		size_t todo = n < sizeof block.c ? n : sizeof block.c;
		chacha20(&block, state);
		/* Full V guarantees at least 64 byte elements in an m4 group. */
		size_t vl = __riscv_vsetvl_e8m4(todo);
		vuint8m4_t plain = __riscv_vle8_v_u8m4(in, vl);
		vuint8m4_t stream = __riscv_vle8_v_u8m4(block.c, vl);
		__riscv_vse8_v_u8m4(out, __riscv_vxor_vv_u8m4(plain, stream, vl), vl);
		out += todo;
		in += todo;
		n -= todo;
		state[12]++;
	}
}

static void
chacha20_riscv_m4(uint8_t *out, const uint8_t *in, size_t n,
		const uint8_t key[32], const uint8_t nonce[12], uint32_t counter)
{
	size_t full = n & ~(size_t)255;
	if (full && !(((uintptr_t)out | (uintptr_t)in |
	              (uintptr_t)key | (uintptr_t)nonce) & 3)) {
		riscv_chacha20_m4_blocks(out, in, full, key, nonce, counter);
		out += full;
		in += full;
		n -= full;
		counter += (uint32_t)(full / 64);
	}
	if (n) chacha20_riscv_xor(out, in, n, key, nonce, counter);
}

#endif
