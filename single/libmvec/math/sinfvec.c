/* GNU libc scalar-algorithm RVV adaptation; LGPL-2.1-or-later.
   sinf: RVV widening adaptation of glibc flt-32 s_sinf.c (see
   trigfvec_impl.h for the algorithm, accuracy contract and limitations). */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "newlib.h"
#include "scalar_tables.h"
#define ENTRY sinfvec_m1
#define CORE sinfvec_core_m1
#define NX _ZGV1Nxv_sinf
#define FIXED_NX _ZGV1N4v_sinf
#define FIXED_LANES 4
#define SETVLMAX __riscv_vsetvlmax_e32m1
#define REAL_TYPE float
#define TRIG_COS 0
#define SCALAR sinf
#define ENTRY_LOAD __riscv_vle32_v_f32m1
#define ENTRY_STORE __riscv_vse32_v_f32m1
#define FV vfloat32m1_t
#define UV vuint32m1_t
#define DV vfloat64m2_t
#define IV vint64m2_t
#define BV vbool32_t
#define FT f32m1
#define UT u32m1
#define DT f64m2
#define IT i64m2
#define BT b32
#define SETVL __riscv_vsetvl_e32m1
#define FU __riscv_vreinterpret_v_f32m1_u32m1
#define UGE __riscv_vmsgeu_vx_u32m1_b32
#define ULT __riscv_vmsltu_vx_u32m1_b32
#define WIDEN __riscv_vfwcvt_f_f_v_f64m2
#define NARROW __riscv_vfncvt_f_f_w_f32m1
#define RTZI __riscv_vfcvt_rtz_x_f_v_i64m2
#define I2F __riscv_vfcvt_f_x_v_f64m2
#include "trigfvec_impl.h"
#undef ENTRY
#undef CORE
#undef NX
#undef FIXED_NX
#undef FIXED_LANES
#undef SETVLMAX
#undef REAL_TYPE
#undef TRIG_COS
#undef SCALAR
#undef ENTRY_LOAD
#undef ENTRY_STORE
#undef FV
#undef UV
#undef DV
#undef IV
#undef BV
#undef FT
#undef UT
#undef DT
#undef IT
#undef BT
#undef SETVL
#undef FU
#undef UGE
#undef ULT
#undef WIDEN
#undef NARROW
#undef RTZI
#undef I2F
#define ENTRY sinfvec_m2
#define CORE sinfvec_core_m2
#define NX _ZGV2Nxv_sinf
#define FIXED_NX _ZGV2N8v_sinf
#define FIXED_LANES 8
#define SETVLMAX __riscv_vsetvlmax_e32m2
#define REAL_TYPE float
#define TRIG_COS 0
#define SCALAR sinf
#define ENTRY_LOAD __riscv_vle32_v_f32m2
#define ENTRY_STORE __riscv_vse32_v_f32m2
#define FV vfloat32m2_t
#define UV vuint32m2_t
#define DV vfloat64m4_t
#define IV vint64m4_t
#define BV vbool16_t
#define FT f32m2
#define UT u32m2
#define DT f64m4
#define IT i64m4
#define BT b16
#define SETVL __riscv_vsetvl_e32m2
#define FU __riscv_vreinterpret_v_f32m2_u32m2
#define UGE __riscv_vmsgeu_vx_u32m2_b16
#define ULT __riscv_vmsltu_vx_u32m2_b16
#define WIDEN __riscv_vfwcvt_f_f_v_f64m4
#define NARROW __riscv_vfncvt_f_f_w_f32m2
#define RTZI __riscv_vfcvt_rtz_x_f_v_i64m4
#define I2F __riscv_vfcvt_f_x_v_f64m4
#include "trigfvec_impl.h"
#undef ENTRY
#undef CORE
#undef NX
#undef FIXED_NX
#undef FIXED_LANES
#undef SETVLMAX
#undef REAL_TYPE
#undef TRIG_COS
#undef SCALAR
#undef ENTRY_LOAD
#undef ENTRY_STORE
#undef FV
#undef UV
#undef DV
#undef IV
#undef BV
#undef FT
#undef UT
#undef DT
#undef IT
#undef BT
#undef SETVL
#undef FU
#undef UGE
#undef ULT
#undef WIDEN
#undef NARROW
#undef RTZI
#undef I2F
