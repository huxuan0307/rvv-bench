/* Paired exact-input probes, included once per libmvec function.
 *
 * The including translation unit (paired_<function>.c) must define:
 *
 *   PAIRED_FN         function token: atan / atan2 / acos / log10 / sin / exp
 *   PAIRED_FN_STR     C string with the function name
 *   PAIRED_BINARY     0 for unary entries, 1 for atan2
 *   PAIRED_DATA_HEADER  the frozen corpus header of that function
 *   PAIRED_GROUP      symbol name of the exported pair_probe_group
 *   PAIRED_TABLE      symbol name of the static probe table
 *   PAIRED_SEED_INIT / PAIRED_SEED0 / PAIRED_SEED1 / PAIRED_SEED_FNV /
 *   PAIRED_PAIR_FNV / PAIRED_SEED_COUNT
 *                     exported seed-identity interface used by paired_main.c
 *
 * Everything else (probe bodies, case table) is generated here, so the
 * functions cannot drift apart in protocol details.
 */
#include <stdint.h>
#include "av_runtime.h"
#include <riscv_vector.h>

#include "paired_protocol.h"
#include "paired_bridge.h"
#include PAIRED_DATA_HEADER

#ifndef LAT_RVV_CC
#define LAT_RVV_CC __attribute__ ((riscv_vector_cc))
#endif

#ifndef PAIRED_SCAL_M1_LANES
#define PAIRED_SCAL_M1_LANES 2
#endif
#ifndef PAIRED_SCAL_M2_LANES
#define PAIRED_SCAL_M2_LANES 4
#endif
#ifndef PAIR_MAX_LANES
#define PAIR_MAX_LANES 8
#endif

/* ---- symbol mangling --------------------------------------------------- */
#define PAIR_CAT(a, b) a##b
#define PAIR_CAT2(a, b) PAIR_CAT (a, b)
#define PAIR_PROBE(tag)                                                       \
  PAIR_CAT2 (PAIR_CAT2 (probe_, PAIRED_FN), PAIR_CAT2 (_, tag))
#define PAIR_SYM_1(tail) PAIR_CAT2 (_ZGV1, PAIR_CAT2 (tail, PAIRED_FN))
#define PAIR_SYM_2(tail) PAIR_CAT2 (_ZGV2, PAIR_CAT2 (tail, PAIRED_FN))

