/* RVV adaptation of IBM AML, GNU libc s_sin.c, usncs.h, sincostab.c.
   Copyright (C) 2001-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   Historical precision testing used RNE; the final rounding contract is open. */
#include <math.h>
#include <stdint.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/common/trigvec_data.h"
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
#include "../../kernels/common/trigvec_impl.h"
