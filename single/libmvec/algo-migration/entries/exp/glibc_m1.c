/* GNU libc scalar-algorithm RVV adaptation; LGPL-2.1-or-later. */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/common/scalar_tables.h"
#define ENTRY expvec_m1
#define CORE expvec_core_m1
#define NX _ZGV1Nxv_exp
#define SETVLMAX __riscv_vsetvlmax_e64m1
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m1
#define ENTRY_STORE __riscv_vse64_v_f64m1
#define FV vfloat64m1_t
#define UV vuint64m1_t
#define BV vbool64_t
#define FT f64m1
#define UT u64m1
#define BT b64
#define SETVL __riscv_vsetvl_e64m1
#define FU __riscv_vreinterpret_v_f64m1_u64m1
#define UF __riscv_vreinterpret_v_u64m1_f64m1
#define UGE __riscv_vmsgeu_vx_u64m1_b64
#define ULT __riscv_vmsltu_vx_u64m1_b64
#define GATHER __riscv_vluxei64_v_u64m1
#include "../../kernels/exp/glibc.c"