#if PAIRED_BINARY
extern vfloat64m1_t PAIR_SYM_1 (N2vv_) (vfloat64m1_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t PAIR_SYM_1 (Nxvv_) (vfloat64m1_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t PAIR_SYM_1 (M2vv_) (vbool64_t, vfloat64m1_t,
                                        vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t PAIR_SYM_1 (Mxvv_) (vbool64_t, vfloat64m1_t,
                                        vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (N4vv_) (vfloat64m2_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (Nxvv_) (vfloat64m2_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (M4vv_) (vbool32_t, vfloat64m2_t,
                                        vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (Mxvv_) (vbool32_t, vfloat64m2_t,
                                        vfloat64m2_t) LAT_RVV_CC;

#define PAIR_CALL_U_FIX_M1(y, x) PAIR_SYM_1 (N2vv_) ((y), (x))
#define PAIR_CALL_U_SC_M1(y, x) PAIR_SYM_1 (Nxvv_) ((y), (x))
#define PAIR_CALL_U_FIX_M2(y, x) PAIR_SYM_2 (N4vv_) ((y), (x))
#define PAIR_CALL_U_SC_M2(y, x) PAIR_SYM_2 (Nxvv_) ((y), (x))
#define PAIR_CALL_UM_FIX_M1(y, x) PAIR_SYM_1 (M2vv_) (mask, (y), (x))
#define PAIR_CALL_UM_SC_M1(y, x) PAIR_SYM_1 (Mxvv_) (mask, (y), (x))
#define PAIR_CALL_UM_FIX_M2(y, x) PAIR_SYM_2 (M4vv_) (mask, (y), (x))
#define PAIR_CALL_UM_SC_M2(y, x) PAIR_SYM_2 (Mxvv_) (mask, (y), (x))
#else
extern vfloat64m1_t PAIR_SYM_1 (N2v_) (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t PAIR_SYM_1 (Nxv_) (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t PAIR_SYM_1 (M2v_) (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t PAIR_SYM_1 (Mxv_) (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (N4v_) (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (Nxv_) (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (M4v_) (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t PAIR_SYM_2 (Mxv_) (vbool32_t, vfloat64m2_t) LAT_RVV_CC;

#define PAIR_CALL_U_FIX_M1(x) PAIR_SYM_1 (N2v_) (x)
#define PAIR_CALL_U_SC_M1(x) PAIR_SYM_1 (Nxv_) (x)
#define PAIR_CALL_U_FIX_M2(x) PAIR_SYM_2 (N4v_) (x)
#define PAIR_CALL_U_SC_M2(x) PAIR_SYM_2 (Nxv_) (x)
#define PAIR_CALL_UM_FIX_M1(x) PAIR_SYM_1 (M2v_) (mask, (x))
#define PAIR_CALL_UM_SC_M1(x) PAIR_SYM_1 (Mxv_) (mask, (x))
#define PAIR_CALL_UM_FIX_M2(x) PAIR_SYM_2 (M4v_) (mask, (x))
#define PAIR_CALL_UM_SC_M2(x) PAIR_SYM_2 (Mxv_) (mask, (x))
#endif

/* ---- per-function frozen seed storage ---------------------------------- */
static double pair_fn_seed0[PAIRED_MAX_N] __attribute__ ((aligned (64)));
static double pair_fn_seed1[PAIRED_MAX_N] __attribute__ ((aligned (64)));
static unsigned pair_fn_seed_n;

void
PAIRED_SEED_INIT (unsigned n)
{
  if (n > DATA_COUNT)
    n = DATA_COUNT;
  if (n > PAIRED_MAX_N)
    n = PAIRED_MAX_N;
  for (unsigned i = 0; i < n; ++i)
    __builtin_memcpy (&pair_fn_seed0[i], &input_bits[i], sizeof (double));
#if PAIRED_BINARY
  for (unsigned i = 0; i < n; ++i)
    __builtin_memcpy (&pair_fn_seed1[i], &second_bits[i], sizeof (double));
#endif
  pair_fn_seed_n = n;
}

const double *
PAIRED_SEED0 (void)
{
  return pair_fn_seed0;
}

const double *
PAIRED_SEED1 (void)
{
#if PAIRED_BINARY
  return pair_fn_seed1;
#else
  return pair_fn_seed0;
#endif
}

unsigned
PAIRED_SEED_COUNT (void)
{
  return pair_fn_seed_n;
}

/* FNV-1a over the exact seed bit patterns of the first n elements (unary
 * operand).  paired_main.c prints it and the host recomputes it from the
 * frozen header file, so the guest proves which bytes it actually consumed. */
uint64_t
PAIRED_SEED_FNV (unsigned n)
{
  uint64_t h = PAIR_FNV_INIT;
  for (unsigned i = 0; i < n; ++i)
    h = pair_fnv (h, input_bits[i]);
  return h;
}

/* FNV-1a over the (y,x) pair order of the first n elements (atan2). */
uint64_t
PAIRED_PAIR_FNV (unsigned n)
{
  uint64_t h = PAIR_FNV_INIT;
  for (unsigned i = 0; i < n; ++i)
    {
      h = pair_fnv (h, input_bits[i]);
#if PAIRED_BINARY
      h = pair_fnv (h, second_bits[i]);
#endif
    }
  return h;
}

/* ---- per-TU helpers ---------------------------------------------------- */
static double pair_tbuf[PAIR_MAX_LANES] __attribute__ ((aligned (64)));
static double pair_tbuf2[PAIR_MAX_LANES] __attribute__ ((aligned (64)));
static double pair_tbuf3[PAIR_MAX_LANES] __attribute__ ((aligned (64)));
static uint8_t pair_mbuf[PAIR_MAX_LANES] __attribute__ ((aligned (64)));

static const double pair_zero_v[PAIR_MAX_LANES] __attribute__ ((aligned (64)))
= { 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0 };

static void
pair_oracle_check (const double *out, unsigned lanes, const uint64_t *ref,
                   unsigned idx, const char *entry)
{
  for (unsigned i = 0; i < lanes; ++i)
    {
      ++pair_checks;
      uint64_t got = pair_bits (out + i);
      uint64_t a = pair_ordered (got), b = pair_ordered (ref[idx + i]);
      uint64_t d = a > b ? a - b : b - a;
      if (!pair_isfinite_bits (got) || d > ORACLE_LIMIT)
        {
          if (pair_failed++ < 8)
            printf (
                "PAIR_ERROR,entry=%s,index=%u,got=%llx,want=%llx,ulp=%llu\n",
                entry, idx + i, (unsigned long long)got,
                (unsigned long long)ref[idx + i], (unsigned long long)d);
        }
    }
}

#define PAIR_SEED_AT(i) pair_seed_vec (pair_seed0, (i), (lanes))
#if PAIRED_BINARY
#define PAIR_SEED2_AT(i) pair_seed_vec (pair_seed1, (i), (lanes))
#else
#define PAIR_SEED2_AT(i) PAIR_SEED_AT (i)
#endif

#define PAIR_TRACE_INIT(w)                                                    \
  do                                                                          \
    {                                                                         \
      (w)->arg_fnv = PAIR_FNV_INIT;                                           \
      (w)->mask_fnv = PAIR_FNV_INIT;                                          \
      (w)->zero_bad = 0;                                                      \
      (w)->recover_bad = 0;                                                   \
      (w)->finite_bad = 0;                                                    \
    }                                                                         \
  while (0)

#define PAIR_SCRUB_ID(r) (r)
#define PAIR_SCRUB_M1(r) lat_pair_san_m1 ((r), mask, vl)
#define PAIR_SCRUB_M2(r) lat_pair_san_m2 ((r), mask, vl)

/* ---- trace bodies ------------------------------------------------------ */
#if PAIRED_TRACE

#define PAIR_TRACE_MASK(w, SM, mask)                                          \
  do                                                                          \
    {                                                                         \
      SM (pair_mbuf, (mask), vl);                                             \
      for (unsigned _mi = 0; _mi < (lanes); ++_mi)                            \
        (w)->mask_fnv = pair_fnv ((w)->mask_fnv, pair_mbuf[_mi]);             \
    }                                                                         \
  while (0)

#define PAIR_TRACE_LAT_UNARY(TAG, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,    \
                             SCRUB)                                           \
  do                                                                          \
    {                                                                         \
      unsigned _lanes = (LANES);                                              \
      unsigned _ncall = pair_n / _lanes;                                      \
      unsigned _tsteps = 2 * _ncall;                                          \
      VT _x = LOAD (PAIR_SEED_AT (0), vl), _r, _d, _s, _nx;                   \
      for (unsigned _t = 0; _t < _tsteps; ++_t)                               \
        {                                                                     \
          STORE (pair_tbuf, _x, vl);                                          \
          w->arg_fnv = pair_fnv_buf (w->arg_fnv, pair_tbuf, _lanes);          \
          _r = CALL (_x);                                                     \
          STORE (pair_tbuf, _r, vl);                                          \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (!pair_isfinite_bits (pair_bits (pair_tbuf + _i)))             \
              ++w->finite_bad;                                                \
          _d = ZERO (SCRUB (_r));                                             \
          STORE (pair_tbuf, _d, vl);                                          \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (pair_bits (pair_tbuf + _i) != 0ULL)                           \
              ++w->zero_bad;                                                  \
          _s = LOAD (PAIR_SEED_AT ((_t + 1) % _ncall), vl);                   \
          STORE (pair_tbuf2, _s, vl);                                         \
          _nx = ADD (_s, _d);                                                 \
          STORE (pair_tbuf, _nx, vl);                                         \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (pair_bits (pair_tbuf + _i) != pair_bits (pair_tbuf2 + _i))    \
              ++w->recover_bad;                                               \
          _x = _nx;                                                           \
        }                                                                     \
      w->steps = _tsteps;                                                     \
      w->calls = _tsteps;                                                     \
    }                                                                         \
  while (0)

#define PAIR_TRACE_LAT_BINARY(TAG, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,   \
                              SCRUB)                                          \
  do                                                                          \
    {                                                                         \
      unsigned _lanes = (LANES);                                              \
      unsigned _ncall = pair_n / _lanes;                                      \
      unsigned _tsteps = 2 * _ncall;                                          \
      VT _y = LOAD (PAIR_SEED_AT (0), vl), _x = LOAD (PAIR_SEED2_AT (0), vl); \
      VT _r, _d, _sy, _sx, _ny, _nx;                                          \
      for (unsigned _t = 0; _t < _tsteps; ++_t)                               \
        {                                                                     \
          STORE (pair_tbuf, _y, vl);                                          \
          STORE (pair_tbuf2, _x, vl);                                         \
          w->arg_fnv = pair_fnv_buf (w->arg_fnv, pair_tbuf, _lanes);          \
          w->arg_fnv = pair_fnv_buf (w->arg_fnv, pair_tbuf2, _lanes);         \
          _r = CALL (_y, _x);                                                 \
          STORE (pair_tbuf, _r, vl);                                          \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (!pair_isfinite_bits (pair_bits (pair_tbuf + _i)))             \
              ++w->finite_bad;                                                \
          _d = ZERO (SCRUB (_r));                                             \
          STORE (pair_tbuf, _d, vl);                                          \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (pair_bits (pair_tbuf + _i) != 0ULL)                           \
              ++w->zero_bad;                                                  \
          _sy = LOAD (PAIR_SEED_AT ((_t + 1) % _ncall), vl);                  \
          _sx = LOAD (PAIR_SEED2_AT ((_t + 1) % _ncall), vl);                 \
          STORE (pair_tbuf, _sy, vl);                                         \
          STORE (pair_tbuf2, _sx, vl);                                        \
          _ny = ADD (_sy, _d);                                                \
          _nx = ADD (_sx, _d);                                                \
          STORE (pair_tbuf3, _ny, vl);                                        \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (pair_bits (pair_tbuf3 + _i) != pair_bits (pair_tbuf + _i))    \
              ++w->recover_bad;                                               \
          STORE (pair_tbuf3, _nx, vl);                                        \
          for (unsigned _i = 0; _i < _lanes; ++_i)                            \
            if (pair_bits (pair_tbuf3 + _i) != pair_bits (pair_tbuf2 + _i))   \
              ++w->recover_bad;                                               \
          _y = _ny;                                                           \
          _x = _nx;                                                           \
        }                                                                     \
      w->steps = _tsteps;                                                     \
      w->calls = _tsteps;                                                     \
    }                                                                         \
  while (0)

#define PAIR_TRACE_TPUT_UNARY(TAG, LANES, VT, LOAD, STORE, CALL)              \
  do                                                                          \
    {                                                                         \
      unsigned _lanes = (LANES);                                              \
      unsigned _ncall = pair_n / _lanes;                                      \
      VT _tx, _ty, _sink;                                                     \
      _sink = LOAD (pair_zero_v, vl);                                         \
      for (unsigned _p = 0; _p < 2; ++_p)                                     \
        for (unsigned _i = 0; _i < _ncall; ++_i)                              \
          {                                                                   \
            _tx = LOAD (PAIR_SEED_AT (_i), vl);                               \
            STORE (pair_tbuf, _tx, vl);                                       \
            w->arg_fnv = pair_fnv_buf (w->arg_fnv, pair_tbuf, _lanes);        \
            _ty = CALL (_tx);                                                 \
            _sink = __riscv_vfadd (_sink, _ty, vl);                           \
          }                                                                   \
      STORE (pair_tbuf, _sink, vl);                                           \
      w->steps = 2 * _ncall;                                                  \
      w->calls = 2 * _ncall;                                                  \
    }                                                                         \
  while (0)

#define PAIR_TRACE_TPUT_BINARY(TAG, LANES, VT, LOAD, STORE, CALL)             \
  do                                                                          \
    {                                                                         \
      unsigned _lanes = (LANES);                                              \
      unsigned _ncall = pair_n / _lanes;                                      \
      VT _ty, _tx, _r, _sink;                                                 \
      _sink = LOAD (pair_zero_v, vl);                                         \
      for (unsigned _p = 0; _p < 2; ++_p)                                     \
        for (unsigned _i = 0; _i < _ncall; ++_i)                              \
          {                                                                   \
            _ty = LOAD (PAIR_SEED_AT (_i), vl);                               \
            _tx = LOAD (PAIR_SEED2_AT (_i), vl);                              \
            STORE (pair_tbuf, _ty, vl);                                       \
            w->arg_fnv = pair_fnv_buf (w->arg_fnv, pair_tbuf, _lanes);        \
            STORE (pair_tbuf, _tx, vl);                                       \
            w->arg_fnv = pair_fnv_buf (w->arg_fnv, pair_tbuf, _lanes);        \
            _r = CALL (_ty, _tx);                                             \
            _sink = __riscv_vfadd (_sink, _r, vl);                            \
          }                                                                   \
      STORE (pair_tbuf, _sink, vl);                                           \
      w->steps = 2 * _ncall;                                                  \
      w->calls = 2 * _ncall;                                                  \
    }                                                                         \
  while (0)

#else /* !PAIRED_TRACE: the trace pass is compiled out of timed builds */

#define PAIR_TRACE_MASK(w, SM, mask)                                          \
  do                                                                          \
    {                                                                         \
    }                                                                         \
  while (0)
#define PAIR_TRACE_LAT_UNARY(...)                                             \
  do                                                                          \
    {                                                                         \
    }                                                                         \
  while (0)
#define PAIR_TRACE_LAT_BINARY(...)                                            \
  do                                                                          \
    {                                                                         \
    }                                                                         \
  while (0)
#define PAIR_TRACE_TPUT_UNARY(...)                                            \
  do                                                                          \
    {                                                                         \
    }                                                                         \
  while (0)
#define PAIR_TRACE_TPUT_BINARY(...)                                           \
  do                                                                          \
    {                                                                         \
    }                                                                         \
  while (0)

#endif /* PAIRED_TRACE */

/* ---- latency probes: next argument = bridge(r, frozen seed) ------------ */

#define PAIR_LAT_UNARY(TAG, CASE, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,    \
                       SCRUB)                                                 \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned steps = ncall * pair_batch * pair_k * repeat;              \
    VT r, x;                                                                  \
    unsigned ni = 1;                                                          \
    (void)round;                                                              \
    if (ni == ncall)                                                          \
      ni = 0;                                                                 \
    x = LOAD (PAIR_SEED_AT (0), vl);                                          \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = steps;                                                         \
    w->calls = steps;                                                         \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    for (unsigned i = 0; i < steps; ++i)                                      \
      {                                                                       \
        r = CALL (x);                                                         \
        x = ADD (LOAD (PAIR_SEED_AT (ni), vl), ZERO (SCRUB (r)));             \
        if (++ni == ncall)                                                    \
          ni = 0;                                                             \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    STORE (pair_tbuf, x, vl);                                                 \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT seed = LOAD (PAIR_SEED_AT (0), vl);                                  \
      STORE (pair_tbuf, CALL (seed), vl);                                     \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_LAT_UNARY (#TAG, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,      \
                          SCRUB);                                             \
  }

#define PAIR_LAT_UNARY_MASKED(TAG, CASE, LANES, VT, MASKVT, MSET, SM, LOAD,   \
                              STORE, CALL, ZERO, ADD, SCRUB)                  \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned steps = ncall * pair_batch * pair_k * repeat;              \
    MASKVT mask = MSET ((int32_t)(LANES));                                    \
    VT r, x;                                                                  \
    unsigned ni = 1;                                                          \
    (void)round;                                                              \
    if (ni == ncall)                                                          \
      ni = 0;                                                                 \
    x = LOAD (PAIR_SEED_AT (0), vl);                                          \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = steps;                                                         \
    w->calls = steps;                                                         \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    for (unsigned i = 0; i < steps; ++i)                                      \
      {                                                                       \
        r = CALL (x);                                                         \
        x = ADD (LOAD (PAIR_SEED_AT (ni), vl), ZERO (SCRUB (r)));             \
        if (++ni == ncall)                                                    \
          ni = 0;                                                             \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    STORE (pair_tbuf, x, vl);                                                 \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT seed = LOAD (PAIR_SEED_AT (0), vl);                                  \
      STORE (pair_tbuf, CALL (seed), vl);                                     \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_MASK (w, SM, mask);                                            \
    PAIR_TRACE_LAT_UNARY (#TAG, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,      \
                          SCRUB);                                             \
  }

#define PAIR_LAT_BINARY(TAG, CASE, LANES, VT, LOAD, STORE, CALL, ZERO, ADD)   \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned steps = ncall * pair_batch * pair_k * repeat;              \
    VT r, y, x, d;                                                            \
    unsigned ni = 1;                                                          \
    (void)round;                                                              \
    if (ni == ncall)                                                          \
      ni = 0;                                                                 \
    y = LOAD (PAIR_SEED_AT (0), vl);                                          \
    x = LOAD (PAIR_SEED2_AT (0), vl);                                         \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = steps;                                                         \
    w->calls = steps;                                                         \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    for (unsigned i = 0; i < steps; ++i)                                      \
      {                                                                       \
        r = CALL (y, x);                                                      \
        d = ZERO (r);                                                         \
        y = ADD (LOAD (PAIR_SEED_AT (ni), vl), d);                            \
        x = ADD (LOAD (PAIR_SEED2_AT (ni), vl), d);                           \
        if (++ni == ncall)                                                    \
          ni = 0;                                                             \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    STORE (pair_tbuf, y, vl);                                                 \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT sy = LOAD (PAIR_SEED_AT (0), vl);                                    \
      VT sx = LOAD (PAIR_SEED2_AT (0), vl);                                   \
      STORE (pair_tbuf, CALL (sy, sx), vl);                                   \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_LAT_BINARY (#TAG, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,     \
                           PAIR_SCRUB_ID);                                    \
  }

#define PAIR_LAT_BINARY_MASKED(TAG, CASE, LANES, VT, MASKVT, MSET, SM, LOAD,  \
                               STORE, CALL, ZERO, ADD, SCRUB)                 \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned steps = ncall * pair_batch * pair_k * repeat;              \
    MASKVT mask = MSET ((int32_t)(LANES));                                    \
    VT r, y, x, d;                                                            \
    unsigned ni = 1;                                                          \
    (void)round;                                                              \
    if (ni == ncall)                                                          \
      ni = 0;                                                                 \
    y = LOAD (PAIR_SEED_AT (0), vl);                                          \
    x = LOAD (PAIR_SEED2_AT (0), vl);                                         \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = steps;                                                         \
    w->calls = steps;                                                         \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    for (unsigned i = 0; i < steps; ++i)                                      \
      {                                                                       \
        r = CALL (y, x);                                                      \
        d = ZERO (SCRUB (r));                                                 \
        y = ADD (LOAD (PAIR_SEED_AT (ni), vl), d);                            \
        x = ADD (LOAD (PAIR_SEED2_AT (ni), vl), d);                           \
        if (++ni == ncall)                                                    \
          ni = 0;                                                             \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    STORE (pair_tbuf, y, vl);                                                 \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT sy = LOAD (PAIR_SEED_AT (0), vl);                                    \
      VT sx = LOAD (PAIR_SEED2_AT (0), vl);                                   \
      STORE (pair_tbuf, CALL (sy, sx), vl);                                   \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_MASK (w, SM, mask);                                            \
    PAIR_TRACE_LAT_BINARY (#TAG, LANES, VT, LOAD, STORE, CALL, ZERO, ADD,     \
                           SCRUB);                                            \
  }

/* ---- throughput probes: same frozen args, independent calls ------------ */

#define PAIR_TPUT_UNARY(TAG, CASE, LANES, VT, LOAD, STORE, CALL)              \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned passes = pair_batch * pair_k * repeat;                     \
    VT a0, a1, a2, a3, y;                                                     \
    (void)round;                                                              \
    a0 = a1 = a2 = a3 = LOAD (pair_zero_v, vl);                               \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = passes * ncall;                                                \
    w->calls = passes * ncall;                                                \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    if (pair_streams == 1)                                                    \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; ++i)                                \
            {                                                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i), vl));                         \
              a0 = __riscv_vfadd (a0, y, vl);                                 \
            }                                                                 \
      }                                                                       \
    else if (pair_streams == 2)                                               \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 2)                             \
            {                                                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i), vl));                         \
              a0 = __riscv_vfadd (a0, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 1), vl));                     \
              a1 = __riscv_vfadd (a1, y, vl);                                 \
            }                                                                 \
      }                                                                       \
    else                                                                      \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 4)                             \
            {                                                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i), vl));                         \
              a0 = __riscv_vfadd (a0, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 1), vl));                     \
              a1 = __riscv_vfadd (a1, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 2), vl));                     \
              a2 = __riscv_vfadd (a2, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 3), vl));                     \
              a3 = __riscv_vfadd (a3, y, vl);                                 \
            }                                                                 \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),                           \
                        __riscv_vfadd (a2, a3, vl), vl);                      \
    STORE (pair_tbuf, a0, vl);                                                \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT seed = LOAD (PAIR_SEED_AT (0), vl);                                  \
      STORE (pair_tbuf, CALL (seed), vl);                                     \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_TPUT_UNARY (#TAG, LANES, VT, LOAD, STORE, CALL);               \
  }

