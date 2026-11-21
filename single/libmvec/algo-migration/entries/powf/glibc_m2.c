/* GNU libc scalar-algorithm RVV adaptation; LGPL-2.1-or-later.
   powf: RVV widening adaptation of glibc flt-32 e_powf.c (see
   powfvec_impl.inc.h for the algorithm, accuracy contract and the scalar
   slow-path contract). */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/common/scalar_tables.h"
#define ENTRY powfvec_m2
#define CORE powfvec_core_m2
#define NX _ZGV2Nxvv_powf
#define FIXED_NX _ZGV2N8vv_powf
#define FIXED_LANES 8
#define SETVLMAX __riscv_vsetvlmax_e32m2
#define ENTRY_LOAD __riscv_vle32_v_f32m2
#define ENTRY_STORE __riscv_vse32_v_f32m2
#define FV vfloat32m2_t
#define UV vuint32m2_t
#define DV vfloat64m4_t
#define IV vint64m4_t
#define WV vuint64m4_t
#define BV vbool16_t
#define FT f32m2
#define UT u32m2
#define DT f64m4
#define IT i64m4
#define WT u64m4
#define BT b16
#define SETVL __riscv_vsetvl_e32m2
#define FU __riscv_vreinterpret_v_f32m2_u32m2
#define UF __riscv_vreinterpret_v_u32m2_f32m2
#define IU __riscv_vreinterpret_v_u32m2_i32m2
#define DU __riscv_vreinterpret_v_f64m4_u64m4
#define UD __riscv_vreinterpret_v_u64m4_f64m4
#define WIDEN __riscv_vfwcvt_f_f_v_f64m4
#define WIDENU __riscv_vzext_vf2_u64m4
#define SEXT __riscv_vsext_vf2_i64m4
#define I2F __riscv_vfcvt_f_x_v_f64m4
#define GATHERF __riscv_vluxei64_v_f64m4
#define GATHERW __riscv_vluxei64_v_u64m4
#define NARROW __riscv_vfncvt_f_f_w_f32m2
#define VSM __riscv_vsm_v_b16
#include "../../kernels/powf/glibc.c"
