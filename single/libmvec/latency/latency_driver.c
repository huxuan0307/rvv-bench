/* Function-level latency probes for the public RVV libmvec entries.
 *
 * One probe = one *closed* dependency chain timed by a single rdcycle pair:
 *
 *     begin = rdcycle();  for (K times) x = map(entry(x));  cycles = rdcycle()-begin;
 *
 * K is a runtime loop bound, so a single binary produces the whole K series
 * (K = 0 measures window overhead only) and the host fits cycles(K) with a
 * line: slope = cycles per chained call, intercept = fixed window cost.
 *
 * Why a closing map: a call whose argument comes from memory would let
 * consecutive calls overlap, which is throughput, not latency.  map() folds the
 * result back into the entry's input domain ([-1, 1]) with two vector FP
 * instructions, so the next call cannot start before the previous one retires.
 * latency_map.h documents the maps and their fixed point.
 *
 * rdcycle boundaries: on XiangShan a `csrr cycle` is an ordinary out-of-order
 * read, so a short window can be timed by how far issue ran ahead instead of by
 * how long the chain took.  LAT_SERIAL=1 brackets each window with
 * `sfence.vma`, which forces a drain without touching memory.  Both variants
 * stay in the tree: the calibration build compares them (see README) and the
 * manifest records which one produced a data set.
 *
 * Correctness: every window is followed by an untimed call on the frozen input
 * words, checked against the frozen MPFR reference bits with ORACLE_LIMIT.  A
 * wrong ABI signature, a broken entry, or an optimised-away chain shows up as
 * LAT_ERROR / failed>0 rather than as a plausible cycle count.  The frozen
 * headers are per-function: atan and acos are separate translation units, each
 * compiled with -DLAT_DATA_HEADER.
 *
 * Groups are selected at build time (-DLAT_GROUP_*), so a calibration binary
 * never pulls libmvec members it does not measure.
 */
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "av_runtime.h"
#include "latency_probe.h"
#include "latency_map.h"

#ifdef LAT_DATA_HEADER
#include LAT_DATA_HEADER
static const uint64_t *const lat_input = input_bits;
#define LAT_HAVE_ORACLE 1
#define LAT_ORACLE_HEADER 1
#elif defined(LAT_SCALAR_ORACLE)
/* Scalar-libm oracle: the reference is the scalar routine of the very same
 * libm linked into this image, evaluated outside the timed window.  Inputs are
 * frozen here (2 lanes are read for m1, 4 for m2, and the four throughput
 * streams use offsets 0..3 so every lane stays inside the 8-entry corpus).
 * exp/log/pow need a domain near (0, 2]; sin/cos also cover reduction inputs. */
#define LAT_HAVE_ORACLE 1
#define LAT_ORACLE_SCALAR 1
#if defined(LAT_GROUP_EXP)
static const uint64_t lat_scalar_input[8] = {
  0x3fe0000000000000ULL,	/*  0.5  */
  0x3ff8000000000000ULL,	/*  1.5  */
  0xbfe8000000000000ULL,	/* -0.75 */
  0x4000000000000000ULL,	/*  2.0  */
  0xbff8000000000000ULL,	/* -1.5  */
  0x3fb999999999999aULL,	/*  0.1  */
  0x3ff4000000000000ULL,	/*  1.25 */
  0xc000000000000000ULL,	/* -2.0  */
};
#elif defined(LAT_GROUP_LOG)
static const uint64_t lat_scalar_input[8] = {
  0x3fb999999999999aULL,	/* 0.1  */
  0x3fe0000000000000ULL,	/* 0.5  */
  0x3ff0000000000000ULL,	/* 1.0  */
  0x4000000000000000ULL,	/* 2.0  */
  0x4014000000000000ULL,	/* 5.0  */
  0x3fd0000000000000ULL,	/* 0.25 */
  0x4020000000000000ULL,	/* 8.0  */
  0x3fe8000000000000ULL,	/* 0.75 */
};
#elif defined(LAT_GROUP_SIN) || defined(LAT_GROUP_COS)
static const uint64_t lat_scalar_input[8] = {
  0xc004000000000000ULL,	/* -2.5  */
  0xbfe0000000000000ULL,	/* -0.5  */
  0x3fd0000000000000ULL,	/*  0.25 */
  0x3ff8000000000000ULL,	/*  1.5  */
  0x4008000000000000ULL,	/*  3.0  */
  0xbff4000000000000ULL,	/* -1.25 */
  0x3fe8000000000000ULL,	/*  0.75 */
  0x4000000000000000ULL,	/*  2.0  */
};
#elif defined(LAT_GROUP_POW)
static const uint64_t lat_scalar_input[8] = {
  0x3fe0000000000000ULL,	/* 0.5  */
  0x3ff8000000000000ULL,	/* 1.5  */
  0x3fe999999999999aULL,	/* 0.8  */
  0x4000000000000000ULL,	/* 2.0  */
  0x3fd0000000000000ULL,	/* 0.25 */
  0x3ff0000000000000ULL,	/* 1.0  */
  0x3fe3333333333333ULL,	/* 0.6  */
  0x3ffccccccccccccdULL,	/* 1.8  */
};
static const uint64_t lat_scalar_input2[8] = {
  0xbff0000000000000ULL,	/* -1.0  */
  0x4000000000000000ULL,	/*  2.0  */
  0x3fe0000000000000ULL,	/*  0.5  */
  0xc000000000000000ULL,	/* -2.0  */
  0x4008000000000000ULL,	/*  3.0  */
  0x3fd0000000000000ULL,	/*  0.25 */
  0xbfe0000000000000ULL,	/* -0.5  */
  0x3ff0000000000000ULL,	/*  1.0  */
};
#endif
static const uint64_t *const lat_input = lat_scalar_input;
#if defined(LAT_GROUP_POW)
static const uint64_t *const lat_input2 = lat_scalar_input2;
#endif
#else
/* Calibration probes have no oracle.  Values sit inside [-1, 1] so the maps
 * stay in domain; both chains converge to the same fixed point regardless. */
static const uint64_t lat_input_local[8] = {
  0x3fd0000000000000ULL,	/*  0.25  */
  0xbfe0000000000000ULL,	/* -0.5   */
  0x3fe8000000000000ULL,	/*  0.75  */
  0xbfc0000000000000ULL,	/* -0.125 */
  0x3fe0000000000000ULL,	/*  0.5   */
  0xbfd0000000000000ULL,	/* -0.25  */
  0x3ff0000000000000ULL,	/*  1.0   */
  0xbfe8000000000000ULL,	/* -0.75  */
};
static const uint64_t *const lat_input = lat_input_local;
#define LAT_HAVE_ORACLE 0
#define LAT_ORACLE_NONE 1
#endif
#include <math.h>

#ifndef LAT_SCALAR_FN
#define LAT_SCALAR_FN exp
#endif
#ifndef LAT_SCALAR_FN2
#define LAT_SCALAR_FN2 pow
#endif

#ifndef LAT_SERIAL
#define LAT_SERIAL 0
#endif

#define LAT_RVV_CC __attribute__((riscv_vector_cc))