#define PAIR_TPUT_UNARY_MASKED(TAG, CASE, LANES, VT, MASKVT, MSET, SM, LOAD,  \
                               STORE, CALL)                                   \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned passes = pair_batch * pair_k * repeat;                     \
    MASKVT mask = MSET ((int32_t)(LANES));                                    \
    VT a0, a1, a2, a3, y;                                                     \
    (void)round;                                                              \
    a0 = a1 = a2 = a3 = LOAD (pair_zero_v, vl);                               \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = passes * ncall;                                                \
    w->calls = passes * ncall;                                                \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    if (pair_streams == 1)                                                    \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; ++i)                                \
            {                                                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i), vl));                         \
              a0 = __riscv_vfadd (a0, y, vl);                                 \
            }                                                                 \
      }                                                                       \
    else if (pair_streams == 2)                                               \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 2)                             \
            {                                                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i), vl));                         \
              a0 = __riscv_vfadd (a0, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 1), vl));                     \
              a1 = __riscv_vfadd (a1, y, vl);                                 \
            }                                                                 \
      }                                                                       \
    else                                                                      \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 4)                             \
            {                                                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i), vl));                         \
              a0 = __riscv_vfadd (a0, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 1), vl));                     \
              a1 = __riscv_vfadd (a1, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 2), vl));                     \
              a2 = __riscv_vfadd (a2, y, vl);                                 \
              y = CALL (LOAD (PAIR_SEED_AT (i + 3), vl));                     \
              a3 = __riscv_vfadd (a3, y, vl);                                 \
            }                                                                 \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),                           \
                        __riscv_vfadd (a2, a3, vl), vl);                      \
    STORE (pair_tbuf, a0, vl);                                                \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT seed = LOAD (PAIR_SEED_AT (0), vl);                                  \
      STORE (pair_tbuf, CALL (seed), vl);                                     \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_MASK (w, SM, mask);                                            \
    PAIR_TRACE_TPUT_UNARY (#TAG, LANES, VT, LOAD, STORE, CALL);               \
  }

