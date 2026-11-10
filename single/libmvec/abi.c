/* libmvec public-ABI correctness program (bare metal, AM/TLS).
 *
 * Exercises every one of the 36 shared libmvec entry points exported by the
 * installed GLIBC_2.45 RISC-V libmvec:
 *
 *   log/exp      m1/m2 x fixed/Nx x unmasked/masked   (8 + 8)
 *   sin/cos/pow  m1/m2 x fixed/Nx unmasked            (4 + 4 + 4)
 *   logf/expf    m1/m2 x fixed/Nx unmasked            (4 + 4)
 *
 * Each entry is called directly through its native RVV intrinsic type with
 * the vector calling convention.  The checks cover:
 *
 *   - MPFR-derived bit-pattern oracle (abi-data.h), corpus thresholds;
 *   - short arrays, zero length and guard zones: Nx entries only ever see
 *     complete VLMAX batches, fixed entries only complete fixed batches,
 *     masked entries take a full batch with mask=false beyond the array
 *     (caller memory made legal), the caller handles the remainder;
 *   - masked all-on / all-off / alternating masks with inactive sNaN,
 *     negative and zero lanes: inactive lanes must not raise exceptions and
 *     their output is never stored;
 *   - active sNaN raises NV; active qNaN propagates without NV;
 *   - FRM is restored to the caller's value, results are the RNE result,
 *     pre-accrued sticky flags survive and the entry's own flags are ORed in;
 *   - fixed entries use only their low 2/4 (f64 m1/m2) or 4/8 (f32 m1/m2)
 *     lanes at any VLEN >= 128; VLEN is not required to be exactly 128;
 *   - entries do not depend on the caller's vl CSR.
 *
 * Thresholds and data provenance live in abi-data.h; the generator is
 * abi-probe.py.  No host libm value is used on target.
 *
 * SPDX-License-Identifier: MIT
 */
#include <riscv_vector.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include "../../autovec/av_runtime.h"
#include "abi-data.h"

#define RVV_CC __attribute__ ((riscv_vector_cc))

/* ------------------------------------------------------------------ */
/* Public ABI declarations (native RVV intrinsic types, vector CC).    */

