#ifndef MX
#if __riscv_xlen != 32
#include "../thirdparty/chacha20-riscv/src/chacha20.s"
.text
#include "chacha20-riscv-m4.S"
#include "../thirdparty/rvv-chacha-poly/vchacha.s"
#endif
#endif
