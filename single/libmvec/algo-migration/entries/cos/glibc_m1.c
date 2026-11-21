/* RVV adaptation of IBM AML, GNU libc s_sin.c, usncs.h, sincostab.c.
   Copyright (C) 2001-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   Historical precision testing used RNE; the final rounding contract is open. */
#include <math.h>
#include <stdint.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/common/trigvec_data.h"
#define ENTRY cosvec_m1
#define CORE cosvec_core_m1
#define NX _ZGV1Nxv_cos
#define SETVLMAX __riscv_vsetvlmax_e64m1
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m1
#define ENTRY_STORE __riscv_vse64_v_f64m1
#define TRIG_COS 1
#define SCALAR cos
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
#include "../../kernels/common/trigvec_impl.h"
