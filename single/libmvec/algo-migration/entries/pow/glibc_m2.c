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
#define ENTRY powvec_m2
#define CORE powvec_core_m2
#define NX _ZGV2Nxvv_pow
#define SETVLMAX __riscv_vsetvlmax_e64m2
#define REAL_TYPE double
#define ENTRY_LOAD __riscv_vle64_v_f64m2
#define ENTRY_STORE __riscv_vse64_v_f64m2
#define FV vfloat64m2_t
#define UV vuint64m2_t
#define IV vint64m2_t
#define BV vbool32_t
#define FT f64m2
#define UT u64m2
#define IT i64m2
#define BT b32
#define SETVL __riscv_vsetvl_e64m2
#define REINTERPRET_FU __riscv_vreinterpret_v_f64m2_u64m2
#define REINTERPRET_UF __riscv_vreinterpret_v_u64m2_f64m2
#define REINTERPRET_UI __riscv_vreinterpret_v_u64m2_i64m2
#define UCMPLT __riscv_vmsltu_vx_u64m2_b32
#define GATHER __riscv_vluxei64_v_f64m2
#define UGATHER __riscv_vluxei64_v_u64m2
#define CONVERT __riscv_vfcvt_f_x_v_f64m2
#include "../../kernels/pow/glibc.c"
