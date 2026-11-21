/* Generated LMUL4/8 instantiations of the measured expfvec template.
   The m1/m2 measurement units are untouched so their machine code stays
   byte-identical; only the macro parameters below differ.
   SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef _EXPFVEC_LMUL48_C
#define _EXPFVEC_LMUL48_C
#include <math.h>
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "../common/newlib.h"
#include "../common/scalar_tables.h"
/* ---- LMUL4 (f32) ---- */
#define ENTRY expfvec_m4
#define CORE expfvec_core_m4
#define NX _ZGV4Nxv_expf
#define SETVLMAX __riscv_vsetvlmax_e32m4
#define REAL_TYPE float
#define ENTRY_LOAD __riscv_vle32_v_f32m4
#define ENTRY_STORE __riscv_vse32_v_f32m4
#define FV vfloat32m4_t
#define UV vuint32m4_t
#define IV vint32m4_t
#define DV vfloat64m8_t
#define WV vuint64m8_t
#define BV vbool8_t
#define FT f32m4
#define UT u32m4
#define IT i32m4
#define DT f64m8
#define WT u64m8
#define BT b8
#define SETVL __riscv_vsetvl_e32m4
#define FU __riscv_vreinterpret_v_f32m4_u32m4
#define UF __riscv_vreinterpret_v_u32m4_f32m4
#define UI __riscv_vreinterpret_v_u32m4_i32m4
#define DU __riscv_vreinterpret_v_f64m8_u64m8
#define UD __riscv_vreinterpret_v_u64m8_f64m8
#define UGE __riscv_vmsgeu_vx_u32m4_b8
#define ULT __riscv_vmsltu_vx_u32m4_b8
#define WIDEN __riscv_vfwcvt_f_f_v_f64m8
#define NARROW __riscv_vfncvt_f_f_w_f32m4
#define GATHER __riscv_vluxei64_v_u64m8
#define WIDEN_U __riscv_vzext_vf2_u64m8
#define WIDEN_I __riscv_vfwcvt_f_x_v_f64m8
#include "glibc.c"

/* ---- appended shell ---- */
#define LOGFVEC_M8_ENTRY expfvec_m8
#define LOGFVEC_CORE_M4 expfvec_core_m4
#define LOGFVEC_M8_SYMBOL _ZGV8Nxv_expf
#include "../common/f32_m8_shell.inc.h"
#undef LOGFVEC_M8_ENTRY
#undef LOGFVEC_CORE_M4
#undef LOGFVEC_M8_SYMBOL

#endif /* _EXPFVEC_LMUL48_C */