#define PAIR_TPUT_BINARY(TAG, CASE, LANES, VT, LOAD, STORE, CALL)             \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned passes = pair_batch * pair_k * repeat;                     \
    VT a0, a1, a2, a3, y, x, r;                                               \
    (void)round;                                                              \
    a0 = a1 = a2 = a3 = LOAD (pair_zero_v, vl);                               \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = passes * ncall;                                                \
    w->calls = passes * ncall;                                                \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    if (pair_streams == 1)                                                    \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; ++i)                                \
            {                                                                 \
              y = LOAD (PAIR_SEED_AT (i), vl);                                \
              x = LOAD (PAIR_SEED2_AT (i), vl);                               \
              r = CALL (y, x);                                                \
              a0 = __riscv_vfadd (a0, r, vl);                                 \
            }                                                                 \
      }                                                                       \
    else if (pair_streams == 2)                                               \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 2)                             \
            {                                                                 \
              y = LOAD (PAIR_SEED_AT (i), vl);                                \
              x = LOAD (PAIR_SEED2_AT (i), vl);                               \
              r = CALL (y, x);                                                \
              a0 = __riscv_vfadd (a0, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 1), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 1), vl);                           \
              r = CALL (y, x);                                                \
              a1 = __riscv_vfadd (a1, r, vl);                                 \
            }                                                                 \
      }                                                                       \
    else                                                                      \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 4)                             \
            {                                                                 \
              y = LOAD (PAIR_SEED_AT (i), vl);                                \
              x = LOAD (PAIR_SEED2_AT (i), vl);                               \
              r = CALL (y, x);                                                \
              a0 = __riscv_vfadd (a0, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 1), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 1), vl);                           \
              r = CALL (y, x);                                                \
              a1 = __riscv_vfadd (a1, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 2), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 2), vl);                           \
              r = CALL (y, x);                                                \
              a2 = __riscv_vfadd (a2, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 3), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 3), vl);                           \
              r = CALL (y, x);                                                \
              a3 = __riscv_vfadd (a3, r, vl);                                 \
            }                                                                 \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),                           \
                        __riscv_vfadd (a2, a3, vl), vl);                      \
    STORE (pair_tbuf, a0, vl);                                                \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT sy = LOAD (PAIR_SEED_AT (0), vl);                                    \
      VT sx = LOAD (PAIR_SEED2_AT (0), vl);                                   \
      STORE (pair_tbuf, CALL (sy, sx), vl);                                   \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_TPUT_BINARY (#TAG, LANES, VT, LOAD, STORE, CALL);              \
  }

