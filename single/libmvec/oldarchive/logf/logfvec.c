/* GNU libc scalar-algorithm RVV adaptation; LGPL-2.1-or-later. */
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "newlib.h"
#include "scalar_tables.h"
#define ENTRY logfvec_m1
#define CORE logfvec_core_m1
#define NX _ZGV1Nxv_logf
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
#define GATHER __riscv_vluxei64_v_f64m2
#define WIDEN_U __riscv_vzext_vf2_u64m2
#define WIDEN_I __riscv_vfwcvt_f_x_v_f64m2
#include "logfvec_impl.inc.h"
#undef ENTRY
#undef CORE
#undef NX
#undef SETVLMAX
#undef REAL_TYPE
#undef ENTRY_LOAD
#undef ENTRY_STORE
#undef FV
#undef UV
#undef IV
#undef DV
#undef WV
#undef BV
#undef FT
#undef UT
#undef IT
#undef DT
#undef WT
#undef BT
#undef SETVL
#undef FU
#undef UF
#undef UI
#undef DU
#undef UD
#undef UGE
#undef ULT
#undef WIDEN
#undef NARROW
#undef GATHER
#undef WIDEN_U
#undef WIDEN_I
#define ENTRY logfvec_m2
#define CORE logfvec_core_m2
#define NX _ZGV2Nxv_logf
#define SETVLMAX __riscv_vsetvlmax_e32m2
#define REAL_TYPE float
#define ENTRY_LOAD __riscv_vle32_v_f32m2
#define ENTRY_STORE __riscv_vse32_v_f32m2
#define FV vfloat32m2_t
#define UV vuint32m2_t
#define IV vint32m2_t
#define DV vfloat64m4_t
#define WV vuint64m4_t
#define BV vbool16_t
#define FT f32m2
#define UT u32m2
#define IT i32m2
#define DT f64m4
#define WT u64m4
#define BT b16
#define SETVL __riscv_vsetvl_e32m2
#define FU __riscv_vreinterpret_v_f32m2_u32m2
#define UF __riscv_vreinterpret_v_u32m2_f32m2
#define UI __riscv_vreinterpret_v_u32m2_i32m2
#define DU __riscv_vreinterpret_v_f64m4_u64m4
#define UD __riscv_vreinterpret_v_u64m4_f64m4
#define UGE __riscv_vmsgeu_vx_u32m2_b16
#define ULT __riscv_vmsltu_vx_u32m2_b16
#define WIDEN __riscv_vfwcvt_f_f_v_f64m4
#define NARROW __riscv_vfncvt_f_f_w_f32m2
#define GATHER __riscv_vluxei64_v_f64m4
#define WIDEN_U __riscv_vzext_vf2_u64m4
#define WIDEN_I __riscv_vfwcvt_f_x_v_f64m4
#include "logfvec_impl.inc.h"
#undef ENTRY
#undef CORE
#undef NX
#undef SETVLMAX
#undef REAL_TYPE
#undef ENTRY_LOAD
#undef ENTRY_STORE
#undef FV
#undef UV
#undef IV
#undef DV
#undef WV
#undef BV
#undef FT
#undef UT
#undef IT
#undef DT
#undef WT
#undef BT
#undef SETVL
#undef FU
#undef UF
#undef UI
#undef DU
#undef UD
#undef UGE
#undef ULT
#undef WIDEN
#undef NARROW
#undef GATHER
#undef WIDEN_U
#undef WIDEN_I
