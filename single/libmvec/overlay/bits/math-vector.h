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
/* ABI variant mask.  A compiler that knows __riscv_simd_math_abi_v1_lmul8
   understands the full 16-bit mask (m1/m2/m4/m8, fixed and scalable); older
   compilers reject any value above 255 instead of ignoring the high bits, so
   they get the historical 8-bit m1/m2 scalable-N mask. */
# ifdef __riscv_simd_math_abi_v1_lmul8
#  define RVV_BENCH_ABI_MASK 0xffffu
# else
#  define RVV_BENCH_ABI_MASK 0x22u
# endif
# define RVV_BENCH_NX_ONLY __attribute__((__simd__("notinbranch"), __riscv_simd_math_abi_v1__(RVV_BENCH_ABI_MASK), const))
# undef __DECL_SIMD_log
# undef __DECL_SIMD_exp
# undef __DECL_SIMD_sin
# undef __DECL_SIMD_cos
# undef __DECL_SIMD_pow
# undef __DECL_SIMD_logf
# undef __DECL_SIMD_expf
# undef __DECL_SIMD_sinf
# undef __DECL_SIMD_cosf
# undef __DECL_SIMD_powf
# define __DECL_SIMD_log RVV_BENCH_NX_ONLY
# define __DECL_SIMD_exp RVV_BENCH_NX_ONLY
# define __DECL_SIMD_sin RVV_BENCH_NX_ONLY
# define __DECL_SIMD_cos RVV_BENCH_NX_ONLY
# define __DECL_SIMD_pow RVV_BENCH_NX_ONLY
# define __DECL_SIMD_logf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_expf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_sinf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_cosf RVV_BENCH_NX_ONLY
# define __DECL_SIMD_powf RVV_BENCH_NX_ONLY
/* The downstream glibc also advertises the vector entries of log10/atan/
   atan2/acos; the throughput harness declares them NX-only too so the auto_m1/
   auto_m2 columns really measure compiler-generated Nx calls. */
# undef __DECL_SIMD_log10
# undef __DECL_SIMD_atan
# undef __DECL_SIMD_atan2
# undef __DECL_SIMD_acos
# define __DECL_SIMD_log10 RVV_BENCH_NX_ONLY
# define __DECL_SIMD_atan RVV_BENCH_NX_ONLY
# define __DECL_SIMD_atan2 RVV_BENCH_NX_ONLY
# define __DECL_SIMD_acos RVV_BENCH_NX_ONLY
#endif