#define PAIR_TPUT_BINARY_MASKED(TAG, CASE, LANES, VT, MASKVT, MSET, SM, LOAD, \
                                STORE, CALL)                                  \
  static void PAIR_PROBE (TAG) (unsigned round, pair_window *w)               \
  {                                                                           \
    const size_t vl = (LANES);                                                \
    const unsigned lanes = (LANES);                                           \
    const unsigned ncall = pair_n / lanes;                                    \
    const unsigned repeat = pair_repeat ? pair_repeat : 1;                    \
    const unsigned passes = pair_batch * pair_k * repeat;                     \
    MASKVT mask = MSET ((int32_t)(LANES));                                    \
    VT a0, a1, a2, a3, y, x, r;                                               \
    (void)round;                                                              \
    a0 = a1 = a2 = a3 = LOAD (pair_zero_v, vl);                               \
    PAIR_TRACE_INIT (w);                                                      \
    w->steps = passes * ncall;                                                \
    w->calls = passes * ncall;                                                \
    w->repeat = repeat;                                                       \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    if (pair_streams == 1)                                                    \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; ++i)                                \
            {                                                                 \
              y = LOAD (PAIR_SEED_AT (i), vl);                                \
              x = LOAD (PAIR_SEED2_AT (i), vl);                               \
              r = CALL (y, x);                                                \
              a0 = __riscv_vfadd (a0, r, vl);                                 \
            }                                                                 \
      }                                                                       \
    else if (pair_streams == 2)                                               \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 2)                             \
            {                                                                 \
              y = LOAD (PAIR_SEED_AT (i), vl);                                \
              x = LOAD (PAIR_SEED2_AT (i), vl);                               \
              r = CALL (y, x);                                                \
              a0 = __riscv_vfadd (a0, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 1), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 1), vl);                           \
              r = CALL (y, x);                                                \
              a1 = __riscv_vfadd (a1, r, vl);                                 \
            }                                                                 \
      }                                                                       \
    else                                                                      \
      {                                                                       \
        for (unsigned p = 0; p < passes; ++p)                                 \
          for (unsigned i = 0; i < ncall; i += 4)                             \
            {                                                                 \
              y = LOAD (PAIR_SEED_AT (i), vl);                                \
              x = LOAD (PAIR_SEED2_AT (i), vl);                               \
              r = CALL (y, x);                                                \
              a0 = __riscv_vfadd (a0, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 1), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 1), vl);                           \
              r = CALL (y, x);                                                \
              a1 = __riscv_vfadd (a1, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 2), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 2), vl);                           \
              r = CALL (y, x);                                                \
              a2 = __riscv_vfadd (a2, r, vl);                                 \
              y = LOAD (PAIR_SEED_AT (i + 3), vl);                            \
              x = LOAD (PAIR_SEED2_AT (i + 3), vl);                           \
              r = CALL (y, x);                                                \
              a3 = __riscv_vfadd (a3, r, vl);                                 \
            }                                                                 \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),                           \
                        __riscv_vfadd (a2, a3, vl), vl);                      \
    STORE (pair_tbuf, a0, vl);                                                \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);             \
    {                                                                         \
      VT sy = LOAD (PAIR_SEED_AT (0), vl);                                    \
      VT sx = LOAD (PAIR_SEED2_AT (0), vl);                                   \
      STORE (pair_tbuf, CALL (sy, sx), vl);                                   \
    }                                                                         \
    pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, #TAG);            \
    PAIR_TRACE_MASK (w, SM, mask);                                            \
    PAIR_TRACE_TPUT_BINARY (#TAG, LANES, VT, LOAD, STORE, CALL);              \
  }

