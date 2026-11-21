/* GNU libc scalar-algorithm RVV adaptation; LGPL-2.1-or-later.
   cosf: RVV widening adaptation of glibc flt-32 s_cosf.c (see
   trigfvec_impl.h for the algorithm, accuracy contract and limitations). */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/common/scalar_tables.h"
#define ENTRY cosfvec_m2
#define CORE cosfvec_core_m2
#define NX _ZGV2Nxv_cosf
#define FIXED_NX _ZGV2N8v_cosf
#define FIXED_LANES 8
#define SETVLMAX __riscv_vsetvlmax_e32m2
#define REAL_TYPE float
#define TRIG_COS 1
#define SCALAR cosf
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
#include "../../kernels/common/trigfvec_impl.h"
