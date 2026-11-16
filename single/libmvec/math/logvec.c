/* Experimental vector implementation derived from GNU libc e_log.c.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   See LICENSES.md. Installed libraries and public vector ABI are untouched. */
#include <math.h>
#include <stdint.h>
#include <riscv_vector.h>
#include "newlib.h"
#include "logvec.h"
#include "logvec_data.h"
#define ENTRY logvec_m1
#define CORE logvec_core_m1
#define NX _ZGV1Nxv_log
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
#define UCMPGT __riscv_vmsgtu_vx_u64m1_b64
#define GATHER __riscv_vluxei64_v_f64m1
#define CONVERT __riscv_vfcvt_f_x_v_f64m1
/* Only m1 array may opt into the full inline baseline core.  Defining the
   option to 0 disables it; LOGVEC_BASELINE already uses that core everywhere. */
#if defined(LOGVEC_ARRAY_M1_BASELINE) && LOGVEC_ARRAY_M1_BASELINE && !defined(LOGVEC_BASELINE)
#define LOGVEC_M1_ARRAY_FULL 1
#endif
#define LOGVEC_NO_BARRIER 1
#define LOGVEC_USE_MACC 1
#include "logvec_impl.inc.h"
#undef LOGVEC_USE_MACC
#undef LOGVEC_NO_BARRIER
#undef LOGVEC_M1_ARRAY_FULL
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
#undef BV
#undef FT
#undef UT
#undef IT
#undef BT
#undef SETVL
#undef REINTERPRET_FU
#undef REINTERPRET_UF
#undef REINTERPRET_UI
#undef UCMPLT
#undef UCMPGT
#undef GATHER
#undef CONVERT
#define ENTRY logvec_m2
#define CORE logvec_core_m2
#define NX _ZGV2Nxv_log
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
#define UCMPGT __riscv_vmsgtu_vx_u64m2_b32
#define GATHER __riscv_vluxei64_v_f64m2
#define CONVERT __riscv_vfcvt_f_x_v_f64m2
#include "logvec_impl.inc.h"
