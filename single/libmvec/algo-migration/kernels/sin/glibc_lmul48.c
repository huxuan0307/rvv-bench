/* Generated LMUL4/8 instantiations of the measured sinvec template.
   The m1/m2 measurement units are untouched so their machine code stays
   byte-identical; only the macro parameters below differ.
   SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef _SINVEC_LMUL48_C
#define _SINVEC_LMUL48_C
#include <math.h>
#include <stdint.h>
#include <riscv_vector.h>
#include "../common/newlib.h"
#include "../common/trigvec_data.h"
/* ---- LMUL4 (f64) ---- */
#define ENTRY sinvec_m4
#define CORE sinvec_core_m4
#define NX _ZGV4Nxv_sin
#define SETVLMAX __riscv_vsetvlmax_e64m4
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m4
#define ENTRY_STORE __riscv_vse64_v_f64m4
#define TRIG_COS 0
#define SCALAR sin
#define FV vfloat64m4_t
#define UV vuint64m4_t
#define BV vbool16_t
#define FT f64m4
#define UT u64m4
#define BT b16
#define SETVL __riscv_vsetvl_e64m4
#define REINTERPRET_FU __riscv_vreinterpret_v_f64m4_u64m4
#define UCMPLT __riscv_vmsltu_vx_u64m4_b16
#define UCMPNE __riscv_vmsne_vx_u64m4_b16
#define FCMPLT __riscv_vmflt_vf_f64m4_b16
#define GATHER __riscv_vluxei64_v_f64m4
#include "../common/trigvec_impl.h"

#undef ENTRY
#undef CORE
#undef NX
#undef SETVLMAX
#undef REAL_TYPE
#undef ENTRY_LOAD
#undef ENTRY_STORE
#undef TRIG_COS
#undef SCALAR
#undef FV
#undef UV
#undef BV
#undef FT
#undef UT
#undef BT
#undef SETVL
#undef REINTERPRET_FU
#undef UCMPLT
#undef UCMPNE
#undef FCMPLT
#undef GATHER

/* ---- LMUL8 (f64) ---- */
#define ENTRY sinvec_m8
#define CORE sinvec_core_m8
#define NX _ZGV8Nxv_sin
#define SETVLMAX __riscv_vsetvlmax_e64m8
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m8
#define ENTRY_STORE __riscv_vse64_v_f64m8
#define TRIG_COS 0
#define SCALAR sin
#define FV vfloat64m8_t
#define UV vuint64m8_t
#define BV vbool8_t
#define FT f64m8
#define UT u64m8
#define BT b8
#define SETVL __riscv_vsetvl_e64m8
#define REINTERPRET_FU __riscv_vreinterpret_v_f64m8_u64m8
#define UCMPLT __riscv_vmsltu_vx_u64m8_b8
#define UCMPNE __riscv_vmsne_vx_u64m8_b8
#define FCMPLT __riscv_vmflt_vf_f64m8_b8
#define GATHER __riscv_vluxei64_v_f64m8
#include "../common/trigvec_impl.h"

#endif /* _SINVEC_LMUL48_C */