/* ---- case instantiation ------------------------------------------------ */
#define PAIR_CASE_STR(i) PAIRED_FN_STR i

#define PAIR_CASE_MM1 "|m1|"
#define PAIR_CASE_MM2 "|m2|"

/* Bit order is PAIRED_CASE_DIMS in build_latency.py. Disabled cases must
 * have neither probe bodies (callee relocations) nor table entries. */
#ifndef PAIRED_PROBE_MASK
#define PAIRED_PROBE_MASK 0xff
#endif
#define PAIR_ENABLED(i) ((PAIRED_PROBE_MASK) & (1 << (i)))

#if PAIRED_BINARY

#if PAIR_ENABLED (0)
PAIR_LAT_BINARY (m1_fixed_unmasked, atan2, 2, vfloat64m1_t,
                 __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                 PAIR_CALL_U_FIX_M1, lat_pair_zero_m1, lat_pair_add_m1)
PAIR_TPUT_BINARY (m1_fixed_unmasked_tput, atan2, 2, vfloat64m1_t,
                  __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                  PAIR_CALL_U_FIX_M1)
#endif
#if PAIR_ENABLED (1)
PAIR_LAT_BINARY (m1_scalable_unmasked, atan2, PAIRED_SCAL_M1_LANES,
                 vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                 PAIR_CALL_U_SC_M1, lat_pair_zero_m1, lat_pair_add_m1)
PAIR_TPUT_BINARY (m1_scalable_unmasked_tput, atan2, PAIRED_SCAL_M1_LANES,
                  vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                  PAIR_CALL_U_SC_M1)
#endif
#if PAIR_ENABLED (2)
PAIR_LAT_BINARY_MASKED (m1_fixed_masked, atan2, 2, vfloat64m1_t, vbool64_t,
                        __riscv_vmset_m_b64, __riscv_vsm_v_b64,
                        __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                        PAIR_CALL_UM_FIX_M1, lat_pair_zero_m1, lat_pair_add_m1,
                        PAIR_SCRUB_M1)
PAIR_TPUT_BINARY_MASKED (m1_fixed_masked_tput, atan2, 2, vfloat64m1_t,
                         vbool64_t, __riscv_vmset_m_b64, __riscv_vsm_v_b64,
                         __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                         PAIR_CALL_UM_FIX_M1)
#endif
#if PAIR_ENABLED (3)
PAIR_LAT_BINARY_MASKED (m1_scalable_masked, atan2, PAIRED_SCAL_M1_LANES,
                        vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
                        __riscv_vsm_v_b64, __riscv_vle64_v_f64m1,
                        __riscv_vse64_v_f64m1, PAIR_CALL_UM_SC_M1,
                        lat_pair_zero_m1, lat_pair_add_m1, PAIR_SCRUB_M1)
PAIR_TPUT_BINARY_MASKED (m1_scalable_masked_tput, atan2, PAIRED_SCAL_M1_LANES,
                         vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
                         __riscv_vsm_v_b64, __riscv_vle64_v_f64m1,
                         __riscv_vse64_v_f64m1, PAIR_CALL_UM_SC_M1)
#endif
#if PAIR_ENABLED (4)
PAIR_LAT_BINARY (m2_fixed_unmasked, atan2, 4, vfloat64m2_t,
                 __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                 PAIR_CALL_U_FIX_M2, lat_pair_zero_m2, lat_pair_add_m2)
PAIR_TPUT_BINARY (m2_fixed_unmasked_tput, atan2, 4, vfloat64m2_t,
                  __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                  PAIR_CALL_U_FIX_M2)
#endif
#if PAIR_ENABLED (5)
PAIR_LAT_BINARY (m2_scalable_unmasked, atan2, PAIRED_SCAL_M2_LANES,
                 vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                 PAIR_CALL_U_SC_M2, lat_pair_zero_m2, lat_pair_add_m2)
PAIR_TPUT_BINARY (m2_scalable_unmasked_tput, atan2, PAIRED_SCAL_M2_LANES,
                  vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                  PAIR_CALL_U_SC_M2)
#endif
#if PAIR_ENABLED (6)
PAIR_LAT_BINARY_MASKED (m2_fixed_masked, atan2, 4, vfloat64m2_t, vbool32_t,
                        __riscv_vmset_m_b32, __riscv_vsm_v_b32,
                        __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                        PAIR_CALL_UM_FIX_M2, lat_pair_zero_m2, lat_pair_add_m2,
                        PAIR_SCRUB_M2)
PAIR_TPUT_BINARY_MASKED (m2_fixed_masked_tput, atan2, 4, vfloat64m2_t,
                         vbool32_t, __riscv_vmset_m_b32, __riscv_vsm_v_b32,
                         __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                         PAIR_CALL_UM_FIX_M2)
