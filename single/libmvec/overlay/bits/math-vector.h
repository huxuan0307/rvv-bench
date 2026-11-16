/* Benchmark availability overlay: only unmasked scalable Nx m1/m2.
   The scalar math prototypes remain installed glibc declarations. */
#ifndef _MATH_H
# error "Include math.h instead"
#endif
#include <bits/libm-simd-decl-stubs.h>
#if defined(__FAST_MATH__) && defined(__riscv_vector)
# if !defined(__riscv_simd_math_abi_v1) || __riscv_simd_math_abi_v1 != 1
#  error "Compiler lacks required RISC-V vector math ABI handshake"
# endif
# define RVV_BENCH_NX_ONLY __attribute__((__simd__("notinbranch"), __riscv_simd_math_abi_v1__(0x22), const))
# undef __DECL_SIMD_log
# undef __DECL_SIMD_exp
# undef __DECL_SIMD_sin
# undef __DECL_SIMD_cos
# undef __DECL_SIMD_pow
# undef __DECL_SIMD_logf
# undef __DECL_SIMD_expf
# undef __DECL_SIMD_sinf
# undef __DECL_SIMD_cosf
# define __DECL_SIMD_log RVV_BENCH_NX_ONLY
# define __DECL_SIMD_exp RVV_BENCH_NX_ONLY
# define __DECL_SIMD_sin RVV_BENCH_NX_ONLY
# define __DECL_SIMD_cos RVV_BENCH_NX_ONLY
# define __DECL_SIMD_pow RVV_BENCH_NX_ONLY
# define __DECL_SIMD_logf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_expf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_sinf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_cosf RVV_BENCH_NX_ONLY
#endif
