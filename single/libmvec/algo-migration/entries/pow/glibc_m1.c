/* Experimental RVV array adaptation of GNU libc e_pow.c.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   The original full license notice is retained in powvec_data.h.
   This experiment does not change installed libraries or the public vector ABI. */
#include <math.h>
#include <stdint.h>
#include <riscv_vector.h>
#include "../../kernels/common/newlib.h"
#include "../../kernels/pow/powvec.h"
#include "../../kernels/pow/powvec_data.h"
#define ENTRY powvec_m1
#define CORE powvec_core_m1
#define NX _ZGV1Nxvv_pow
#define SETVLMAX __riscv_vsetvlmax_e64m1
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m1
#define ENTRY_STORE __riscv_vse64_v_f64m1
#define FV vfloat64m1_t
#define UV vuint64m1_t
#define IV vint64m1_t
#define BV vbool64_t
#define FT f64m1
#define UT u64m1
#define IT i64m1
#define BT b64
#define SETVL __riscv_vsetvl_e64m1
#define REINTERPRET_FU __riscv_vreinterpret_v_f64m1_u64m1
#define REINTERPRET_UF __riscv_vreinterpret_v_u64m1_f64m1
#define REINTERPRET_UI __riscv_vreinterpret_v_u64m1_i64m1
#define UCMPLT __riscv_vmsltu_vx_u64m1_b64
#define GATHER __riscv_vluxei64_v_f64m1
#define UGATHER __riscv_vluxei64_v_u64m1
#define CONVERT __riscv_vfcvt_f_x_v_f64m1
#include "../../kernels/pow/glibc.c"
