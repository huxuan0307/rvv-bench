/* GNU libc scalar-algorithm RVV adaptation; LGPL-2.1-or-later. */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/common/scalar_tables.h"
#define ENTRY expfvec_m1
#define CORE expfvec_core_m1
#define NX _ZGV1Nxv_expf
#define SETVLMAX __riscv_vsetvlmax_e32m1
#define REAL_TYPE float
#define ENTRY_LOAD __riscv_vle32_v_f32m1
#define ENTRY_STORE __riscv_vse32_v_f32m1
#define FV vfloat32m1_t
#define UV vuint32m1_t
#define IV vint32m1_t
#define DV vfloat64m2_t
#define WV vuint64m2_t
#define BV vbool32_t
#define FT f32m1
#define UT u32m1
#define IT i32m1
#define DT f64m2
#define WT u64m2
#define BT b32
#define SETVL __riscv_vsetvl_e32m1
#define FU __riscv_vreinterpret_v_f32m1_u32m1
#define UF __riscv_vreinterpret_v_u32m1_f32m1
#define UI __riscv_vreinterpret_v_u32m1_i32m1
#define DU __riscv_vreinterpret_v_f64m2_u64m2
#define UD __riscv_vreinterpret_v_u64m2_f64m2
#define UGE __riscv_vmsgeu_vx_u32m1_b32
#define ULT __riscv_vmsltu_vx_u32m1_b32
#define WIDEN __riscv_vfwcvt_f_f_v_f64m2
#define NARROW __riscv_vfncvt_f_f_w_f32m1
#define GATHER __riscv_vluxei64_v_u64m2
#define WIDEN_U __riscv_vzext_vf2_u64m2
#define WIDEN_I __riscv_vfwcvt_f_x_v_f64m2
#include "../../kernels/expf/glibc.c"