/* ---- entries under test, named by the RVV vector ABI ---- */
extern vfloat64m1_t _ZGV1Nxv_atan (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_atan (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_acos (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_acos (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1M2v_acos (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2M4v_acos (vbool32_t, vfloat64m2_t) LAT_RVV_CC;

/* exp family: 8 ABI variants (fixed/VLMAX x unmasked/masked, m1/m2). */
extern vfloat64m1_t _ZGV1N2v_exp (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_exp (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1M2v_exp (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Mxv_exp (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2N4v_exp (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_exp (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2M4v_exp (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Mxv_exp (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
/* log family: 8 ABI variants. */
extern vfloat64m1_t _ZGV1N2v_log (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_log (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1M2v_log (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Mxv_log (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2N4v_log (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_log (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2M4v_log (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Mxv_log (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
/* sin family: 8 ABI variants. */
extern vfloat64m1_t _ZGV1N2v_sin (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_sin (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1M2v_sin (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Mxv_sin (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2N4v_sin (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_sin (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2M4v_sin (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Mxv_sin (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
/* cos family: 8 ABI variants. */
extern vfloat64m1_t _ZGV1N2v_cos (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Nxv_cos (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1M2v_cos (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Mxv_cos (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2N4v_cos (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxv_cos (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2M4v_cos (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Mxv_cos (vbool32_t, vfloat64m2_t) LAT_RVV_CC;
/* pow family: binary, 8 ABI variants (two vector arguments). */
extern vfloat64m1_t _ZGV1N2vv_pow (vfloat64m1_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Nxvv_pow (vfloat64m1_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1M2vv_pow (vbool64_t, vfloat64m1_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m1_t _ZGV1Mxvv_pow (vbool64_t, vfloat64m1_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2N4vv_pow (vfloat64m2_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Nxvv_pow (vfloat64m2_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2M4vv_pow (vbool32_t, vfloat64m2_t, vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m2_t _ZGV2Mxvv_pow (vbool32_t, vfloat64m2_t, vfloat64m2_t) LAT_RVV_CC;

/* ---- empty ABI controls (latency_noop.c) ---- */
extern vfloat64m1_t latency_noop_m1 (vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t latency_noop_m2 (vfloat64m2_t) LAT_RVV_CC;
extern vfloat64m1_t latency_noop_mask_m2 (vbool64_t, vfloat64m1_t) LAT_RVV_CC;
extern vfloat64m2_t latency_noop_mask_m4 (vbool32_t, vfloat64m2_t) LAT_RVV_CC;

/* Oracle counters live in latency_main.c; main resets them before each entry. */
extern unsigned lat_checks;
extern unsigned lat_failed;

static inline uint64_t
lat_cycle (void)
{
  uint64_t c;
  __asm__ volatile ("rdcycle %0" : "=r"(c) :: "memory");
  return c;
}

/* Read the cycle CSR after draining the pipeline.  sfence.vma is a serialising
 * instruction for the hart; it costs a fixed amount, which the regression
 * intercept absorbs, and stops the closing read from issuing before the chain
 * has retired. */
static inline uint64_t
lat_cycle_serial (void)
{
  __asm__ volatile ("sfence.vma zero, zero" ::: "memory");
  return lat_cycle ();
}

#if LAT_SERIAL
#define LAT_WIN_BEGIN() lat_cycle_serial ()
#define LAT_WIN_END() lat_cycle_serial ()
#else
#define LAT_WIN_BEGIN() lat_cycle ()
#define LAT_WIN_END() lat_cycle ()
#endif

static inline uint64_t
lat_bits (const double *p)
{
  uint64_t b;
  __builtin_memcpy (&b, p, sizeof b);
  return b;
}

static inline double
lat_double (uint64_t b)
{
  double d;
  __builtin_memcpy (&d, &b, sizeof d);
  return d;
}

/* Zero row for the throughput accumulators (loaded with the group's own LMUL
 * load, so the macro does not need the vector type name). */
static const double lat_zero[8] = { 0.0, 0.0, 0.0, 0.0,
				    0.0, 0.0, 0.0, 0.0 };

/* Scalar counterparts of the vector maps, used by the scalar libm control
 * chain.  -ffp-contract=off keeps them instruction-for-instruction equal to
 * the vector maps (no fused multiply-add).
 *
 * The clamp must stay branchless.  The former `v < lo ? lo : (v > hi ? hi : v)`
 * compiled to flt.d + bnez: on a saturated orbit the compare skips the
 * arithmetic and moves a constant into fa0, so the next call no longer waits
 * for the returned value and the window measures call throughput instead of
 * latency.  FMIN.D/FMAX.D are unconditional FP instructions, so every
 * iteration keeps the previous return value on the FP data path.  The
 * operands are finite on every orbit we seed, so the NaN-selection rule of
 * these instructions never applies. */
static inline double
lat_fmin_d (double a, double b)
{
  double r;
  __asm__ ("fmin.d %0, %1, %2" : "=f" (r) : "f" (a), "f" (b));
  return r;
}

static inline double
lat_fmax_d (double a, double b)
{
  double r;
  __asm__ ("fmax.d %0, %1, %2" : "=f" (r) : "f" (a), "f" (b));
  return r;
}

static inline double
lat_scalar_clamp (double v, double lo, double hi)
{
  return lat_fmin_d (lat_fmax_d (v, lo), hi);
}

/* Total order on IEEE-754 bit patterns so a one-bit difference stays one. */
static inline uint64_t
lat_ordered (uint64_t b)
{
  const uint64_t sign = 1ULL << 63;
  return (b & sign) ? ~b : (b | sign);
}

static inline uint64_t
lat_fnv (uint64_t h, uint64_t v)
{
  return (h ^ v) * 1099511628211ULL;
}

static uint64_t
lat_fnvbuf (const double *p, unsigned n)
{
  uint64_t h = 1469598103934665603ULL;
  for (unsigned i = 0; i < n; ++i)
    h = lat_fnv (h, lat_bits (p + i));
  return h;
}

#if LAT_HAVE_ORACLE
#if LAT_ORACLE_HEADER
static void
lat_check_bits (const double *out, unsigned n, const uint64_t *want,
		const char *entry)
{
  for (unsigned i = 0; i < n; ++i)
    {
      ++lat_checks;
      uint64_t got = lat_bits (out + i);
      uint64_t a = lat_ordered (got), b = lat_ordered (want[i]);
      uint64_t d = a > b ? a - b : b - a;
      if (d > ORACLE_LIMIT)
	{
	  if (lat_failed++ < 8)
	    printf ("LAT_ERROR,entry=%s,index=%u,got=%llx,want=%llx\n", entry, i,
		    (unsigned long long) got, (unsigned long long) want[i]);
	}
    }
}
#define LAT_CHECK(out, n, entry) lat_check_bits ((out), (n), reference_bits, (entry))
#else /* LAT_ORACLE_SCALAR */
/* Compare each vector lane with the scalar libm routine of the same library on
 * the same frozen input.  The distance is in ordered IEEE-754 steps, i.e. ULP,
 * and >LAT_SCALAR_ULP is a hard failure: a bad ABI, a broken mask, or a wrong
 * chain shows up as failed>0, never as a plausible cycle count. */
#ifndef LAT_SCALAR_ULP
#define LAT_SCALAR_ULP 2
#endif
static void
lat_check_scalar (const double *out, unsigned n, const char *entry)
{
  for (unsigned i = 0; i < n; ++i)
    {
      ++lat_checks;
      uint64_t got = lat_bits (out + i);
      double want_d;
#if defined(LAT_GROUP_POW)
      want_d = LAT_SCALAR_FN2 (lat_double (lat_input[i]),
			       lat_double (lat_input2[i]));
#else
      want_d = LAT_SCALAR_FN (lat_double (lat_input[i]));
#endif
      uint64_t want = lat_bits (&want_d);
      uint64_t a = lat_ordered (got), b = lat_ordered (want);
      uint64_t d = a > b ? a - b : b - a;
      if (d > LAT_SCALAR_ULP)
	{
	  if (lat_failed++ < 8)
	    printf ("LAT_ERROR,entry=%s,index=%u,got=%llx,want=%llx,ulp=%llu\n",
		    entry, i, (unsigned long long) got,
		    (unsigned long long) want, (unsigned long long) d);
	}
    }
}
#define LAT_CHECK(out, n, entry) lat_check_scalar ((out), (n), (entry))
#endif
#else
#define LAT_CHECK(out, n, entry) ((void) 0)
#endif

/* Chained map probe.  Round 0 seeds from the frozen input and runs the untimed
 * prelude; later rounds continue from the stored state, so every timed window
 * starts at the closed-loop operating point instead of the raw corpus value. */
#define LAT_DEFINE_MAP_PROBE(TAG, NAME, LANES, VT, LOAD, STORE, MAPFN, CALL) \
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  static double state[8] __attribute__((aligned(64)));			\
  static double out[8] __attribute__((aligned(64)));			\
  VT x;									\
  if (round == 0)							\
    {									\
      x = LOAD ((const double *)(const void *) lat_input, vl);		\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	x = MAPFN (CALL (x), vl);					\
    }									\
  else									\
    x = LOAD ((const double *) state, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    x = MAPFN (CALL (x), vl);						\
  w->cycles = LAT_WIN_END () - w->begin;				\
  STORE (state, x, vl);							\
  w->chain_checksum = lat_fnvbuf (state, (LANES));			\
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    STORE (out, CALL (rx), vl);						\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* Pure empty-call probe: slope is the ABI call overhead with no kernel and no
 * map, i.e. the floor that every real entry also pays. */
#define LAT_DEFINE_NOOP_PROBE(TAG, NAME, LANES, VT, LOAD, STORE, CALL)	\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  static double out[8] __attribute__((aligned(64)));			\
  VT x = LOAD ((const double *)(const void *) lat_input, vl);		\
  (void) round;								\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    x = CALL (x);							\
  w->cycles = LAT_WIN_END () - w->begin;				\
  STORE (out, x, vl);							\
  w->chain_checksum = lat_fnvbuf (out, (LANES));			\
  w->checksum = w->chain_checksum;					\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* Masked fixed-size probe: the entry takes an explicit mask and no VL argument,
 * so CALL is a function-like macro closing over the all-active mask. */
#define LAT_DEFINE_MASKED_MAP_PROBE(TAG, NAME, LANES, VT, MASKVT, MSET, LOAD,	\
				    STORE, MAPFN, CALL)			\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  MASKVT lat_mask = MSET ((int32_t) (LANES));				\
  static double state[8] __attribute__((aligned(64)));			\
  static double out[8] __attribute__((aligned(64)));			\
  VT x;									\
  if (round == 0)							\
    {									\
      x = LOAD ((const double *)(const void *) lat_input, vl);		\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	x = MAPFN (CALL (x), vl);					\
    }									\
  else									\
    x = LOAD ((const double *) state, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    x = MAPFN (CALL (x), vl);						\
  w->cycles = LAT_WIN_END () - w->begin;				\
  STORE (state, x, vl);							\
  w->chain_checksum = lat_fnvbuf (state, (LANES));			\
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    STORE (out, CALL (rx), vl);						\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* ---- independent-call throughput probes ---------------------------------
 *
 * Latency mode chains map(entry(x)) so the next call waits for the previous
 * result.  Throughput mode does the opposite: four loop-invariant input
 * vectors sit in four register groups and each iteration issues the four calls
 * back to back.  There is no data dependency between successive calls (the
 * arguments never change); the results only feed four independent accumulator
 * chains, which keeps the calls alive without feeding them back.  Calls per
 * window = 4*K and the host divides the window by that count. */
#define LAT_TPUT_STREAMS 4

#define LAT_DEFINE_TPUT_PROBE(TAG, NAME, LANES, VT, LOAD, STORE, CALL)	\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  static double out[8] __attribute__((aligned(64)));			\
  VT x0, x1, x2, x3, a0, a1, a2, a3, y;					\
  (void) round;								\
  x0 = LOAD ((const double *)(const void *) lat_input + 0, vl);		\
  x1 = LOAD ((const double *)(const void *) lat_input + 1, vl);		\
  x2 = LOAD ((const double *)(const void *) lat_input + 2, vl);		\
  x3 = LOAD ((const double *)(const void *) lat_input + 3, vl);		\
  a0 = a1 = a2 = a3 = LOAD (lat_zero, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      y = CALL (x0); a0 = __riscv_vfadd (a0, y, vl);			\
      y = CALL (x1); a1 = __riscv_vfadd (a1, y, vl);			\
      y = CALL (x2); a2 = __riscv_vfadd (a2, y, vl);			\
      y = CALL (x3); a3 = __riscv_vfadd (a3, y, vl);			\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  w->calls = LAT_TPUT_STREAMS * k;					\
  a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),			\
		      __riscv_vfadd (a2, a3, vl), vl);			\
  STORE (out, a0, vl);							\
  w->chain_checksum = lat_fnvbuf (out, (LANES));			\
  /* Functional check: one real entry call on the frozen input.  The \
   * accumulator above is a sum of results, not a function value. */	\
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    STORE (out, CALL (rx), vl);						\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

#define LAT_DEFINE_MASKED_TPUT_PROBE(TAG, NAME, LANES, VT, MASKVT, MSET,	\
				     LOAD, STORE, CALL)			\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  MASKVT lat_mask = MSET ((int32_t) (LANES));				\
  static double out[8] __attribute__((aligned(64)));			\
  VT x0, x1, x2, x3, a0, a1, a2, a3, y;					\
  (void) round;								\
  x0 = LOAD ((const double *)(const void *) lat_input + 0, vl);		\
  x1 = LOAD ((const double *)(const void *) lat_input + 1, vl);		\
  x2 = LOAD ((const double *)(const void *) lat_input + 2, vl);		\
  x3 = LOAD ((const double *)(const void *) lat_input + 3, vl);		\
  a0 = a1 = a2 = a3 = LOAD (lat_zero, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      y = CALL (x0); a0 = __riscv_vfadd (a0, y, vl);			\
      y = CALL (x1); a1 = __riscv_vfadd (a1, y, vl);			\
      y = CALL (x2); a2 = __riscv_vfadd (a2, y, vl);			\
      y = CALL (x3); a3 = __riscv_vfadd (a3, y, vl);			\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  w->calls = LAT_TPUT_STREAMS * k;					\
  a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),			\
		      __riscv_vfadd (a2, a3, vl), vl);			\
  STORE (out, a0, vl);							\
  w->chain_checksum = lat_fnvbuf (out, (LANES));			\
  /* Functional check: one real entry call on the frozen input.  The \
   * accumulator above is a sum of results, not a function value. */	\
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    STORE (out, CALL (rx), vl);						\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* Binary (pow) chained map probe: both argument vectors are part of the
 * closed loop.  The result feeds x, and z is re-derived from the new x. */
#define LAT_DEFINE_BINARY_MAP_PROBE(TAG, NAME, LANES, VT, LOAD, STORE, MAPX,	\
				    MAPZ, CALL)				\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  static double state[8] __attribute__((aligned(64)));			\
  static double state2[8] __attribute__((aligned(64)));			\
  static double out[8] __attribute__((aligned(64)));			\
  VT x, z, r;								\
  if (round == 0)							\
    {									\
      x = LOAD ((const double *)(const void *) lat_input, vl);		\
      z = LOAD ((const double *)(const void *) lat_input2, vl);		\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	{								\
	  r = CALL (x, z);						\
	  x = MAPX (r, vl);						\
	  z = MAPZ (x, vl);						\
	}								\
    }									\
  else									\
    {									\
      x = LOAD ((const double *) state, vl);				\
      z = LOAD ((const double *) state2, vl);				\
    }									\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      r = CALL (x, z);							\
      x = MAPX (r, vl);							\
      z = MAPZ (x, vl);							\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  STORE (state, x, vl);							\
  STORE (state2, z, vl);						\
  w->chain_checksum = lat_fnvbuf (state, (LANES))			\
    ^ (lat_fnvbuf (state2, (LANES)) * 1099511628211ULL);		\
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    VT rz = LOAD ((const double *)(const void *) lat_input2, vl);	\
    STORE (out, CALL (rx, rz), vl);					\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

#define LAT_DEFINE_BINARY_MASKED_MAP_PROBE(TAG, NAME, LANES, VT, MASKVT, MSET,	\
					   LOAD, STORE, MAPX, MAPZ, CALL)	\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  MASKVT lat_mask = MSET ((int32_t) (LANES));				\
  static double state[8] __attribute__((aligned(64)));			\
  static double state2[8] __attribute__((aligned(64)));			\
  static double out[8] __attribute__((aligned(64)));			\
  VT x, z, r;								\
  if (round == 0)							\
    {									\
      x = LOAD ((const double *)(const void *) lat_input, vl);		\
      z = LOAD ((const double *)(const void *) lat_input2, vl);		\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	{								\
	  r = CALL (x, z);						\
	  x = MAPX (r, vl);						\
	  z = MAPZ (x, vl);						\
	}								\
    }									\
  else									\
    {									\
      x = LOAD ((const double *) state, vl);				\
      z = LOAD ((const double *) state2, vl);				\
    }									\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      r = CALL (x, z);							\
      x = MAPX (r, vl);							\
      z = MAPZ (x, vl);							\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  STORE (state, x, vl);							\
  STORE (state2, z, vl);						\
  w->chain_checksum = lat_fnvbuf (state, (LANES))			\
    ^ (lat_fnvbuf (state2, (LANES)) * 1099511628211ULL);		\
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    VT rz = LOAD ((const double *)(const void *) lat_input2, vl);	\
    STORE (out, CALL (rx, rz), vl);					\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* Binary throughput probe: four independent (x,z) pairs, loop-invariant. */
#define LAT_DEFINE_BINARY_TPUT_PROBE(TAG, NAME, LANES, VT, LOAD, STORE, CALL) \
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  static double out[8] __attribute__((aligned(64)));			\
  VT x0, x1, x2, x3, z0, z1, z2, z3, a0, a1, a2, a3, y;			\
  (void) round;								\
  x0 = LOAD ((const double *)(const void *) lat_input + 0, vl);		\
  x1 = LOAD ((const double *)(const void *) lat_input + 1, vl);		\
  x2 = LOAD ((const double *)(const void *) lat_input + 2, vl);		\
  x3 = LOAD ((const double *)(const void *) lat_input + 3, vl);		\
  z0 = LOAD ((const double *)(const void *) lat_input2 + 0, vl);	\
  z1 = LOAD ((const double *)(const void *) lat_input2 + 1, vl);	\
  z2 = LOAD ((const double *)(const void *) lat_input2 + 2, vl);	\
  z3 = LOAD ((const double *)(const void *) lat_input2 + 3, vl);	\
  a0 = a1 = a2 = a3 = LOAD (lat_zero, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      y = CALL (x0, z0); a0 = __riscv_vfadd (a0, y, vl);		\
      y = CALL (x1, z1); a1 = __riscv_vfadd (a1, y, vl);		\
      y = CALL (x2, z2); a2 = __riscv_vfadd (a2, y, vl);		\
      y = CALL (x3, z3); a3 = __riscv_vfadd (a3, y, vl);		\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  w->calls = LAT_TPUT_STREAMS * k;					\
  a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),			\
		      __riscv_vfadd (a2, a3, vl), vl);			\
  STORE (out, a0, vl);							\
  w->chain_checksum = lat_fnvbuf (out, (LANES));			\
  /* Functional check: one real two-argument call on the frozen inputs. */ \
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    VT rz = LOAD ((const double *)(const void *) lat_input2, vl);	\
    STORE (out, CALL (rx, rz), vl);					\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

#define LAT_DEFINE_BINARY_MASKED_TPUT_PROBE(TAG, NAME, LANES, VT, MASKVT,	\
					    MSET, LOAD, STORE, CALL)	\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  MASKVT lat_mask = MSET ((int32_t) (LANES));				\
  static double out[8] __attribute__((aligned(64)));			\
  VT x0, x1, x2, x3, z0, z1, z2, z3, a0, a1, a2, a3, y;			\
  (void) round;								\
  x0 = LOAD ((const double *)(const void *) lat_input + 0, vl);		\
  x1 = LOAD ((const double *)(const void *) lat_input + 1, vl);		\
  x2 = LOAD ((const double *)(const void *) lat_input + 2, vl);		\
  x3 = LOAD ((const double *)(const void *) lat_input + 3, vl);		\
  z0 = LOAD ((const double *)(const void *) lat_input2 + 0, vl);	\
  z1 = LOAD ((const double *)(const void *) lat_input2 + 1, vl);	\
  z2 = LOAD ((const double *)(const void *) lat_input2 + 2, vl);	\
  z3 = LOAD ((const double *)(const void *) lat_input2 + 3, vl);	\
  a0 = a1 = a2 = a3 = LOAD (lat_zero, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      y = CALL (x0, z0); a0 = __riscv_vfadd (a0, y, vl);		\
      y = CALL (x1, z1); a1 = __riscv_vfadd (a1, y, vl);		\
      y = CALL (x2, z2); a2 = __riscv_vfadd (a2, y, vl);		\
      y = CALL (x3, z3); a3 = __riscv_vfadd (a3, y, vl);		\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  w->calls = LAT_TPUT_STREAMS * k;					\
  a0 = __riscv_vfadd (__riscv_vfadd (a0, a1, vl),			\
		      __riscv_vfadd (a2, a3, vl), vl);			\
  STORE (out, a0, vl);							\
  w->chain_checksum = lat_fnvbuf (out, (LANES));			\
  /* Functional check: one real two-argument call on the frozen inputs. */ \
  {									\
    VT rx = LOAD ((const double *)(const void *) lat_input, vl);	\
    VT rz = LOAD ((const double *)(const void *) lat_input2, vl);	\
    STORE (out, CALL (rx, rz), vl);					\
  }									\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* Scalar libm control chain: the mapped scalar routine sits in the same K
 * loop as the vector variants, so cycles/call is directly comparable.  The
 * map is branchless by construction (FMIN/FMAX inline asm for every clamp), so
 * the next iteration's x is always produced from this iteration's return
 * value on the FP data path; build_latency.py audits the loop for both a real
 * call and a tainted fa0/fa1 feed. */
#define LAT_DEFINE_SCALAR_MAP_PROBE(TAG, NAME, MAPF, CALLF)		\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  static double state;							\
  double x;								\
  if (round == 0)							\
    {									\
      x = lat_double (lat_input[0]);					\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	x = MAPF (CALLF (x));						\
    }									\
  else									\
    x = state;								\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    x = MAPF (CALLF (x));						\
  w->cycles = LAT_WIN_END () - w->begin;				\
  state = x;								\
  w->chain_checksum = lat_bits (&state);				\
  {									\
    double out = CALLF (lat_double (lat_input[0]));			\
    w->checksum = lat_bits (&out);					\
    LAT_CHECK (&out, 1, NAME);						\
  }									\
}

#define LAT_DEFINE_SCALAR_BINARY_MAP_PROBE(TAG, NAME, MAPF, MAPF2, CALLF) \
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  static double state, state2;						\
  double x, z, y;							\
  if (round == 0)							\
    {									\
      x = lat_double (lat_input[0]);					\
      z = lat_double (lat_input2[0]);					\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	{								\
	  y = CALLF (x, z);						\
	  x = MAPF (y);							\
	  z = MAPF2 (x);						\
	}								\
    }									\
  else									\
    {									\
      x = state;							\
      z = state2;							\
    }									\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    {									\
      y = CALLF (x, z);							\
      x = MAPF (y);							\
      z = MAPF2 (x);							\
    }									\
  w->cycles = LAT_WIN_END () - w->begin;				\
  state = x;								\
  state2 = z;								\
  w->chain_checksum = lat_bits (&state) ^ (lat_bits (&state2) * 1099511628211ULL); \
  {									\
    double out = CALLF (lat_double (lat_input[0]),			\
			lat_double (lat_input2[0]));			\
    w->checksum = lat_bits (&out);					\
    LAT_CHECK (&out, 1, NAME);						\
  }									\
}

/* Map-only probe: two vector FP instructions per step, no call at all. */
#define LAT_DEFINE_MAP_ONLY_PROBE(TAG, NAME, LANES, VT, LOAD, STORE, MAPFN) \
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  const size_t vl = (LANES);						\
  static double state[8] __attribute__((aligned(64)));			\
  static double out[8] __attribute__((aligned(64)));			\
  VT x;									\
  if (round == 0)							\
    {									\
      x = LOAD ((const double *)(const void *) lat_input, vl);		\
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)			\
	x = MAPFN (x, vl);						\
    }									\
  else									\
    x = LOAD ((const double *) state, vl);				\
  w->begin = LAT_WIN_BEGIN ();						\
  for (unsigned i = 0; i < k; ++i)					\
    x = MAPFN (x, vl);							\
  w->cycles = LAT_WIN_END () - w->begin;				\
  STORE (state, x, vl);							\
  w->chain_checksum = lat_fnvbuf (state, (LANES));			\
  STORE (out, x, vl);							\
  w->checksum = lat_fnvbuf (out, (LANES));				\
  LAT_CHECK (out, (LANES), NAME);					\
}

/* ---- scalar control chain: K dependent addi blocks, no loop overhead ---- */
#define LAT_A1(a) __asm__ volatile ("addi %0, %0, 1" : "+r"(a));
#define LAT_A2(a) LAT_A1 (a) LAT_A1 (a)
#define LAT_A4(a) LAT_A2 (a) LAT_A2 (a)
#define LAT_A8(a) LAT_A4 (a) LAT_A4 (a)
#define LAT_A16(a) LAT_A8 (a) LAT_A8 (a)
#define LAT_A32(a) LAT_A16 (a) LAT_A16 (a)
#define LAT_A64(a) LAT_A32 (a) LAT_A32 (a)
#define LAT_A128(a) LAT_A64 (a) LAT_A64 (a)
#define LAT_A256(a) LAT_A128 (a) LAT_A128 (a)
#define LAT_A512(a) LAT_A256 (a) LAT_A256 (a)
#define LAT_A1024(a) LAT_A512 (a) LAT_A512 (a)
#define LAT_A2048(a) LAT_A1024 (a) LAT_A1024 (a)

#define LAT_NOP_CASE(STEPS, BODY)					\
  case STEPS:								\
    w->begin = LAT_WIN_BEGIN ();					\
    BODY;								\
    w->cycles = LAT_WIN_END () - w->begin;				\
    break;

#define LAT_DEFINE_NOP_PROBE(TAG, NAME, B4, B8, B16, B32)		\
static void								\
probe_##TAG (unsigned k, unsigned round, lat_window *w)			\
{									\
  uint64_t a = 1;							\
  (void) round;								\
  switch (k)								\
    {									\
    case 0:								\
      w->begin = LAT_WIN_BEGIN ();					\
      w->cycles = LAT_WIN_END () - w->begin;				\
      break;								\
    LAT_NOP_CASE (4, B4)						\
    LAT_NOP_CASE (8, B8)						\
    LAT_NOP_CASE (16, B16)						\
    LAT_NOP_CASE (32, B32)						\
    default:								\
      printf ("LAT_ERROR,entry=" NAME ",kind=unsupported_k,k=%u\n", k);	\
      w->begin = 0;							\
      w->cycles = 0;							\
      break;								\
    }									\
  w->checksum = a;							\
  w->chain_checksum = a;						\
}

/* 1 addi per step: the exact "K dependent instructions" shape. */
LAT_DEFINE_NOP_PROBE (nop_addi, "nop_addi", LAT_A4 (a), LAT_A8 (a),
		      LAT_A16 (a), LAT_A32 (a))
/* 64 addi per step: a long window whose latency is far above the issue skew, so
 * the same fit must report 64 cycles per step when the measurement is sound. */
LAT_DEFINE_NOP_PROBE (nop_addi64, "nop_addi64", LAT_A256 (a), LAT_A512 (a),
		      LAT_A1024 (a), LAT_A2048 (a))

#ifdef LAT_GROUP_CALIB
LAT_DEFINE_NOOP_PROBE (noop_m1, "noop_m1", LAT_LANES_M1, vfloat64m1_t,
		       __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       latency_noop_m1)
LAT_DEFINE_NOOP_PROBE (noop_m2, "noop_m2", LAT_LANES_M2, vfloat64m2_t,
		       __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       latency_noop_m2)
LAT_DEFINE_MAP_PROBE (noop_map_m1, "noop_map_m1", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_acos_m1, latency_noop_m1)
LAT_DEFINE_MAP_PROBE (noop_map_m2, "noop_map_m2", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_acos_m2, latency_noop_m2)
LAT_DEFINE_MAP_ONLY_PROBE (map_m1, "map_m1", LAT_LANES_M1, vfloat64m1_t,
			   __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			   lat_map_acos_m1)
LAT_DEFINE_MAP_ONLY_PROBE (map_m2, "map_m2", LAT_LANES_M2, vfloat64m2_t,
			   __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			   lat_map_acos_m2)

/* Masked empty-call + map, mirroring _ZGV1M2v_acos (2 lanes, vbool64_t). */
static void
probe_noop_mask_map_m2 (unsigned k, unsigned round, lat_window *w)
{
  const size_t vl = LAT_LANES_M1;
  vbool64_t mask = __riscv_vmset_m_b64 ((int32_t) LAT_LANES_M1);
  static double state[8] __attribute__((aligned(64)));
  static double out[8] __attribute__((aligned(64)));
  vfloat64m1_t x;
  if (round == 0)
    {
      x = __riscv_vle64_v_f64m1 ((const double *)(const void *) lat_input, vl);
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)
	x = lat_map_acos_m1 (latency_noop_mask_m2 (mask, x), vl);
    }
  else
    x = __riscv_vle64_v_f64m1 ((const double *) state, vl);
  w->begin = LAT_WIN_BEGIN ();
  for (unsigned i = 0; i < k; ++i)
    x = lat_map_acos_m1 (latency_noop_mask_m2 (mask, x), vl);
  w->cycles = LAT_WIN_END () - w->begin;
  __riscv_vse64_v_f64m1 (state, x, vl);
  w->chain_checksum = lat_fnvbuf (state, LAT_LANES_M1);
  __riscv_vse64_v_f64m1 (out, x, vl);
  w->checksum = lat_fnvbuf (out, LAT_LANES_M1);
  LAT_CHECK (out, LAT_LANES_M1, "noop_mask_map_m2");
}

/* Masked empty-call + map, mirroring _ZGV2M4v_acos (4 lanes, vbool32_t). */
static void
probe_noop_mask_map_m4 (unsigned k, unsigned round, lat_window *w)
{
  const size_t vl = LAT_LANES_M2;
  vbool32_t mask = __riscv_vmset_m_b32 ((int32_t) LAT_LANES_M2);
  static double state[8] __attribute__((aligned(64)));
  static double out[8] __attribute__((aligned(64)));
  vfloat64m2_t x;
  if (round == 0)
    {
      x = __riscv_vle64_v_f64m2 ((const double *)(const void *) lat_input, vl);
      for (unsigned i = 0; i < LAT_PRELUDE; ++i)
	x = lat_map_acos_m2 (latency_noop_mask_m4 (mask, x), vl);
    }
  else
    x = __riscv_vle64_v_f64m2 ((const double *) state, vl);
  w->begin = LAT_WIN_BEGIN ();
  for (unsigned i = 0; i < k; ++i)
    x = lat_map_acos_m2 (latency_noop_mask_m4 (mask, x), vl);
  w->cycles = LAT_WIN_END () - w->begin;
  __riscv_vse64_v_f64m2 (state, x, vl);
  w->chain_checksum = lat_fnvbuf (state, LAT_LANES_M2);
  __riscv_vse64_v_f64m2 (out, x, vl);
  w->checksum = lat_fnvbuf (out, LAT_LANES_M2);
  LAT_CHECK (out, LAT_LANES_M2, "noop_mask_map_m4");
}

static const lat_probe calib_probes[] = {
  {"nop_addi", probe_nop_addi},
  {"nop_addi64", probe_nop_addi64},
  {"noop_m1", probe_noop_m1},
  {"noop_m2", probe_noop_m2},
  {"noop_map_m1", probe_noop_map_m1},
  {"noop_map_m2", probe_noop_map_m2},
  {"noop_mask_map_m2", probe_noop_mask_map_m2},
  {"noop_mask_map_m4", probe_noop_mask_map_m4},
  {"map_m1", probe_map_m1},
  {"map_m2", probe_map_m2},
};
const lat_probe_group lat_group_calib
  = {"calib", calib_probes, LAT_PROBE_COUNT (calib_probes)};
#endif /* LAT_GROUP_CALIB */

#ifdef LAT_GROUP_ATAN
#if !LAT_HAVE_ORACLE
#error "LAT_GROUP_ATAN needs -DLAT_DATA_HEADER=\"atan.h\""
#endif
LAT_DEFINE_MAP_PROBE (atan_m1_Nx, "atan_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_atan_m1, _ZGV1Nxv_atan)
LAT_DEFINE_MAP_PROBE (atan_m2_Nx, "atan_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_atan_m2, _ZGV2Nxv_atan)

static const lat_probe atan_probes[] = {
  {"atan_m1_Nx", probe_atan_m1_Nx},
  {"atan_m2_Nx", probe_atan_m2_Nx},
};
const lat_probe_group lat_group_atan
  = {"atan", atan_probes, LAT_PROBE_COUNT (atan_probes)};
#endif /* LAT_GROUP_ATAN */

#ifdef LAT_GROUP_ACOS_NX
#if !LAT_HAVE_ORACLE
#error "LAT_GROUP_ACOS_NX needs -DLAT_DATA_HEADER=\"acos.h\""
#endif
LAT_DEFINE_MAP_PROBE (acos_m1_Nx, "acos_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_acos_m1, _ZGV1Nxv_acos)
LAT_DEFINE_MAP_PROBE (acos_m2_Nx, "acos_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_acos_m2, _ZGV2Nxv_acos)

static const lat_probe acos_nx_probes[] = {
  {"acos_m1_Nx", probe_acos_m1_Nx},
  {"acos_m2_Nx", probe_acos_m2_Nx},
};
const lat_probe_group lat_group_acos_nx
  = {"acos_nx", acos_nx_probes, LAT_PROBE_COUNT (acos_nx_probes)};
#endif /* LAT_GROUP_ACOS_NX */

#ifdef LAT_GROUP_ACOS_MASKED
#if !LAT_HAVE_ORACLE
#error "LAT_GROUP_ACOS_MASKED needs -DLAT_DATA_HEADER=\"acos.h\""
#endif
/* Fixed-size masked acos M2: 2 lanes, vbool64_t all-active mask, no VL
 * argument.  The closing map uses the same lane count. */
#define LAT_CALL_ACOS_M2(v) _ZGV1M2v_acos (lat_mask, (v))
#define LAT_CALL_ACOS_M4(v) _ZGV2M4v_acos (lat_mask, (v))
LAT_DEFINE_MASKED_MAP_PROBE (acos_m1_M2, "acos_m1_M2", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_acos_m1, LAT_CALL_ACOS_M2)
LAT_DEFINE_MASKED_MAP_PROBE (acos_m2_M4, "acos_m2_M4", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_acos_m2, LAT_CALL_ACOS_M4)

static const lat_probe acos_masked_probes[] = {
  {"acos_m1_M2", probe_acos_m1_M2},
  {"acos_m2_M4", probe_acos_m2_M4},
};
const lat_probe_group lat_group_acos_masked
  = {"acos_masked", acos_masked_probes, LAT_PROBE_COUNT (acos_masked_probes)};
#endif /* LAT_GROUP_ACOS_MASKED */

/* ------------------------------------------------------------------------
 * exp: 8 ABI variants + 8 independent-call throughput probes + scalar libm
 * control.  One binary per function (build_latency.py --groups exp).
 * ------------------------------------------------------------------------ */
#ifdef LAT_GROUP_EXP
#if !LAT_HAVE_ORACLE || !LAT_ORACLE_SCALAR
#error "LAT_GROUP_EXP needs -DLAT_SCALAR_ORACLE=1"
#endif
#define LAT_CALL_EXP_M1_M2(v) _ZGV1M2v_exp (lat_mask, (v))
#define LAT_CALL_EXP_M1_MX(v) _ZGV1Mxv_exp (lat_mask, (v))
#define LAT_CALL_EXP_M2_M4(v) _ZGV2M4v_exp (lat_mask, (v))
#define LAT_CALL_EXP_M2_MX(v) _ZGV2Mxv_exp (lat_mask, (v))

LAT_DEFINE_MAP_PROBE (exp_m1_N2, "exp_m1_N2", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_exp_m1, _ZGV1N2v_exp)
LAT_DEFINE_MAP_PROBE (exp_m1_Nx, "exp_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_exp_m1, _ZGV1Nxv_exp)
LAT_DEFINE_MASKED_MAP_PROBE (exp_m1_M2, "exp_m1_M2", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_exp_m1, LAT_CALL_EXP_M1_M2)
LAT_DEFINE_MASKED_MAP_PROBE (exp_m1_Mx, "exp_m1_Mx", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_exp_m1, LAT_CALL_EXP_M1_MX)
LAT_DEFINE_MAP_PROBE (exp_m2_N4, "exp_m2_N4", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_exp_m2, _ZGV2N4v_exp)
LAT_DEFINE_MAP_PROBE (exp_m2_Nx, "exp_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_exp_m2, _ZGV2Nxv_exp)
LAT_DEFINE_MASKED_MAP_PROBE (exp_m2_M4, "exp_m2_M4", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_exp_m2, LAT_CALL_EXP_M2_M4)
LAT_DEFINE_MASKED_MAP_PROBE (exp_m2_Mx, "exp_m2_Mx", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_exp_m2, LAT_CALL_EXP_M2_MX)

LAT_DEFINE_TPUT_PROBE (exp_m1_N2_tput, "exp_m1_N2_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1,
		       __riscv_vse64_v_f64m1, _ZGV1N2v_exp)
LAT_DEFINE_TPUT_PROBE (exp_m1_Nx_tput, "exp_m1_Nx_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1,
		       __riscv_vse64_v_f64m1, _ZGV1Nxv_exp)
LAT_DEFINE_MASKED_TPUT_PROBE (exp_m1_M2_tput, "exp_m1_M2_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			      LAT_CALL_EXP_M1_M2)
LAT_DEFINE_MASKED_TPUT_PROBE (exp_m1_Mx_tput, "exp_m1_Mx_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			      LAT_CALL_EXP_M1_MX)
LAT_DEFINE_TPUT_PROBE (exp_m2_N4_tput, "exp_m2_N4_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2,
		       __riscv_vse64_v_f64m2, _ZGV2N4v_exp)
LAT_DEFINE_TPUT_PROBE (exp_m2_Nx_tput, "exp_m2_Nx_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2,
		       __riscv_vse64_v_f64m2, _ZGV2Nxv_exp)
LAT_DEFINE_MASKED_TPUT_PROBE (exp_m2_M4_tput, "exp_m2_M4_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			      LAT_CALL_EXP_M2_M4)
LAT_DEFINE_MASKED_TPUT_PROBE (exp_m2_Mx_tput, "exp_m2_Mx_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			      LAT_CALL_EXP_M2_MX)

/* exp, non-saturated operating point.  gain = 0.5/exp(0.5), so the fixed
 * point is x=0.5 with y=exp(0.5)=1.6487 strictly inside the clamp, and the
 * local slope of the map is gain*exp(0.5) = 0.5 (contracting).  The clamp
 * still has to be branchless: it is a guard for the first step, not a
 * replacement for the multiply. */
#define LAT_EXP_GAIN 0.3032653298563167
static double
lat_scalar_map_exp (double y)
{
  return LAT_EXP_GAIN * lat_scalar_clamp (y, 1.0, 2.5);
}

/* exp, saturated operating point (control).  Same orbit as the old probe
 * (x -> 1, exp(1)=2.718 clamped to 2.0) but evaluated with FMIN/FMAX, so the
 * subtract consumes the clamped return value instead of a branch-selected
 * constant.  Comparing this probe with exp_scalar separates "the clamp output
 * is locally constant" from "the chain is bypassed". */
static double
lat_scalar_map_exp_sat (double y)
{
  return lat_scalar_clamp (y, 0.1, 2.0) - 1.0;
}
LAT_DEFINE_SCALAR_MAP_PROBE (exp_scalar, "exp_scalar", lat_scalar_map_exp, exp)
LAT_DEFINE_SCALAR_MAP_PROBE (exp_scalar_sat, "exp_scalar_sat",
			     lat_scalar_map_exp_sat, exp)

static const lat_probe exp_probes[] = {
  {"exp_m1_N2", probe_exp_m1_N2},
  {"exp_m1_Nx", probe_exp_m1_Nx},
  {"exp_m1_M2", probe_exp_m1_M2},
  {"exp_m1_Mx", probe_exp_m1_Mx},
  {"exp_m2_N4", probe_exp_m2_N4},
  {"exp_m2_Nx", probe_exp_m2_Nx},
  {"exp_m2_M4", probe_exp_m2_M4},
  {"exp_m2_Mx", probe_exp_m2_Mx},
  {"exp_m1_N2_tput", probe_exp_m1_N2_tput},
  {"exp_m1_Nx_tput", probe_exp_m1_Nx_tput},
  {"exp_m1_M2_tput", probe_exp_m1_M2_tput},
  {"exp_m1_Mx_tput", probe_exp_m1_Mx_tput},
  {"exp_m2_N4_tput", probe_exp_m2_N4_tput},
  {"exp_m2_Nx_tput", probe_exp_m2_Nx_tput},
  {"exp_m2_M4_tput", probe_exp_m2_M4_tput},
  {"exp_m2_Mx_tput", probe_exp_m2_Mx_tput},
  {"exp_scalar", probe_exp_scalar},
  {"exp_scalar_sat", probe_exp_scalar_sat},
};
const lat_probe_group lat_group_exp
  = {"exp", exp_probes, LAT_PROBE_COUNT (exp_probes)};
#endif /* LAT_GROUP_EXP */

/* ------------------------------------------------------------------------
 * log: 8 ABI variants + 8 independent-call throughput probes + scalar libm
 * control.  One binary per function (build_latency.py --groups log).
 * ------------------------------------------------------------------------ */
#ifdef LAT_GROUP_LOG
#if !LAT_HAVE_ORACLE || !LAT_ORACLE_SCALAR
#error "LAT_GROUP_LOG needs -DLAT_SCALAR_ORACLE=1"
#endif
#define LAT_CALL_LOG_M1_M2(v) _ZGV1M2v_log (lat_mask, (v))
#define LAT_CALL_LOG_M1_Mx(v) _ZGV1Mxv_log (lat_mask, (v))
#define LAT_CALL_LOG_M2_M4(v) _ZGV2M4v_log (lat_mask, (v))
#define LAT_CALL_LOG_M2_Mx(v) _ZGV2Mxv_log (lat_mask, (v))

LAT_DEFINE_MAP_PROBE (log_m1_N2, "log_m1_N2", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_log_m1, _ZGV1N2v_log)
LAT_DEFINE_MAP_PROBE (log_m1_Nx, "log_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_log_m1, _ZGV1Nxv_log)
LAT_DEFINE_MASKED_MAP_PROBE (log_m1_M2, "log_m1_M2", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_log_m1, LAT_CALL_LOG_M1_M2)
LAT_DEFINE_MASKED_MAP_PROBE (log_m1_Mx, "log_m1_Mx", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_log_m1, LAT_CALL_LOG_M1_Mx)
LAT_DEFINE_MAP_PROBE (log_m2_N4, "log_m2_N4", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_log_m2, _ZGV2N4v_log)
LAT_DEFINE_MAP_PROBE (log_m2_Nx, "log_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_log_m2, _ZGV2Nxv_log)
LAT_DEFINE_MASKED_MAP_PROBE (log_m2_M4, "log_m2_M4", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_log_m2, LAT_CALL_LOG_M2_M4)
LAT_DEFINE_MASKED_MAP_PROBE (log_m2_Mx, "log_m2_Mx", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_log_m2, LAT_CALL_LOG_M2_Mx)

LAT_DEFINE_TPUT_PROBE (log_m1_N2_tput, "log_m1_N2_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       _ZGV1N2v_log)
LAT_DEFINE_TPUT_PROBE (log_m1_Nx_tput, "log_m1_Nx_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       _ZGV1Nxv_log)
LAT_DEFINE_MASKED_TPUT_PROBE (log_m1_M2_tput, "log_m1_M2_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_LOG_M1_M2)
LAT_DEFINE_MASKED_TPUT_PROBE (log_m1_Mx_tput, "log_m1_Mx_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_LOG_M1_Mx)
LAT_DEFINE_TPUT_PROBE (log_m2_N4_tput, "log_m2_N4_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       _ZGV2N4v_log)
LAT_DEFINE_TPUT_PROBE (log_m2_Nx_tput, "log_m2_Nx_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       _ZGV2Nxv_log)
LAT_DEFINE_MASKED_TPUT_PROBE (log_m2_M4_tput, "log_m2_M4_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_LOG_M2_M4)
LAT_DEFINE_MASKED_TPUT_PROBE (log_m2_Mx_tput, "log_m2_Mx_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_LOG_M2_Mx)

/* log: x' = 0.8 + 0.4*clamp(y,-1,1) in [0.4,1.2].  The fixed point is
 * x=0.58671 with log(x)=-0.533 (clamp inactive, |dx'/dx|=0.682), so the orbit
 * settles on one reproducible input instead of the old x=0.1 where log(0.1)
 * < 0.1 made the clamp return the constant 0.1 forever.  The clamp keeps the
 * next input strictly positive even if a different seed makes log() return
 * -inf, and it is branchless (FMIN/FMAX). */
static double
lat_scalar_map_log (double y)
{
  return 0.8 + 0.4 * lat_scalar_clamp (y, -1.0, 1.0);
}
LAT_DEFINE_SCALAR_MAP_PROBE (log_scalar, "log_scalar",
			   lat_scalar_map_log, log)

static const lat_probe log_probes[] = {
  {"log_m1_N2", probe_log_m1_N2},
  {"log_m1_N2_tput", probe_log_m1_N2_tput},
  {"log_m1_Nx", probe_log_m1_Nx},
  {"log_m1_Nx_tput", probe_log_m1_Nx_tput},
  {"log_m1_M2", probe_log_m1_M2},
  {"log_m1_M2_tput", probe_log_m1_M2_tput},
  {"log_m1_Mx", probe_log_m1_Mx},
  {"log_m1_Mx_tput", probe_log_m1_Mx_tput},
  {"log_m2_N4", probe_log_m2_N4},
  {"log_m2_N4_tput", probe_log_m2_N4_tput},
  {"log_m2_Nx", probe_log_m2_Nx},
  {"log_m2_Nx_tput", probe_log_m2_Nx_tput},
  {"log_m2_M4", probe_log_m2_M4},
  {"log_m2_M4_tput", probe_log_m2_M4_tput},
  {"log_m2_Mx", probe_log_m2_Mx},
  {"log_m2_Mx_tput", probe_log_m2_Mx_tput},
  {"log_scalar", probe_log_scalar},
};
const lat_probe_group lat_group_log
  = {"log", log_probes, LAT_PROBE_COUNT (log_probes)};
#endif /* LAT_GROUP_LOG */

/* ------------------------------------------------------------------------
 * sin: 8 ABI variants + 8 independent-call throughput probes + scalar libm
 * control.  One binary per function (build_latency.py --groups sin).
 * ------------------------------------------------------------------------ */
#ifdef LAT_GROUP_SIN
#if !LAT_HAVE_ORACLE || !LAT_ORACLE_SCALAR
#error "LAT_GROUP_SIN needs -DLAT_SCALAR_ORACLE=1"
#endif
#define LAT_CALL_SIN_M1_M2(v) _ZGV1M2v_sin (lat_mask, (v))
#define LAT_CALL_SIN_M1_Mx(v) _ZGV1Mxv_sin (lat_mask, (v))
#define LAT_CALL_SIN_M2_M4(v) _ZGV2M4v_sin (lat_mask, (v))
#define LAT_CALL_SIN_M2_Mx(v) _ZGV2Mxv_sin (lat_mask, (v))

LAT_DEFINE_MAP_PROBE (sin_m1_N2, "sin_m1_N2", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_identity_m1, _ZGV1N2v_sin)
LAT_DEFINE_MAP_PROBE (sin_m1_Nx, "sin_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_identity_m1, _ZGV1Nxv_sin)
LAT_DEFINE_MASKED_MAP_PROBE (sin_m1_M2, "sin_m1_M2", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_identity_m1, LAT_CALL_SIN_M1_M2)
LAT_DEFINE_MASKED_MAP_PROBE (sin_m1_Mx, "sin_m1_Mx", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_identity_m1, LAT_CALL_SIN_M1_Mx)
LAT_DEFINE_MAP_PROBE (sin_m2_N4, "sin_m2_N4", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_identity_m2, _ZGV2N4v_sin)
LAT_DEFINE_MAP_PROBE (sin_m2_Nx, "sin_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_identity_m2, _ZGV2Nxv_sin)
LAT_DEFINE_MASKED_MAP_PROBE (sin_m2_M4, "sin_m2_M4", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_identity_m2, LAT_CALL_SIN_M2_M4)
LAT_DEFINE_MASKED_MAP_PROBE (sin_m2_Mx, "sin_m2_Mx", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_identity_m2, LAT_CALL_SIN_M2_Mx)

LAT_DEFINE_TPUT_PROBE (sin_m1_N2_tput, "sin_m1_N2_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       _ZGV1N2v_sin)
LAT_DEFINE_TPUT_PROBE (sin_m1_Nx_tput, "sin_m1_Nx_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       _ZGV1Nxv_sin)
LAT_DEFINE_MASKED_TPUT_PROBE (sin_m1_M2_tput, "sin_m1_M2_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_SIN_M1_M2)
LAT_DEFINE_MASKED_TPUT_PROBE (sin_m1_Mx_tput, "sin_m1_Mx_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_SIN_M1_Mx)
LAT_DEFINE_TPUT_PROBE (sin_m2_N4_tput, "sin_m2_N4_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       _ZGV2N4v_sin)
LAT_DEFINE_TPUT_PROBE (sin_m2_Nx_tput, "sin_m2_Nx_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       _ZGV2Nxv_sin)
LAT_DEFINE_MASKED_TPUT_PROBE (sin_m2_M4_tput, "sin_m2_M4_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_SIN_M2_M4)
LAT_DEFINE_MASKED_TPUT_PROBE (sin_m2_Mx_tput, "sin_m2_Mx_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_SIN_M2_Mx)

static double
lat_scalar_map_sin (double y)
{
  return y;
}
LAT_DEFINE_SCALAR_MAP_PROBE (sin_scalar, "sin_scalar",
			   lat_scalar_map_sin, sin)

static const lat_probe sin_probes[] = {
  {"sin_m1_N2", probe_sin_m1_N2},
  {"sin_m1_N2_tput", probe_sin_m1_N2_tput},
  {"sin_m1_Nx", probe_sin_m1_Nx},
  {"sin_m1_Nx_tput", probe_sin_m1_Nx_tput},
  {"sin_m1_M2", probe_sin_m1_M2},
  {"sin_m1_M2_tput", probe_sin_m1_M2_tput},
  {"sin_m1_Mx", probe_sin_m1_Mx},
  {"sin_m1_Mx_tput", probe_sin_m1_Mx_tput},
  {"sin_m2_N4", probe_sin_m2_N4},
  {"sin_m2_N4_tput", probe_sin_m2_N4_tput},
  {"sin_m2_Nx", probe_sin_m2_Nx},
  {"sin_m2_Nx_tput", probe_sin_m2_Nx_tput},
  {"sin_m2_M4", probe_sin_m2_M4},
  {"sin_m2_M4_tput", probe_sin_m2_M4_tput},
  {"sin_m2_Mx", probe_sin_m2_Mx},
  {"sin_m2_Mx_tput", probe_sin_m2_Mx_tput},
  {"sin_scalar", probe_sin_scalar},
};
const lat_probe_group lat_group_sin
  = {"sin", sin_probes, LAT_PROBE_COUNT (sin_probes)};
#endif /* LAT_GROUP_SIN */

/* ------------------------------------------------------------------------
 * cos: 8 ABI variants + 8 independent-call throughput probes + scalar libm
 * control.  One binary per function (build_latency.py --groups cos).
 * ------------------------------------------------------------------------ */
#ifdef LAT_GROUP_COS
#if !LAT_HAVE_ORACLE || !LAT_ORACLE_SCALAR
#error "LAT_GROUP_COS needs -DLAT_SCALAR_ORACLE=1"
#endif
#define LAT_CALL_COS_M1_M2(v) _ZGV1M2v_cos (lat_mask, (v))
#define LAT_CALL_COS_M1_Mx(v) _ZGV1Mxv_cos (lat_mask, (v))
#define LAT_CALL_COS_M2_M4(v) _ZGV2M4v_cos (lat_mask, (v))
#define LAT_CALL_COS_M2_Mx(v) _ZGV2Mxv_cos (lat_mask, (v))

LAT_DEFINE_MAP_PROBE (cos_m1_N2, "cos_m1_N2", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_identity_m1, _ZGV1N2v_cos)
LAT_DEFINE_MAP_PROBE (cos_m1_Nx, "cos_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
		      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		      lat_map_identity_m1, _ZGV1Nxv_cos)
LAT_DEFINE_MASKED_MAP_PROBE (cos_m1_M2, "cos_m1_M2", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_identity_m1, LAT_CALL_COS_M1_M2)
LAT_DEFINE_MASKED_MAP_PROBE (cos_m1_Mx, "cos_m1_Mx", LAT_LANES_M1,
			     vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			     __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			     lat_map_identity_m1, LAT_CALL_COS_M1_Mx)
LAT_DEFINE_MAP_PROBE (cos_m2_N4, "cos_m2_N4", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_identity_m2, _ZGV2N4v_cos)
LAT_DEFINE_MAP_PROBE (cos_m2_Nx, "cos_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
		      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		      lat_map_identity_m2, _ZGV2Nxv_cos)
LAT_DEFINE_MASKED_MAP_PROBE (cos_m2_M4, "cos_m2_M4", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_identity_m2, LAT_CALL_COS_M2_M4)
LAT_DEFINE_MASKED_MAP_PROBE (cos_m2_Mx, "cos_m2_Mx", LAT_LANES_M2,
			     vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			     __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			     lat_map_identity_m2, LAT_CALL_COS_M2_Mx)

LAT_DEFINE_TPUT_PROBE (cos_m1_N2_tput, "cos_m1_N2_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       _ZGV1N2v_cos)
LAT_DEFINE_TPUT_PROBE (cos_m1_Nx_tput, "cos_m1_Nx_tput", LAT_LANES_M1,
		       vfloat64m1_t, __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
		       _ZGV1Nxv_cos)
LAT_DEFINE_MASKED_TPUT_PROBE (cos_m1_M2_tput, "cos_m1_M2_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_COS_M1_M2)
LAT_DEFINE_MASKED_TPUT_PROBE (cos_m1_Mx_tput, "cos_m1_Mx_tput", LAT_LANES_M1,
			      vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
			      __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_COS_M1_Mx)
LAT_DEFINE_TPUT_PROBE (cos_m2_N4_tput, "cos_m2_N4_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       _ZGV2N4v_cos)
LAT_DEFINE_TPUT_PROBE (cos_m2_Nx_tput, "cos_m2_Nx_tput", LAT_LANES_M2,
		       vfloat64m2_t, __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
		       _ZGV2Nxv_cos)
LAT_DEFINE_MASKED_TPUT_PROBE (cos_m2_M4_tput, "cos_m2_M4_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_COS_M2_M4)
LAT_DEFINE_MASKED_TPUT_PROBE (cos_m2_Mx_tput, "cos_m2_Mx_tput", LAT_LANES_M2,
			      vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
			      __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_COS_M2_Mx)

static double
lat_scalar_map_cos (double y)
{
  return y;
}
LAT_DEFINE_SCALAR_MAP_PROBE (cos_scalar, "cos_scalar",
			   lat_scalar_map_cos, cos)

static const lat_probe cos_probes[] = {
  {"cos_m1_N2", probe_cos_m1_N2},
  {"cos_m1_N2_tput", probe_cos_m1_N2_tput},
  {"cos_m1_Nx", probe_cos_m1_Nx},
  {"cos_m1_Nx_tput", probe_cos_m1_Nx_tput},
  {"cos_m1_M2", probe_cos_m1_M2},
  {"cos_m1_M2_tput", probe_cos_m1_M2_tput},
  {"cos_m1_Mx", probe_cos_m1_Mx},
  {"cos_m1_Mx_tput", probe_cos_m1_Mx_tput},
  {"cos_m2_N4", probe_cos_m2_N4},
  {"cos_m2_N4_tput", probe_cos_m2_N4_tput},
  {"cos_m2_Nx", probe_cos_m2_Nx},
  {"cos_m2_Nx_tput", probe_cos_m2_Nx_tput},
  {"cos_m2_M4", probe_cos_m2_M4},
  {"cos_m2_M4_tput", probe_cos_m2_M4_tput},
  {"cos_m2_Mx", probe_cos_m2_Mx},
  {"cos_m2_Mx_tput", probe_cos_m2_Mx_tput},
  {"cos_scalar", probe_cos_scalar},
};
const lat_probe_group lat_group_cos
  = {"cos", cos_probes, LAT_PROBE_COUNT (cos_probes)};
#endif /* LAT_GROUP_COS */

/* ------------------------------------------------------------------------
 * pow: binary function (two vector arguments), 8 ABI variants + 8
 * independent-call throughput probes + scalar libm control.
 * ------------------------------------------------------------------------ */
#ifdef LAT_GROUP_POW
#if !LAT_HAVE_ORACLE || !LAT_ORACLE_SCALAR
#error "LAT_GROUP_POW needs -DLAT_SCALAR_ORACLE=1"
#endif
#define LAT_CALL_POW_M1_M2(x, z) _ZGV1M2vv_pow (lat_mask, (x), (z))
#define LAT_CALL_POW_M1_Mx(x, z) _ZGV1Mxvv_pow (lat_mask, (x), (z))
#define LAT_CALL_POW_M2_M4(x, z) _ZGV2M4vv_pow (lat_mask, (x), (z))
#define LAT_CALL_POW_M2_Mx(x, z) _ZGV2Mxvv_pow (lat_mask, (x), (z))

LAT_DEFINE_BINARY_MAP_PROBE (pow_m1_N2, "pow_m1_N2", LAT_LANES_M1, vfloat64m1_t,
			    __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, lat_map_pow_x_m1, lat_map_pow_z_m1,
			    _ZGV1N2vv_pow)
LAT_DEFINE_BINARY_MAP_PROBE (pow_m1_Nx, "pow_m1_Nx", LAT_LANES_M1, vfloat64m1_t,
			    __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, lat_map_pow_x_m1, lat_map_pow_z_m1,
			    _ZGV1Nxvv_pow)
LAT_DEFINE_BINARY_MASKED_MAP_PROBE (pow_m1_M2, "pow_m1_M2", LAT_LANES_M1,
				   vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
				   __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, lat_map_pow_x_m1, lat_map_pow_z_m1,
				   LAT_CALL_POW_M1_M2)
LAT_DEFINE_BINARY_MASKED_MAP_PROBE (pow_m1_Mx, "pow_m1_Mx", LAT_LANES_M1,
				   vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
				   __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, lat_map_pow_x_m1, lat_map_pow_z_m1,
				   LAT_CALL_POW_M1_Mx)
LAT_DEFINE_BINARY_MAP_PROBE (pow_m2_N4, "pow_m2_N4", LAT_LANES_M2, vfloat64m2_t,
			    __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, lat_map_pow_x_m2, lat_map_pow_z_m2,
			    _ZGV2N4vv_pow)
LAT_DEFINE_BINARY_MAP_PROBE (pow_m2_Nx, "pow_m2_Nx", LAT_LANES_M2, vfloat64m2_t,
			    __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, lat_map_pow_x_m2, lat_map_pow_z_m2,
			    _ZGV2Nxvv_pow)
LAT_DEFINE_BINARY_MASKED_MAP_PROBE (pow_m2_M4, "pow_m2_M4", LAT_LANES_M2,
				   vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
				   __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, lat_map_pow_x_m2, lat_map_pow_z_m2,
				   LAT_CALL_POW_M2_M4)
LAT_DEFINE_BINARY_MASKED_MAP_PROBE (pow_m2_Mx, "pow_m2_Mx", LAT_LANES_M2,
				   vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
				   __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, lat_map_pow_x_m2, lat_map_pow_z_m2,
				   LAT_CALL_POW_M2_Mx)

LAT_DEFINE_BINARY_TPUT_PROBE (pow_m1_N2_tput, "pow_m1_N2_tput", LAT_LANES_M1, vfloat64m1_t,
			       __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			       _ZGV1N2vv_pow)
LAT_DEFINE_BINARY_TPUT_PROBE (pow_m1_Nx_tput, "pow_m1_Nx_tput", LAT_LANES_M1, vfloat64m1_t,
			       __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1,
			       _ZGV1Nxvv_pow)
LAT_DEFINE_BINARY_MASKED_TPUT_PROBE (pow_m1_M2_tput, "pow_m1_M2_tput", LAT_LANES_M1,
				    vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
				    __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_POW_M1_M2)
LAT_DEFINE_BINARY_MASKED_TPUT_PROBE (pow_m1_Mx_tput, "pow_m1_Mx_tput", LAT_LANES_M1,
				    vfloat64m1_t, vbool64_t, __riscv_vmset_m_b64,
				    __riscv_vle64_v_f64m1, __riscv_vse64_v_f64m1, LAT_CALL_POW_M1_Mx)
LAT_DEFINE_BINARY_TPUT_PROBE (pow_m2_N4_tput, "pow_m2_N4_tput", LAT_LANES_M2, vfloat64m2_t,
			       __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			       _ZGV2N4vv_pow)
LAT_DEFINE_BINARY_TPUT_PROBE (pow_m2_Nx_tput, "pow_m2_Nx_tput", LAT_LANES_M2, vfloat64m2_t,
			       __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2,
			       _ZGV2Nxvv_pow)
LAT_DEFINE_BINARY_MASKED_TPUT_PROBE (pow_m2_M4_tput, "pow_m2_M4_tput", LAT_LANES_M2,
				    vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
				    __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_POW_M2_M4)
LAT_DEFINE_BINARY_MASKED_TPUT_PROBE (pow_m2_Mx_tput, "pow_m2_Mx_tput", LAT_LANES_M2,
				    vfloat64m2_t, vbool32_t, __riscv_vmset_m_b32,
				    __riscv_vle64_v_f64m2, __riscv_vse64_v_f64m2, LAT_CALL_POW_M2_Mx)

/* pow: x' = 0.5*clamp(y,0.5,2.0) + 0.4259006208709515, z' = x'+0.5.  The
 * offset is 0.8 - 0.5*0.8^1.3, so (x,z)=(0.8,1.3) is a fixed point with
 * y=0.748 strictly inside the clamp (local slope 0.52).  The old map drove
 * x to 2 and z to 2.5, where pow(2,2.5)>2 made the branchy clamp select the
 * constant 2 for every following iteration. */
static double
lat_scalar_map_pow_x (double y)
{
  return 0.5 * lat_scalar_clamp (y, 0.5, 2.0) + 0.4259006208709515;
}
static double
lat_scalar_map_pow_z (double x)
{
  return x + 0.5;
}
LAT_DEFINE_SCALAR_BINARY_MAP_PROBE (pow_scalar, "pow_scalar",
				  lat_scalar_map_pow_x, lat_scalar_map_pow_z, pow)

static const lat_probe pow_probes[] = {
  {"pow_m1_N2", probe_pow_m1_N2},
  {"pow_m1_N2_tput", probe_pow_m1_N2_tput},
  {"pow_m1_Nx", probe_pow_m1_Nx},
  {"pow_m1_Nx_tput", probe_pow_m1_Nx_tput},
  {"pow_m1_M2", probe_pow_m1_M2},
  {"pow_m1_M2_tput", probe_pow_m1_M2_tput},
  {"pow_m1_Mx", probe_pow_m1_Mx},
  {"pow_m1_Mx_tput", probe_pow_m1_Mx_tput},
  {"pow_m2_N4", probe_pow_m2_N4},
  {"pow_m2_N4_tput", probe_pow_m2_N4_tput},
  {"pow_m2_Nx", probe_pow_m2_Nx},
  {"pow_m2_Nx_tput", probe_pow_m2_Nx_tput},
  {"pow_m2_M4", probe_pow_m2_M4},
  {"pow_m2_M4_tput", probe_pow_m2_M4_tput},
  {"pow_m2_Mx", probe_pow_m2_Mx},
  {"pow_m2_Mx_tput", probe_pow_m2_Mx_tput},
  {"pow_scalar", probe_pow_scalar},
};
const lat_probe_group lat_group_pow
  = {"pow", pow_probes, LAT_PROBE_COUNT (pow_probes)};
#endif /* LAT_GROUP_POW */