#endif
#if PAIR_ENABLED (7)
PAIR_LAT_BINARY_MASKED (m2_scalable_masked, atan2, PAIRED_SCAL_M2_LANES,
                        vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
                        __riscv_vsm_v_b32, __riscv_vle64_v_f64m2,
                        __riscv_vse64_v_f64m2, PAIR_CALL_UM_SC_M2,
                        lat_pair_zero_m2, lat_pair_add_m2, PAIR_SCRUB_M2)
PAIR_TPUT_BINARY_MASKED (m2_scalable_masked_tput, atan2, PAIRED_SCAL_M2_LANES,
                         vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
                         __riscv_vsm_v_b32, __riscv_vle64_v_f64m2,
                         __riscv_vse64_v_f64m2, PAIR_CALL_UM_SC_M2)
#endif

#else /* unary */

#define PAIR_LAT_U(TAG, LAYOUT, MASKED, LANES, SYM, ZERO, ADD, SCRUB)         \
  PAIR_LAT_UNARY (TAG, atan, LANES, vfloat64m1_t, __riscv_vle64_v_f64m1,      \
                  __riscv_vse64_v_f64m1, SYM, ZERO, ADD, SCRUB)
#define PAIR_TPUT_U(TAG, LANES, SYM)                                          \
  PAIR_TPUT_UNARY (TAG, atan, LANES, vfloat64m1_t, __riscv_vle64_v_f64m1,     \
                   __riscv_vse64_v_f64m1, SYM)

#if PAIR_ENABLED (0)
PAIR_LAT_U (m1_fixed_unmasked, fixed, unmasked, 2, PAIR_CALL_U_FIX_M1,
            lat_pair_zero_m1, lat_pair_add_m1, PAIR_SCRUB_ID)
PAIR_TPUT_U (m1_fixed_unmasked_tput, 2, PAIR_CALL_U_FIX_M1)
#endif
#if PAIR_ENABLED (1)
PAIR_LAT_U (m1_scalable_unmasked, scalable, unmasked, PAIRED_SCAL_M1_LANES,
            PAIR_CALL_U_SC_M1, lat_pair_zero_m1, lat_pair_add_m1,
            PAIR_SCRUB_ID)
PAIR_TPUT_U (m1_scalable_unmasked_tput, PAIRED_SCAL_M1_LANES,
             PAIR_CALL_U_SC_M1)
#endif
#if PAIR_ENABLED (2)
PAIR_LAT_UNARY_MASKED (m1_fixed_masked, atan, 2, vfloat64m1_t, vbool64_t,
                       __riscv_vmset_m_b64, __riscv_vsm_v_b64,
                       __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                       PAIR_CALL_UM_FIX_M1, lat_pair_zero_m1, lat_pair_add_m1,
                       PAIR_SCRUB_M1)
PAIR_TPUT_UNARY_MASKED (m1_fixed_masked_tput, atan, 2, vfloat64m1_t, vbool64_t,
                        __riscv_vmset_m_b64, __riscv_vsm_v_b64,
                        __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
                        PAIR_CALL_UM_FIX_M1)
#endif
#if PAIR_ENABLED (3)
PAIR_LAT_UNARY_MASKED (m1_scalable_masked, atan, PAIRED_SCAL_M1_LANES,
                       vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
                       __riscv_vsm_v_b64, __riscv_vle64_v_f64m1,
                       __riscv_vse64_v_f64m1, PAIR_CALL_UM_SC_M1,
                       lat_pair_zero_m1, lat_pair_add_m1, PAIR_SCRUB_M1)
PAIR_TPUT_UNARY_MASKED (m1_scalable_masked_tput, atan, PAIRED_SCAL_M1_LANES,
                        vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
                        __riscv_vsm_v_b64, __riscv_vle64_v_f64m1,
                        __riscv_vse64_v_f64m1, PAIR_CALL_UM_SC_M1)
#endif
#if PAIR_ENABLED (4)
PAIR_LAT_UNARY (m2_fixed_unmasked, atan, 4, vfloat64m2_t,
                __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                PAIR_CALL_U_FIX_M2, lat_pair_zero_m2, lat_pair_add_m2,
                PAIR_SCRUB_ID)
PAIR_TPUT_UNARY (m2_fixed_unmasked_tput, atan, 4, vfloat64m2_t,
                 __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                 PAIR_CALL_U_FIX_M2)
#endif
#if PAIR_ENABLED (5)
PAIR_LAT_UNARY (m2_scalable_unmasked, atan, PAIRED_SCAL_M2_LANES, vfloat64m2_t,
                __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                PAIR_CALL_U_SC_M2, lat_pair_zero_m2, lat_pair_add_m2,
                PAIR_SCRUB_ID)
PAIR_TPUT_UNARY (m2_scalable_unmasked_tput, atan, PAIRED_SCAL_M2_LANES,
                 vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                 PAIR_CALL_U_SC_M2)
#endif
#if PAIR_ENABLED (6)
PAIR_LAT_UNARY_MASKED (m2_fixed_masked, atan, 4, vfloat64m2_t, vbool32_t,
                       __riscv_vmset_m_b32, __riscv_vsm_v_b32,
                       __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                       PAIR_CALL_UM_FIX_M2, lat_pair_zero_m2, lat_pair_add_m2,
                       PAIR_SCRUB_M2)
PAIR_TPUT_UNARY_MASKED (m2_fixed_masked_tput, atan, 4, vfloat64m2_t, vbool32_t,
                        __riscv_vmset_m_b32, __riscv_vsm_v_b32,
                        __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
                        PAIR_CALL_UM_FIX_M2)
#endif
#if PAIR_ENABLED (7)
PAIR_LAT_UNARY_MASKED (m2_scalable_masked, atan, PAIRED_SCAL_M2_LANES,
                       vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
                       __riscv_vsm_v_b32, __riscv_vle64_v_f64m2,
                       __riscv_vse64_v_f64m2, PAIR_CALL_UM_SC_M2,
                       lat_pair_zero_m2, lat_pair_add_m2, PAIR_SCRUB_M2)
PAIR_TPUT_UNARY_MASKED (m2_scalable_masked_tput, atan, PAIRED_SCAL_M2_LANES,
                        vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
                        __riscv_vsm_v_b32, __riscv_vle64_v_f64m2,
                        __riscv_vse64_v_f64m2, PAIR_CALL_UM_SC_M2)
#endif