extern vfloat64m1_t _ZGV1N2v_log (vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_log (vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2N4v_log (vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_log (vfloat64m2_t) RVV_CC;
extern vfloat64m1_t _ZGV1M2v_log (vbool64_t, vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Mxv_log (vbool64_t, vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2M4v_log (vbool32_t, vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Mxv_log (vbool32_t, vfloat64m2_t) RVV_CC;

extern vfloat64m1_t _ZGV1N2v_exp (vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_exp (vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2N4v_exp (vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_exp (vfloat64m2_t) RVV_CC;
extern vfloat64m1_t _ZGV1M2v_exp (vbool64_t, vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Mxv_exp (vbool64_t, vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2M4v_exp (vbool32_t, vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Mxv_exp (vbool32_t, vfloat64m2_t) RVV_CC;

extern vfloat64m1_t _ZGV1N2v_sin (vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_sin (vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2N4v_sin (vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_sin (vfloat64m2_t) RVV_CC;

extern vfloat64m1_t _ZGV1N2v_cos (vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_cos (vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2N4v_cos (vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_cos (vfloat64m2_t) RVV_CC;

extern vfloat64m1_t _ZGV1N2vv_pow (vfloat64m1_t, vfloat64m1_t) RVV_CC;
extern vfloat64m1_t _ZGV1Nxvv_pow (vfloat64m1_t, vfloat64m1_t) RVV_CC;
extern vfloat64m2_t _ZGV2N4vv_pow (vfloat64m2_t, vfloat64m2_t) RVV_CC;
extern vfloat64m2_t _ZGV2Nxvv_pow (vfloat64m2_t, vfloat64m2_t) RVV_CC;

extern vfloat32m1_t _ZGV1N4v_logf (vfloat32m1_t) RVV_CC;
extern vfloat32m1_t _ZGV1Nxv_logf (vfloat32m1_t) RVV_CC;
extern vfloat32m2_t _ZGV2N8v_logf (vfloat32m2_t) RVV_CC;
extern vfloat32m2_t _ZGV2Nxv_logf (vfloat32m2_t) RVV_CC;

extern vfloat32m1_t _ZGV1N4v_expf (vfloat32m1_t) RVV_CC;
extern vfloat32m1_t _ZGV1Nxv_expf (vfloat32m1_t) RVV_CC;
extern vfloat32m2_t _ZGV2N8v_expf (vfloat32m2_t) RVV_CC;
extern vfloat32m2_t _ZGV2Nxv_expf (vfloat32m2_t) RVV_CC;

/* ------------------------------------------------------------------ */
/* CSR helpers and bit utilities.                                     */

#define ABI_MAX_LANES 512u

static uint64_t abi_checks, abi_failed, sym_checks, sym_failed;
static size_t abi_vlen_bits;
static int abi_perturb_vl;

static inline unsigned
frm_get (void)
{
  unsigned v;
  __asm__ volatile ("frrm %0" : "=r" (v));
  return v & 7u;
}

static inline void
frm_set (unsigned v)
{
  __asm__ volatile ("fsrm %0" : : "r" (v) : "memory");
}

static inline unsigned
fflags_get (void)
{
  unsigned v;
  __asm__ volatile ("frflags %0" : "=r" (v));
  return v & ABI_FE_ALL;
}

static inline void
fflags_set (unsigned v)
{
  __asm__ volatile ("fsflags %0" : : "r" (v) : "memory");
}

static inline uint64_t
d2b (double d)
{
  uint64_t u;
  memcpy (&u, &d, sizeof u);
  return u;
}

static inline double
b2d (uint64_t u)
{
  double d;
  memcpy (&d, &u, sizeof d);
  return d;
}

static inline uint32_t
f2b (float f)
{
  uint32_t u;
  memcpy (&u, &f, sizeof u);
  return u;
}

static inline float
b2f (uint32_t u)
{
  float f;
  memcpy (&f, &u, sizeof f);
  return f;
}

static inline int
is_nan64 (uint64_t u)
{
  return (u & UINT64_C (0x7ff0000000000000)) == UINT64_C (0x7ff0000000000000)
         && (u & UINT64_C (0x000fffffffffffff)) != 0;
}

static inline int
is_nan32 (uint32_t u)
{
  return (u & 0x7f800000u) == 0x7f800000u && (u & 0x007fffffu) != 0;
}

static inline uint64_t
order64 (uint64_t a)
{
  return (a >> 63) ? ~a : (a | UINT64_C (0x8000000000000000));
}

static inline uint64_t
ulp64 (uint64_t a, uint64_t b)
{
  uint64_t x = order64 (a), y = order64 (b);
  return x > y ? x - y : y - x;
}

static inline uint32_t
order32 (uint32_t a)
{
  return (a >> 31) ? ~a : (a | 0x80000000u);
}

static inline uint32_t
ulp32 (uint32_t a, uint32_t b)
{
  uint32_t x = order32 (a), y = order32 (b);
  return x > y ? x - y : y - x;
}

static void
fail (const char *sym, const char *what, uint64_t got, uint64_t want)
{
  ++sym_failed;
  ++abi_failed;
  printf_ ("FAIL symbol=%s what=%s got=0x%016llx want=0x%016llx\n", sym, what,
           (unsigned long long) got, (unsigned long long) want);
}

#define CHK(sym, what, cond, got, want)                                  \
  do {                                                                   \
    ++sym_checks;                                                        \
    if (!(cond))                                                         \
      fail ((sym), (what), (uint64_t) (got), (uint64_t) (want));         \
  } while (0)

/* ------------------------------------------------------------------ */
/* Direct entry wrappers.  Each wrapper loads the lane batch, calls the
 * public symbol through its native intrinsic signature and stores the
 * result.  Masked wrappers store only active lanes, exactly like the
 * masked store a real caller must use (inactive results are unspecified).
 *
 * abi_perturb_vl makes the caller clobber the vl CSR right before the
 * call; the entry must set its own lane count and ignore it.  The clobber
 * is a volatile vsetivli, not an unused __riscv_vsetvl_* builtin: the
 * compiler is free to delete a builtin whose result is unused, which would
 * leave the test with no real perturbation.  The memory clobber orders the
 * vector loads before the clobber and the call after it.
 */

#define ABI_VSETIVLI(SEW, LM)                                            \
  __asm__ __volatile__ ("vsetivli zero, 1, e" #SEW ", " #LM ", ta, ma"  \
                        ::: "memory")

#define DEF_U64(LM, SYM)                                                 \
  static void u_##SYM (const double *x, size_t vl, double *out)          \
  {                                                                      \
    vfloat64##LM##_t v = __riscv_vle64_v_f64##LM (x, vl);                \
    if (abi_perturb_vl)                                                  \
      ABI_VSETIVLI (64, LM);                                             \
    vfloat64##LM##_t r = SYM (v);                                        \
    __riscv_vse64_v_f64##LM (out, r, vl);                                \
  }

#define DEF_U32(LM, SYM)                                                 \
  static void u_##SYM (const float *x, size_t vl, float *out)            \
  {                                                                      \
    vfloat32##LM##_t v = __riscv_vle32_v_f32##LM (x, vl);                \
    if (abi_perturb_vl)                                                  \
      ABI_VSETIVLI (32, LM);                                             \
    vfloat32##LM##_t r = SYM (v);                                        \
    __riscv_vse32_v_f32##LM (out, r, vl);                                \
  }

#define DEF_B64(LM, SYM)                                                 \
  static void b_##SYM (const double *x, const double *y, size_t vl,      \
                       double *out)                                      \
  {                                                                      \
    vfloat64##LM##_t vx = __riscv_vle64_v_f64##LM (x, vl);               \
    vfloat64##LM##_t vy = __riscv_vle64_v_f64##LM (y, vl);               \
    if (abi_perturb_vl)                                                  \
      ABI_VSETIVLI (64, LM);                                             \
    vfloat64##LM##_t r = SYM (vx, vy);                                   \
    __riscv_vse64_v_f64##LM (out, r, vl);                                \
  }

#define DEF_M64(LM, BOOLB, SYM)                                          \
  static void m_##SYM (const uint8_t *mb, const double *x, size_t vl,    \
                       double *out)                                      \
  {                                                                      \
    double tmp[ABI_MAX_LANES];                                           \
    vbool##BOOLB##_t m = __riscv_vlm_v_b##BOOLB (mb, vl);                \
    vfloat64##LM##_t v = __riscv_vle64_v_f64##LM (x, vl);                \
    if (abi_perturb_vl)                                                  \
      ABI_VSETIVLI (64, LM);                                             \
    vfloat64##LM##_t r = SYM (m, v);                                     \
    __riscv_vse64_v_f64##LM (tmp, r, vl);                                \
    for (size_t i = 0; i < vl; ++i)                                      \
      if ((mb[i >> 3] >> (i & 7)) & 1u)                                  \
        out[i] = tmp[i];                                                 \
  }

#define DEF_M32(LM, BOOLB, SYM)                                          \
  static void m_##SYM (const uint8_t *mb, const float *x, size_t vl,     \
                       float *out)                                       \
  {                                                                      \
    float tmp[ABI_MAX_LANES];                                            \
    vbool##BOOLB##_t m = __riscv_vlm_v_b##BOOLB (mb, vl);                \
    vfloat32##LM##_t v = __riscv_vle32_v_f32##LM (x, vl);                \
    if (abi_perturb_vl)                                                  \
      ABI_VSETIVLI (32, LM);                                             \
    vfloat32##LM##_t r = SYM (m, v);                                     \
    __riscv_vse32_v_f32##LM (tmp, r, vl);                                \
    for (size_t i = 0; i < vl; ++i)                                      \
      if ((mb[i >> 3] >> (i & 7)) & 1u)                                  \
        out[i] = tmp[i];                                                 \
  }

DEF_U64 (m1, _ZGV1N2v_log)
DEF_U64 (m1, _ZGV1Nxv_log)
DEF_U64 (m2, _ZGV2N4v_log)
DEF_U64 (m2, _ZGV2Nxv_log)
DEF_M64 (m1, 64, _ZGV1M2v_log)
DEF_M64 (m1, 64, _ZGV1Mxv_log)
DEF_M64 (m2, 32, _ZGV2M4v_log)
DEF_M64 (m2, 32, _ZGV2Mxv_log)

DEF_U64 (m1, _ZGV1N2v_exp)
DEF_U64 (m1, _ZGV1Nxv_exp)
DEF_U64 (m2, _ZGV2N4v_exp)
DEF_U64 (m2, _ZGV2Nxv_exp)
DEF_M64 (m1, 64, _ZGV1M2v_exp)
DEF_M64 (m1, 64, _ZGV1Mxv_exp)
DEF_M64 (m2, 32, _ZGV2M4v_exp)
DEF_M64 (m2, 32, _ZGV2Mxv_exp)

DEF_U64 (m1, _ZGV1N2v_sin)
DEF_U64 (m1, _ZGV1Nxv_sin)
DEF_U64 (m2, _ZGV2N4v_sin)
DEF_U64 (m2, _ZGV2Nxv_sin)

DEF_U64 (m1, _ZGV1N2v_cos)
DEF_U64 (m1, _ZGV1Nxv_cos)
DEF_U64 (m2, _ZGV2N4v_cos)
DEF_U64 (m2, _ZGV2Nxv_cos)

DEF_B64 (m1, _ZGV1N2vv_pow)
DEF_B64 (m1, _ZGV1Nxvv_pow)
DEF_B64 (m2, _ZGV2N4vv_pow)
DEF_B64 (m2, _ZGV2Nxvv_pow)

DEF_U32 (m1, _ZGV1N4v_logf)
DEF_U32 (m1, _ZGV1Nxv_logf)
DEF_U32 (m2, _ZGV2N8v_logf)
DEF_U32 (m2, _ZGV2Nxv_logf)

DEF_U32 (m1, _ZGV1N4v_expf)
DEF_U32 (m1, _ZGV1Nxv_expf)
DEF_U32 (m2, _ZGV2N8v_expf)
DEF_U32 (m2, _ZGV2Nxv_expf)

/* ------------------------------------------------------------------ */
/* Symbol descriptors and shared test engine.                         */

typedef void (*u64_fn) (const double *, size_t, double *);
typedef void (*u32_fn) (const float *, size_t, float *);
typedef void (*b64_fn) (const double *, const double *, size_t, double *);
typedef void (*m64_fn) (const uint8_t *, const double *, size_t, double *);
typedef void (*m32_fn) (const uint8_t *, const float *, size_t, float *);

struct symdesc
{
  const char *name;
  const char *fam;
  unsigned char single, masked, fixed, binary, lmul, fixed_lanes, ulp;
  const void *cases;
  unsigned ncases;
  u64_fn u64;
  u32_fn u32;
  b64_fn b64;
  m64_fn m64;
  m32_fn m32;
};

#define ABI_CASES_log ABI_CASES_LOG
#define ABI_CASES_exp ABI_CASES_EXP
#define ABI_CASES_sin ABI_CASES_SIN
#define ABI_CASES_cos ABI_CASES_COS
#define ABI_CASES_pow ABI_CASES_POW
#define ABI_CASES_logf ABI_CASES_LOGF
#define ABI_CASES_expf ABI_CASES_EXPF

#define ABI_ULP_log ABI_ULP_LOG
#define ABI_ULP_exp ABI_ULP_EXP
#define ABI_ULP_sin ABI_ULP_SIN
#define ABI_ULP_cos ABI_ULP_COS
#define ABI_ULP_pow ABI_ULP_POW
#define ABI_ULP_logf ABI_ULP_LOGF
#define ABI_ULP_expf ABI_ULP_EXPF

#define SYM_U64(NAME, LM, FIXED, LANES, SYMBOL, FAM) \
  { .name = NAME, .fam = #FAM, .single = 0, .masked = 0, .fixed = FIXED, \
    .binary = 0, .lmul = LM, .fixed_lanes = LANES, .ulp = ABI_ULP_##FAM, \
    .cases = abi_cases_##FAM, .ncases = ABI_CASES_##FAM, \
    .u64 = u_##SYMBOL }
#define SYM_B64(NAME, LM, FIXED, LANES, SYMBOL, FAM) \
  { .name = NAME, .fam = #FAM, .single = 0, .masked = 0, .fixed = FIXED, \
    .binary = 1, .lmul = LM, .fixed_lanes = LANES, .ulp = ABI_ULP_##FAM, \
    .cases = abi_cases_##FAM, .ncases = ABI_CASES_##FAM, \
    .b64 = b_##SYMBOL }
#define SYM_M64(NAME, LM, FIXED, LANES, SYMBOL, FAM) \
  { .name = NAME, .fam = #FAM, .single = 0, .masked = 1, .fixed = FIXED, \
    .binary = 0, .lmul = LM, .fixed_lanes = LANES, .ulp = ABI_ULP_##FAM, \
    .cases = abi_cases_##FAM, .ncases = ABI_CASES_##FAM, \
    .m64 = m_##SYMBOL }
#define SYM_U32(NAME, LM, FIXED, LANES, SYMBOL, FAM) \
  { .name = NAME, .fam = #FAM, .single = 1, .masked = 0, .fixed = FIXED, \
    .binary = 0, .lmul = LM, .fixed_lanes = LANES, .ulp = ABI_ULP_##FAM, \
    .cases = abi_cases_##FAM, .ncases = ABI_CASES_##FAM, \
    .u32 = u_##SYMBOL }

static const struct symdesc syms[] = {
  SYM_U64 ("_ZGV1N2v_log", 1, 1, 2, _ZGV1N2v_log, log),
  SYM_U64 ("_ZGV1Nxv_log", 1, 0, 0, _ZGV1Nxv_log, log),
  SYM_M64 ("_ZGV1M2v_log", 1, 1, 2, _ZGV1M2v_log, log),
  SYM_M64 ("_ZGV1Mxv_log", 1, 0, 0, _ZGV1Mxv_log, log),
  SYM_U64 ("_ZGV2N4v_log", 2, 1, 4, _ZGV2N4v_log, log),
  SYM_U64 ("_ZGV2Nxv_log", 2, 0, 0, _ZGV2Nxv_log, log),
  SYM_M64 ("_ZGV2M4v_log", 2, 1, 4, _ZGV2M4v_log, log),
  SYM_M64 ("_ZGV2Mxv_log", 2, 0, 0, _ZGV2Mxv_log, log),

  SYM_U64 ("_ZGV1N2v_exp", 1, 1, 2, _ZGV1N2v_exp, exp),
  SYM_U64 ("_ZGV1Nxv_exp", 1, 0, 0, _ZGV1Nxv_exp, exp),
  SYM_M64 ("_ZGV1M2v_exp", 1, 1, 2, _ZGV1M2v_exp, exp),
  SYM_M64 ("_ZGV1Mxv_exp", 1, 0, 0, _ZGV1Mxv_exp, exp),
  SYM_U64 ("_ZGV2N4v_exp", 2, 1, 4, _ZGV2N4v_exp, exp),
  SYM_U64 ("_ZGV2Nxv_exp", 2, 0, 0, _ZGV2Nxv_exp, exp),
  SYM_M64 ("_ZGV2M4v_exp", 2, 1, 4, _ZGV2M4v_exp, exp),
  SYM_M64 ("_ZGV2Mxv_exp", 2, 0, 0, _ZGV2Mxv_exp, exp),

  SYM_U64 ("_ZGV1N2v_sin", 1, 1, 2, _ZGV1N2v_sin, sin),
  SYM_U64 ("_ZGV1Nxv_sin", 1, 0, 0, _ZGV1Nxv_sin, sin),
  SYM_U64 ("_ZGV2N4v_sin", 2, 1, 4, _ZGV2N4v_sin, sin),
  SYM_U64 ("_ZGV2Nxv_sin", 2, 0, 0, _ZGV2Nxv_sin, sin),

  SYM_U64 ("_ZGV1N2v_cos", 1, 1, 2, _ZGV1N2v_cos, cos),
  SYM_U64 ("_ZGV1Nxv_cos", 1, 0, 0, _ZGV1Nxv_cos, cos),
  SYM_U64 ("_ZGV2N4v_cos", 2, 1, 4, _ZGV2N4v_cos, cos),
  SYM_U64 ("_ZGV2Nxv_cos", 2, 0, 0, _ZGV2Nxv_cos, cos),

  SYM_B64 ("_ZGV1N2vv_pow", 1, 1, 2, _ZGV1N2vv_pow, pow),
  SYM_B64 ("_ZGV1Nxvv_pow", 1, 0, 0, _ZGV1Nxvv_pow, pow),
  SYM_B64 ("_ZGV2N4vv_pow", 2, 1, 4, _ZGV2N4vv_pow, pow),
  SYM_B64 ("_ZGV2Nxvv_pow", 2, 0, 0, _ZGV2Nxvv_pow, pow),

  SYM_U32 ("_ZGV1N4v_logf", 1, 1, 4, _ZGV1N4v_logf, logf),
  SYM_U32 ("_ZGV1Nxv_logf", 1, 0, 0, _ZGV1Nxv_logf, logf),
  SYM_U32 ("_ZGV2N8v_logf", 2, 1, 8, _ZGV2N8v_logf, logf),
  SYM_U32 ("_ZGV2Nxv_logf", 2, 0, 0, _ZGV2Nxv_logf, logf),

  SYM_U32 ("_ZGV1N4v_expf", 1, 1, 4, _ZGV1N4v_expf, expf),
  SYM_U32 ("_ZGV1Nxv_expf", 1, 0, 0, _ZGV1Nxv_expf, expf),
  SYM_U32 ("_ZGV2N8v_expf", 2, 1, 8, _ZGV2N8v_expf, expf),
  SYM_U32 ("_ZGV2Nxv_expf", 2, 0, 0, _ZGV2Nxv_expf, expf),
};

#define NSYMS (sizeof syms / sizeof syms[0])

struct rcase
{
  uint64_t x, y, ref;
  unsigned req, forbid, kind;
};

union lanebuf
{
  double d[ABI_MAX_LANES];
  float f[ABI_MAX_LANES];
};

static union lanebuf abi_x, abi_y, abi_o;
static struct rcase abi_lc[ABI_MAX_LANES];

static struct rcase
get_case (const struct symdesc *s, unsigned i)
{
  struct rcase c;
  if (s->single)
    {
      const abi_case32 *p = (const abi_case32 *) s->cases + i;
      c.x = p->x;
      c.y = p->y;
      c.ref = p->ref;
      c.req = p->req;
      c.forbid = p->forbid;
      c.kind = p->kind;
    }
  else
    {
      const abi_case64 *p = (const abi_case64 *) s->cases + i;
      c.x = p->x;
      c.y = p->y;
      c.ref = p->ref;
      c.req = p->req;
      c.forbid = p->forbid;
      c.kind = p->kind;
    }
  return c;
}

static void
load_case (const struct symdesc *s, const struct rcase *c, size_t lane)
{
  if (s->single)
    {
      abi_x.f[lane] = b2f ((uint32_t) c->x);
      abi_y.f[lane] = b2f ((uint32_t) c->y);
    }
  else
    {
      abi_x.d[lane] = b2d (c->x);
      abi_y.d[lane] = b2d (c->y);
    }
}

static uint64_t
get_out (const struct symdesc *s, size_t lane)
{
  return s->single ? (uint64_t) f2b (abi_o.f[lane]) : d2b (abi_o.d[lane]);
}

static void
set_out_bits (const struct symdesc *s, size_t lane, uint64_t bits)
{
  if (s->single)
    abi_o.f[lane] = b2f ((uint32_t) bits);
  else
    abi_o.d[lane] = b2d (bits);
}

static uint64_t
get_in_bits (const struct symdesc *s, size_t lane)
{
  return s->single ? (uint64_t) f2b (abi_x.f[lane]) : d2b (abi_x.d[lane]);
}

static void
set_in_bits (const struct symdesc *s, size_t lane, uint64_t bits)
{
  if (s->single)
    abi_x.f[lane] = b2f ((uint32_t) bits);
  else
    abi_x.d[lane] = b2d (bits);
}

/* Scalar remainder path: the caller handles elements the vector batches do
 * not cover with the released scalar libm entry, exactly like the real
 * caller loop, and the same bit oracle verifies the result. */
static uint64_t
scalar_tail (const struct symdesc *s, uint64_t xb, uint64_t yb)
{
  if (s->single)
    {
      float x = b2f ((uint32_t) xb);
      (void) yb; /* no binary float entry in this ABI */
      if (strcmp (s->fam, "logf") == 0)
        return (uint64_t) f2b (logf (x));
      if (strcmp (s->fam, "expf") == 0)
        return (uint64_t) f2b (expf (x));
    }
  else
    {
      double x = b2d (xb), y = b2d (yb);
      if (strcmp (s->fam, "log") == 0)
        return d2b (log (x));
      if (strcmp (s->fam, "exp") == 0)
        return d2b (exp (x));
      if (strcmp (s->fam, "sin") == 0)
        return d2b (sin (x));
      if (strcmp (s->fam, "cos") == 0)
        return d2b (cos (x));
      if (strcmp (s->fam, "pow") == 0)
        return d2b (pow (x, y));
    }
  return 0;
}

static void
fill_bits (const struct symdesc *s, uint64_t bits, size_t from, size_t to)
{
  for (size_t i = from; i < to; ++i)
    {
      set_in_bits (s, i, bits);
      if (s->single)
        abi_y.f[i] = b2f ((uint32_t) bits);
      else
        abi_y.d[i] = b2d (bits);
    }
}

static void
fill_out_guard (const struct symdesc *s, uint64_t bits, size_t n)
{
  for (size_t i = 0; i < n; ++i)
    set_out_bits (s, i, bits);
}

static void
mask_fill (uint8_t *mb, size_t vl, int mode, size_t first_valid)
{
  size_t bytes = (vl + 7) / 8;
  memset (mb, 0, bytes + 1);
  for (size_t i = 0; i < vl; ++i)
    {
      int on = 0;
      if (mode == 1)
        on = 1;
      else if (mode == 2)
        on = (i & 1) == 0;
      else if (mode == 3)
        on = i < first_valid;
      if (on)
        mb[i >> 3] |= (uint8_t) (1u << (i & 7));
    }
}

/* Call the entry on the lane batch starting at `off`.  Unmasked batches
 * cover exactly vl lanes; masked batches store only active lanes, so the
 * caller remains responsible for the inactive output lanes. */
static void
apply_call (const struct symdesc *s, const uint8_t *mb, size_t vl, size_t off)
{
  if (s->single)
    s->u32 (abi_x.f + off, vl, abi_o.f + off);
  else if (s->masked)
    s->m64 (mb, abi_x.d + off, vl, abi_o.d + off);
  else if (s->binary)
    s->b64 (abi_x.d + off, abi_y.d + off, vl, abi_o.d + off);
  else
    s->u64 (abi_x.d + off, vl, abi_o.d + off);
}

static void
verify_lane (const struct symdesc *s, const struct rcase *c, uint64_t got)
{
  ++sym_checks;
  if (c->kind == ABI_KIND_NAN)
    {
      if (!(s->single ? is_nan32 ((uint32_t) got) : is_nan64 (got)))
        fail (s->name, "nan-class", got, c->ref);
      return;
    }
  if (c->kind == ABI_KIND_EXACT)
    {
      if (got != c->ref)
        fail (s->name, "exact", got, c->ref);
      return;
    }
  uint64_t d = s->single ? ulp32 ((uint32_t) got, (uint32_t) c->ref)
                         : ulp64 (got, c->ref);
  if (d > s->ulp)
    fail (s->name, "ulp", got, c->ref);
}

static void
verify_flags (const struct symdesc *s, unsigned got, const struct rcase *c)
{
  ++sym_checks;
  if ((got & c->req) != c->req)
    fail (s->name, "required-fflags", got, c->req);
  ++sym_checks;
  if ((got & c->forbid) != 0)
    fail (s->name, "forbidden-fflags", got, c->forbid);
}

/* ------------------------------------------------------------------ */

/* Per-case replicated call: all lanes carry the same case, so the flag
 * budget of that case can be checked exactly and every lane is verified. */
static void
run_data_cases (const struct symdesc *s, size_t N)
{
  uint8_t mb[(ABI_MAX_LANES + 7) / 8 + 1];
  mask_fill (mb, N, 1, 0);

  for (unsigned i = 0; i < s->ncases; ++i)
    {
      struct rcase c = get_case (s, i);
      for (size_t l = 0; l < N; ++l)
        load_case (s, &c, l);

      fflags_set (0);
      frm_set (0);
      apply_call (s, mb, N, 0);
      unsigned fl = fflags_get (), fr = frm_get ();

      CHK (s->name, "frm-restored", fr == 0, fr, 0);
      for (size_t l = 0; l < N; ++l)
        verify_lane (s, &c, get_out (s, l));
      verify_flags (s, fl, &c);
    }
}

/* Mixed finite cases, N distinct lanes per call: catches lane-crossing and
 * per-lane masking mistakes. */
static void
run_chunk_sweep (const struct symdesc *s, size_t N)
{
  uint8_t mb[(ABI_MAX_LANES + 7) / 8 + 1];
  mask_fill (mb, N, 1, 0);
  const unsigned fin = ABI_DATA_FINITE_PER_FUNC;

  for (unsigned base = 0; base < fin; base += N)
    {
      struct rcase cs[ABI_MAX_LANES];
      for (size_t l = 0; l < N; ++l)
        {
          cs[l] = get_case (s, (base + l) % fin);
          load_case (s, &cs[l], l);
        }
      fflags_set (0);
      frm_set (0);
      apply_call (s, mb, N, 0);
      unsigned fl = fflags_get (), fr = frm_get ();

      CHK (s->name, "sweep-frm", fr == 0, fr, 0);
      for (size_t l = 0; l < N; ++l)
        verify_lane (s, &cs[l], get_out (s, l));
      CHK (s->name, "sweep-nv", (fl & ABI_FE_NV) == 0, fl, 0);
      CHK (s->name, "sweep-dz", (fl & ABI_FE_DZ) == 0, fl, 0);
    }
}

/* FRM is restored, the RNE result is retained under caller FRM, sticky
 * flags survive and the entry's own flags are ORed in. */
static void
run_fenv (const struct symdesc *s, size_t N)
{
  static const unsigned priors[4] = {
    0, ABI_FE_NX, ABI_FE_UF | ABI_FE_OF, ABI_FE_ALL
  };
  uint8_t mb[(ABI_MAX_LANES + 7) / 8 + 1];
  uint64_t base_bits[ABI_MAX_LANES];
  struct rcase c = get_case (s, 0);

  mask_fill (mb, N, 1, 0);
  for (size_t l = 0; l < N; ++l)
    load_case (s, &c, l);

  fflags_set (0);
  frm_set (0);
  apply_call (s, mb, N, 0);
  unsigned base_flags = fflags_get ();
  CHK (s->name, "fenv-base-frm", frm_get () == 0, frm_get (), 0);
  for (size_t l = 0; l < N; ++l)
    base_bits[l] = get_out (s, l);

  for (unsigned mode = 0; mode <= 4; ++mode)
    for (unsigned p = 0; p < 4; ++p)
      {
        fflags_set (priors[p]);
        frm_set (mode);
        apply_call (s, mb, N, 0);
        unsigned fl = fflags_get (), fr = frm_get ();

        for (size_t l = 0; l < N; ++l)
          {
            ++sym_checks;
            if (get_out (s, l) != base_bits[l])
              fail (s->name, "frm-result", get_out (s, l), base_bits[l]);
          }
        CHK (s->name, "frm-restored", fr == mode, fr, mode);
        CHK (s->name, "sticky-flags", fl == (priors[p] | base_flags), fl,
             priors[p] | base_flags);
      }
  frm_set (0);
  fflags_set (0);
}

/* Short arrays, zero length and guard zones.  Unmasked entries only ever
 * see complete batches and the caller maps the remainder; masked entries
 * take complete batches with mask=false beyond the array. */
static void
run_adapt (const struct symdesc *s, size_t N)
{
  size_t ns[6];
  unsigned nn = 0;
  size_t cand[6] = { 0, 1, N > 2 ? N - 1 : 0, N, N + 1, 2 * N + 3 };
  for (unsigned i = 0; i < 6; ++i)
    {
      int dup = 0;
      for (unsigned j = 0; j < nn; ++j)
        if (ns[j] == cand[i])
          dup = 1;
      if (!dup)
        ns[nn++] = cand[i];
    }

  const size_t cap = 3 * N + 8;
  if (cap > ABI_MAX_LANES)
    return;
  const uint64_t guard = s->single ? UINT64_C (0x5a5a5a5a)
                                   : UINT64_C (0x5a5a5a5a5a5a5a5a);
  const uint64_t snan = s->single ? UINT64_C (0x7f800001)
                                  : UINT64_C (0x7ff0000000000001);
  const unsigned fin = ABI_DATA_FINITE_PER_FUNC;
  uint8_t mb[(ABI_MAX_LANES + 7) / 8 + 1];

  for (unsigned k = 0; k < nn; ++k)
    {
      size_t n = ns[k];
      /* Every length starts from a clean exception/rounding state: residual
       * flags from the previous n must not leak into this one. */
      fflags_set (0);
      frm_set (0);
      fill_bits (s, guard, 0, cap);
      fill_out_guard (s, guard, cap);
      for (size_t i = 0; i < n; ++i)
        {
          abi_lc[i] = get_case (s, (unsigned) (i % fin));
          load_case (s, &abi_lc[i], i);
        }
      if (s->masked)
        fill_bits (s, snan, n, cap);

      if (s->masked)
        {
          size_t done = 0;
          while (done < n)
            {
              size_t valid = n - done;
              if (valid > N)
                valid = N;
              mask_fill (mb, N, 3, valid);
              fflags_set (0);
              frm_set (0);
              apply_call (s, mb, N, done);
              unsigned fl = fflags_get ();
              CHK (s->name, "adapt-mask-frm", frm_get () == 0, frm_get (), 0);
              CHK (s->name, "adapt-inactive-nv", (fl & ABI_FE_NV) == 0, fl, 0);
              CHK (s->name, "adapt-inactive-dz", (fl & ABI_FE_DZ) == 0, fl, 0);
              for (size_t l = 0; l < valid; ++l)
                verify_lane (s, &abi_lc[done + l], get_out (s, done + l));
              done += valid;
            }
        }
      else
        {
          size_t done = 0;
          while (done + N <= n)
            {
              fflags_set (0);
              frm_set (0);
              apply_call (s, NULL, N, done);
              unsigned fl = fflags_get ();
              CHK (s->name, "adapt-frm", frm_get () == 0, frm_get (), 0);
              CHK (s->name, "adapt-nv", (fl & ABI_FE_NV) == 0, fl, 0);
              CHK (s->name, "adapt-dz", (fl & ABI_FE_DZ) == 0, fl, 0);
              for (size_t l = 0; l < N; ++l)
                verify_lane (s, &abi_lc[done + l], get_out (s, done + l));
              done += N;
            }
          if (done < n)
            {
              /* Scalar remainder: really call the released scalar libm
               * entry for the leftover elements and verify them with the
               * same oracle; only NV/DZ are forbidden for finite inputs,
               * NX/UF/OF are legitimate scalar-libm exceptions. */
              fflags_set (0);
              frm_set (0);
              for (; done < n; ++done)
                {
                  uint64_t yb = s->single ? (uint64_t) f2b (abi_y.f[done])
                                          : d2b (abi_y.d[done]);
                  uint64_t got = scalar_tail (s, get_in_bits (s, done), yb);
                  set_out_bits (s, done, got);
                  verify_lane (s, &abi_lc[done], got);
                }
              CHK (s->name, "adapt-tail-frm", frm_get () == 0, frm_get (), 0);
              CHK (s->name, "adapt-tail-nv",
                   (fflags_get () & (ABI_FE_NV | ABI_FE_DZ)) == 0,
                   fflags_get (), 0);
            }
        }
      if (n == 0)
        {
          ++sym_checks;
          if (fflags_get () != 0)
            fail (s->name, "adapt-zero-flags", fflags_get (), 0);
        }

      /* Everything at or beyond the logical length must be untouched. */
      for (size_t i = n; i < cap; ++i)
        {
          ++sym_checks;
          if (get_out (s, i) != guard)
            fail (s->name, "guard", get_out (s, i), guard);
        }
    }
}

/* The caller's vl CSR value is not part of the entry contract. */
static void
run_vl_independence (const struct symdesc *s, size_t N)
{
  uint8_t mb[(ABI_MAX_LANES + 7) / 8 + 1];
  uint64_t base[ABI_MAX_LANES];
  struct rcase c = get_case (s, 0);

  mask_fill (mb, N, 1, 0);
  for (size_t l = 0; l < N; ++l)
    load_case (s, &c, l);

  fflags_set (0);
  frm_set (0);
  abi_perturb_vl = 0;
  apply_call (s, mb, N, 0);
  for (size_t l = 0; l < N; ++l)
    base[l] = get_out (s, l);

  fflags_set (0);
  frm_set (0);
  abi_perturb_vl = 1;
  apply_call (s, mb, N, 0);
  abi_perturb_vl = 0;
  for (size_t l = 0; l < N; ++l)
    {
      ++sym_checks;
      if (get_out (s, l) != base[l])
        fail (s->name, "vl-independent", get_out (s, l), base[l]);
    }
  CHK (s->name, "vl-frm", frm_get () == 0, frm_get (), 0);
}

/* Masked-only semantics: inactive lanes must never execute FP work and
 * must never be stored; active sNaN raises NV, active qNaN does not. */
static void
run_masked_semantics (const struct symdesc *s, size_t N)
{
  uint8_t mb[(ABI_MAX_LANES + 7) / 8 + 1];
  const int islog = strcmp (s->fam, "log") == 0;
  const uint64_t snan = s->single ? UINT64_C (0x7f800001)
                                  : UINT64_C (0x7ff0000000000001);
  const uint64_t qnan = s->single ? UINT64_C (0x7fc00000)
                                  : UINT64_C (0x7ff8000000000000);
  const uint64_t neg = s->single ? UINT64_C (0xbf800000)
                                 : UINT64_C (0xbff0000000000000);
  const uint64_t zero = s->single ? UINT64_C (0x80000000)
                                  : UINT64_C (0x8000000000000000);
  const uint64_t big = s->single ? UINT64_C (0x7f000000)
                                 : UINT64_C (0x7fe0000000000000);
  const uint64_t active = islog ? (s->single ? UINT64_C (0x3f800000)
                                             : UINT64_C (0x3ff0000000000000))
                                : (s->single ? UINT64_C (0x00000000)
                                             : UINT64_C (0x0000000000000000));
  const uint64_t active_ref = islog ? UINT64_C (0x00000000)
                                    : (s->single ? UINT64_C (0x3f800000)
                                                 : UINT64_C (0x3ff0000000000000));
  const uint64_t guard = s->single ? UINT64_C (0x5a5a5a5a)
                                   : UINT64_C (0x5a5a5a5a5a5a5a5a);

  /* All-off with every lane poisoned: no FP operation at all. */
  for (size_t i = 0; i < N; ++i)
    {
      uint64_t p = (i & 1) ? snan : (islog ? ((i & 2) ? neg : zero) : big);
      set_in_bits (s, i, p);
    }
  fill_out_guard (s, guard, N);
  mask_fill (mb, N, 0, 0);
  fflags_set (0);
  frm_set (0);
  apply_call (s, mb, N, 0);
  CHK (s->name, "alloff-flags", fflags_get () == 0, fflags_get (), 0);
  CHK (s->name, "alloff-frm", frm_get () == 0, frm_get (), 0);
  for (size_t i = 0; i < N; ++i)
    {
      ++sym_checks;
      if (get_out (s, i) != guard)
        fail (s->name, "alloff-store", get_out (s, i), guard);
    }

  /* Alternating mask with inactive poison and exact active results. */
  for (size_t i = 0; i < N; ++i)
    {
      uint64_t p = (i & 1) ? ((i & 2) ? neg : snan) : active;
      set_in_bits (s, i, p);
    }
  fill_out_guard (s, guard, N);
  mask_fill (mb, N, 2, 0);
  fflags_set (0);
  frm_set (0);
  apply_call (s, mb, N, 0);
  CHK (s->name, "alt-flags", fflags_get () == 0, fflags_get (), 0);
  CHK (s->name, "alt-frm", frm_get () == 0, frm_get (), 0);
  for (size_t i = 0; i < N; ++i)
    {
      ++sym_checks;
      if ((i & 1) == 0)
        {
          if (get_out (s, i) != active_ref)
            fail (s->name, "alt-active", get_out (s, i), active_ref);
        }
      else if (get_out (s, i) != guard)
        fail (s->name, "alt-inactive-store", get_out (s, i), guard);
    }

  /* One active sNaN at every lane position; prior sticky flag must stay. */
  for (size_t k = 0; k < N; ++k)
    {
      for (size_t i = 0; i < N; ++i)
        {
          uint64_t p = (i & 1) ? neg : ((i & 2) ? zero : big);
          set_in_bits (s, i, p);
        }
      set_in_bits (s, k, snan);
      fill_out_guard (s, guard, N);
      memset (mb, 0, (N + 7) / 8 + 1);
      mb[k >> 3] |= (uint8_t) (1u << (k & 7));
      fflags_set (ABI_FE_NX);
      frm_set (2);
      apply_call (s, mb, N, 0);
      unsigned fl = fflags_get (), fr = frm_get ();
      CHK (s->name, "snan-nv", (fl & ABI_FE_NV) != 0, fl, ABI_FE_NV);
      CHK (s->name, "snan-sticky", (fl & ABI_FE_NX) != 0, fl, ABI_FE_NX);
      CHK (s->name, "snan-frm", fr == 2, fr, 2);
      ++sym_checks;
      if (!(s->single ? is_nan32 ((uint32_t) get_out (s, k))
                      : is_nan64 (get_out (s, k))))
        fail (s->name, "snan-result", get_out (s, k), 0);
    }

  /* Active qNaN is quiet: NaN propagates without NV. */
  for (size_t i = 0; i < N; ++i)
    {
      uint64_t p = (i & 2) ? neg : zero;
      set_in_bits (s, i, p);
    }
  set_in_bits (s, 0, qnan);
  fill_out_guard (s, guard, N);
  memset (mb, 0, (N + 7) / 8 + 1);
  mb[0] = 1;
  fflags_set (0);
  frm_set (0);
  apply_call (s, mb, N, 0);
  CHK (s->name, "qnan-flags", (fflags_get () & ABI_FE_NV) == 0,
       fflags_get (), 0);
  ++sym_checks;
  if (!(s->single ? is_nan32 ((uint32_t) get_out (s, 0))
                  : is_nan64 (get_out (s, 0))))
    fail (s->name, "qnan-result", get_out (s, 0), 0);
  frm_set (0);
  fflags_set (0);
}

/* ------------------------------------------------------------------ */

static size_t
vlmax_of (const struct symdesc *s)
{
  if (s->single)
    return s->lmul == 1 ? __riscv_vsetvlmax_e32m1 () : __riscv_vsetvlmax_e32m2 ();
  return s->lmul == 1 ? __riscv_vsetvlmax_e64m1 () : __riscv_vsetvlmax_e64m2 ();
}

static void
run_symbol (const struct symdesc *s)
{
  sym_checks = 0;
  sym_failed = 0;

  size_t N;
  if (s->fixed)
    {
      N = s->fixed_lanes;
      size_t want = (s->single ? 4u : 2u) * s->lmul;
      if (N != want)
        {
          printf_ ("LIBMVEC_ERROR case=abi-public symbol=%s reason=fixed-lanes\n",
                   s->name);
          abi_failed++;
          sym_failed++;
          return;
        }
    }
  else
    N = vlmax_of (s);

  if (N == 0 || N > ABI_MAX_LANES / 3)
    {
      printf_ ("LIBMVEC_ERROR case=abi-public symbol=%s reason=vlmax=%llu\n",
               s->name, (unsigned long long) N);
      abi_failed++;
      sym_failed++;
      return;
    }

  if (N > ABI_MAX_LANES)
    {
      printf_ ("LIBMVEC_ERROR case=abi-public symbol=%s reason=lanes\n", s->name);
      abi_failed++;
      sym_failed++;
      return;
    }

  run_data_cases (s, N);
  run_chunk_sweep (s, N);
  run_fenv (s, N);
  run_adapt (s, N);
  run_vl_independence (s, N);
  if (s->masked)
    run_masked_semantics (s, N);

  abi_checks += sym_checks;
  printf_ ("LIBMVEC_ABI symbol=%s checks=%llu failed=%llu\n", s->name,
           (unsigned long long) sym_checks, (unsigned long long) sym_failed);
}

int
main (void)
{
  abi_vlen_bits = __riscv_vlenb () * 8;
  if (abi_vlen_bits < 128)
    {
      printf_ ("LIBMVEC_ERROR case=abi-public reason=vlen_bits=%llu\n",
               (unsigned long long) abi_vlen_bits);
      return 1;
    }

  printf_ ("LIBMVEC_CONFIG case=abi-public function=abi vlen_bits=%llu "
           "seed=123456789 rounds=0\n", (unsigned long long) abi_vlen_bits);

  for (unsigned i = 0; i < NSYMS; ++i)
    run_symbol (&syms[i]);

  printf_ ("LIBMVEC_CHECK case=abi-public checks=%llu failed=%llu\n",
           (unsigned long long) abi_checks, (unsigned long long) abi_failed);
  if (abi_failed != 0)
    {
      printf_ ("LIBMVEC_ERROR case=abi-public checks=%llu failed=%llu\n",
               (unsigned long long) abi_checks, (unsigned long long) abi_failed);
      return 1;
    }
  printf_ ("LIBMVEC_COMPLETE case=abi-public status=pass\n");
  return 0;
}
