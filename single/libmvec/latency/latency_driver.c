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
#else
/* Calibration probes have no oracle.  Values sit inside [-1, 1] so the maps
 * stay in domain; both chains converge to the same fixed point regardless. */
static const uint64_t lat_input_local[4] = {
  0x3fd0000000000000ULL,	/*  0.25  */
  0xbfe0000000000000ULL,	/* -0.5   */
  0x3fe8000000000000ULL,	/*  0.75  */
  0xbfc0000000000000ULL,	/* -0.125 */
};
static const uint64_t *const lat_input = lat_input_local;
#define LAT_HAVE_ORACLE 0
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