/* ---- 编译器自动向量化探针 ------------------------------------------- */

/* 只有在自动向量化用例被选中时，构建器才用 -ffast-math -fopenmp-simd
   和 -mrvv-max-lmul=m1 编译本翻译单元，因此下面的普通循环会被编译器
   向量化，并生成对 _ZGV1Nxv_<函数> 的调用；探针里没有手写 intrinsic。
   always_inline 让调用点留在探针符号内，构建器才能审计最终机器码。 */
#if PAIR_ENABLED (8)
static double pair_auto_buf[PAIRED_MAX_N] __attribute__ ((aligned (64)));

static __attribute__ ((always_inline)) inline void
pair_auto_passes (double *x, unsigned n, unsigned passes)
{
  for (unsigned p = 0; p < passes; ++p)
    for (unsigned i = 0; i < n; ++i)
      x[i] = PAIRED_FN (x[i]);
}

static void
PAIR_PROBE (m1_auto) (unsigned round, pair_window *w)
{
  const unsigned lanes = PAIRED_SCAL_M1_LANES;
  const unsigned ncall = pair_n / lanes;
  const unsigned repeat = pair_repeat ? pair_repeat : 1;
  const unsigned passes = pair_batch * pair_k * repeat;
  (void) round;
  if (ncall == 0)
    {
      w->steps = w->calls = 0;
      w->repeat = repeat;
      w->begin = 0;
      w->cycles = 0;
      w->checksum = PAIR_FNV_INIT;
      return;
    }
  for (unsigned i = 0; i < ncall; ++i)
    __builtin_memcpy (&pair_auto_buf[i * lanes], PAIR_SEED_AT (i),
                      lanes * sizeof (double));
  PAIR_TRACE_INIT (w);
  w->steps = passes * ncall;
  w->calls = w->steps;
  w->repeat = repeat;
  w->begin = PAIR_WIN_BEGIN ();
  pair_auto_passes (pair_auto_buf, ncall * lanes, passes);
  w->cycles = PAIR_WIN_END () - w->begin;
  /* 用同一条自动向量化链重跑一趟，再按配对口径核对返回值。 */
  for (unsigned i = 0; i < ncall; ++i)
    __builtin_memcpy (&pair_auto_buf[i * lanes], PAIR_SEED_AT (i),
                      lanes * sizeof (double));
  pair_auto_passes (pair_auto_buf, ncall * lanes, 1);
  for (unsigned i = 0; i < lanes; ++i)
    pair_tbuf[i] = pair_auto_buf[i];
  w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_tbuf, lanes);
  pair_oracle_check (pair_tbuf, lanes, reference_bits, 0, "m1_auto");
}
#endif /* PAIR_ENABLED (8) */

#endif /* PAIRED_BINARY */

static const pair_probe PAIRED_TABLE[] = {
#if PAIR_ENABLED (0)
  { "m1_fixed_unmasked", PAIRED_FN_STR "|m1|fixed|unmasked", PAIRED_FN_STR,
    "m1", "fixed", "unmasked", 2, PAIRED_BINARY,
    PAIR_PROBE (m1_fixed_unmasked), PAIR_PROBE (m1_fixed_unmasked_tput) },
#endif
#if PAIR_ENABLED (1)
  { "m1_scalable_unmasked", PAIRED_FN_STR "|m1|scalable|unmasked",
    PAIRED_FN_STR, "m1", "scalable", "unmasked", PAIRED_SCAL_M1_LANES,
    PAIRED_BINARY, PAIR_PROBE (m1_scalable_unmasked),
    PAIR_PROBE (m1_scalable_unmasked_tput) },
#endif
#if PAIR_ENABLED (2)
  { "m1_fixed_masked", PAIRED_FN_STR "|m1|fixed|masked", PAIRED_FN_STR, "m1",
    "fixed", "masked", 2, PAIRED_BINARY, PAIR_PROBE (m1_fixed_masked),
    PAIR_PROBE (m1_fixed_masked_tput) },
#endif
#if PAIR_ENABLED (3)
  { "m1_scalable_masked", PAIRED_FN_STR "|m1|scalable|masked", PAIRED_FN_STR,
    "m1", "scalable", "masked", PAIRED_SCAL_M1_LANES, PAIRED_BINARY,
    PAIR_PROBE (m1_scalable_masked), PAIR_PROBE (m1_scalable_masked_tput) },
#endif
#if PAIR_ENABLED (4)
  { "m2_fixed_unmasked", PAIRED_FN_STR "|m2|fixed|unmasked", PAIRED_FN_STR,
    "m2", "fixed", "unmasked", 4, PAIRED_BINARY,
    PAIR_PROBE (m2_fixed_unmasked), PAIR_PROBE (m2_fixed_unmasked_tput) },
#endif
#if PAIR_ENABLED (5)
  { "m2_scalable_unmasked", PAIRED_FN_STR "|m2|scalable|unmasked",
    PAIRED_FN_STR, "m2", "scalable", "unmasked", PAIRED_SCAL_M2_LANES,
    PAIRED_BINARY, PAIR_PROBE (m2_scalable_unmasked),
    PAIR_PROBE (m2_scalable_unmasked_tput) },
#endif
#if PAIR_ENABLED (6)
  { "m2_fixed_masked", PAIRED_FN_STR "|m2|fixed|masked", PAIRED_FN_STR, "m2",
    "fixed", "masked", 4, PAIRED_BINARY, PAIR_PROBE (m2_fixed_masked),
    PAIR_PROBE (m2_fixed_masked_tput) },
#endif
#if PAIR_ENABLED (7)
  { "m2_scalable_masked", PAIRED_FN_STR "|m2|scalable|masked", PAIRED_FN_STR,
    "m2", "scalable", "masked", PAIRED_SCAL_M2_LANES, PAIRED_BINARY,
    PAIR_PROBE (m2_scalable_masked), PAIR_PROBE (m2_scalable_masked_tput) },
#endif
#if PAIR_ENABLED (8)
  { "m1_auto", PAIRED_FN_STR "|m1|auto|unmasked", PAIRED_FN_STR, "m1", "auto",
    "unmasked", PAIRED_SCAL_M1_LANES, PAIRED_BINARY, PAIR_PROBE (m1_auto),
    PAIR_PROBE (m1_auto) },
#endif
};

const pair_probe_group PAIRED_GROUP
    = { PAIRED_FN_STR, PAIRED_TABLE, PAIR_PROBE_COUNT (PAIRED_TABLE) };
