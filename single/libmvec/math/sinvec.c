/* RVV adaptation of IBM AML, GNU libc s_sin.c, usncs.h, sincostab.c.
   Copyright (C) 2001-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   RNE-only kernels: the outer benchmark entry owns the rounding guard. */
#include <math.h>
#include <stdint.h>
#include <riscv_vector.h>
#include "newlib.h"
#include "trigvec_data.h"
#define ENTRY sinvec_m1
#define CORE sinvec_core_m1
#define NX _ZGV1Nxv_sin
#define SETVLMAX __riscv_vsetvlmax_e64m1
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m1
#define ENTRY_STORE __riscv_vse64_v_f64m1
#define TRIG_COS 0
#define SCALAR sin
#define FV vfloat64m1_t
#define UV vuint64m1_t
#define BV vbool64_t
#define FT f64m1
#define UT u64m1
#define BT b64
#define SETVL __riscv_vsetvl_e64m1
#define REINTERPRET_FU __riscv_vreinterpret_v_f64m1_u64m1
#define UCMPLT __riscv_vmsltu_vx_u64m1_b64
#define UCMPNE __riscv_vmsne_vx_u64m1_b64
#define FCMPLT __riscv_vmflt_vf_f64m1_b64
#define GATHER __riscv_vluxei64_v_f64m1
#include "trigvec_impl.h"
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
#define ENTRY sinvec_m2
#define CORE sinvec_core_m2
#define NX _ZGV2Nxv_sin
#define SETVLMAX __riscv_vsetvlmax_e64m2
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m2
#define ENTRY_STORE __riscv_vse64_v_f64m2
#define TRIG_COS 0
#define SCALAR sin
#define FV vfloat64m2_t
#define UV vuint64m2_t
#define BV vbool32_t
#define FT f64m2
#define UT u64m2
#define BT b32
#define SETVL __riscv_vsetvl_e64m2
#define REINTERPRET_FU __riscv_vreinterpret_v_f64m2_u64m2
#define UCMPLT __riscv_vmsltu_vx_u64m2_b32
#define UCMPNE __riscv_vmsne_vx_u64m2_b32
#define FCMPLT __riscv_vmflt_vf_f64m2_b32
#define GATHER __riscv_vluxei64_v_f64m2
#include "trigvec_impl.h"
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
