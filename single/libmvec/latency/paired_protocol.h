/* Shared protocol of the paired exact-input measurement (protocol=2).
 *
 * One BIN contains, for every case, both measurement modes:
 *
 *   latency   the result of call j reaches call j+1 through the exact-input
 *             reset bridge of paired_bridge.h (2-3 extra FP instructions);
 *   tput      the same ordered seed sequence is consumed by independent calls
 *             (no result feedback), S streams interleaved inside a pass.
 *
 * Both modes use the same ordered frozen input prefix and masks. Latency
 * uses B=S=1; throughput may batch passes and unroll independent streams.
 * Use cycles/calls for nonzero work; cycles are raw and calls include R.
 *
 * This is independent of the throughput-only LIBMVEC_CSV v3 protocol.
 * Integers are decimal except FNV hashes, printed as up to 16 hex digits:
 *   PAIR_HEADER,protocol=2,vlenb=<n>,vlen=<n>,candidate=<id>,lto=<off|on>,
 *               modes=<both|latency|tput>,...,timing_repeat=<auto|1..64>,...
 *   PAIR_CASE,<index>,<case>,<function>,<L>  (all before any P row)
 *   PAIR_SEED,function=<f>,N=<n>,count=<n>,unary_fnv=<h>,pair_fnv=<h>
 *   P,<index>,<l|t>,<N>,<B>,<K>,<S>,<round>,<calls>,<cycles>,<R>
 *   PAIR_SKIP,case=,mode=,kind=n_gt_corpus,N=,corpus=
 *   PAIR_SKIP,case=,mode=,kind=n_not_multiple_of_lanes,N=,L=
 *   PAIR_SKIP,case=,mode=,kind=ncall_not_divisible_by_streams,N=,L=,B=,K=,streams=
 *   PAIR_WARN,case=,mode=,N=,B=,K=,S=,kind=window_too_short,
 *             window_min=,window_cycles=,timing_repeat=64
 *   PAIR_CHECK,case=,mode=,N=,checks=,failed=
 *   PAIR_END,status=<ok|fail>,checks=,failed=,records=
 *
 * Untimed trace builds replace PAIR_CASE/P with verbose PAIR records carrying
 * the probe, full point, phase, and argument/mask recovery diagnostics.
 */
#ifndef PAIRED_PROTOCOL_H
#define PAIRED_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define PAIRED_PROTOCOL 2

/* One window of one (case, mode, N, batch, K) point. */
typedef struct
{
  uint64_t begin;
  uint64_t cycles;
  uint64_t calls;       /* entry calls retired inside the window */
  uint64_t checksum;    /* FNV-1a of the out-of-window oracle output */
  uint64_t arg_fnv;     /* trace: FNV-1a of every call argument bit */
  uint64_t mask_fnv;    /* trace: FNV-1a of the predicate bits */
  unsigned steps;       /* lane-vectors executed in the timed loop */
  unsigned repeat;      /* timing_repeat R of this timed interval */
  unsigned zero_bad;    /* trace: lanes where r-r was not +0.0 */
  unsigned recover_bad; /* trace: lanes where seed_next+d != seed_next */
  unsigned finite_bad;  /* trace: lanes where r was inf/NaN */
} pair_window;

typedef void (*pair_probe_fn) (unsigned round, pair_window *w);

typedef struct
{
  const char *name;    /* unique probe symbol suffix */
  const char *case_id; /* <fn>|<lmul>|<layout>|<mask> */
  const char *function;
  const char *lmul;
  const char *layout; /* fixed | scalable */
  const char *masked; /* unmasked | masked */
  unsigned lanes;     /* lane vectors per call at this VLEN */
  unsigned binary;    /* 1 for atan2 (two frozen operand arrays) */
  pair_probe_fn latency;
  pair_probe_fn throughput;
} pair_probe;

typedef struct
{
  const char *name;
  const pair_probe *probes;
  unsigned count;
} pair_probe_group;

#define PAIR_PROBE_COUNT(a) (sizeof (a) / sizeof ((a)[0]))

#ifndef PAIRED_MAX_N
#define PAIRED_MAX_N 1024
#endif
#ifndef PAIRED_MAX_STREAMS
#define PAIRED_MAX_STREAMS 4
#endif
#ifndef PAIRED_ROUNDS
#define PAIRED_ROUNDS 10
#endif
#ifndef PAIRED_SERIAL
#define PAIRED_SERIAL 1
#endif
#ifndef PAIRED_TRACE
#define PAIRED_TRACE 0
#endif

/* ---- timing_repeat standard (see latency/README.zh.md) --------------------
 *
 * Every rdcycle interval must cover R x k calls: the measured window is
 * W = R * (unit calls) * c.  The publish target is 0.05% relative precision,
 * so a ~10-cycle timer overhead needs W >= 20,000 cycles; auto mode targets
 * 40,000 cycles for margin, and a cell whose window stays below W_min after R
 * has been clamped to 64 is flagged window_too_short (raise K or N) instead of
 * being silently reported as a precise number.
 *
 * Auto may choose a different R per point and mode; read R from each window.
 * Compare identical work using cycles/calls, or fit cycles/R vs calls/R.
 * K=0 carries no work and is exempt from the window quality threshold.
 */
#ifndef PAIRED_WINDOW_MIN
#define PAIRED_WINDOW_MIN 20000
#endif
#ifndef PAIRED_WINDOW_TARGET
#define PAIRED_WINDOW_TARGET 40000
#endif
#ifndef PAIRED_MAX_REPEAT
#define PAIRED_MAX_REPEAT 64
#endif
#ifndef PAIRED_MIN_STEADY
#define PAIRED_MIN_STEADY 5
#endif

/* Live knobs set by paired_main.c before every probe call. */
extern unsigned pair_n;
extern unsigned pair_batch;
extern unsigned pair_k;
extern unsigned pair_streams;
/* Calls-per-interval multiplier R: the probe repeats its unit work R times
 * inside the same rdcycle pair, so one window covers R x unit calls.  Always
 * >= 1; 1 is the legacy behaviour. */
extern unsigned pair_repeat;

/* Frozen corpus of the case being measured: the per-function translation unit
 * converts the header bit patterns to doubles once and main points these at
 * the function's arrays.  pair_seed0 is input_bits[] (unary operand / atan2
 * y), pair_seed1 is second_bits[] (atan2 x) or aliases seed0 for unary. */
extern const double *pair_seed0;
extern const double *pair_seed1;
extern unsigned pair_seed_count;

/* Oracle counters, reset by main for each (case, mode, N) block. */
extern unsigned pair_checks;
extern unsigned pair_failed;

/* ---- common helpers ---------------------------------------------------- */

static inline uint64_t
pair_cycle (void)
{
  uint64_t c;
  __asm__ volatile ("rdcycle %0" : "=r"(c)::"memory");
  return c;
}

/* sfence.vma as a drain before/after the window: same serialisation policy as
 * the legacy harness (LAT_SERIAL).  It is a platform-level ordering aid, not a
 * claim that the CSR read is architecturally serialising everywhere. */
static inline uint64_t
pair_cycle_drain (void)
{
  __asm__ volatile ("sfence.vma zero, zero" ::: "memory");
  return pair_cycle ();
}

#if PAIRED_SERIAL
#define PAIR_WIN_BEGIN() pair_cycle_drain ()
#define PAIR_WIN_END() pair_cycle_drain ()
#else
#define PAIR_WIN_BEGIN() pair_cycle ()
#define PAIR_WIN_END() pair_cycle ()
#endif

static inline uint64_t
pair_bits (const double *p)
{
  uint64_t b;
  __builtin_memcpy (&b, p, sizeof b);
  return b;
}

/* finite: exponent field is not all ones (works for +-inf and every NaN). */
static inline int
pair_isfinite_bits (uint64_t b)
{
  return ((b >> 52) & 0x7ff) != 0x7ff;
}

/* total order on IEEE-754 bit patterns (one ULP == one step). */
static inline uint64_t
pair_ordered (uint64_t b)
{
  const uint64_t sign = 1ULL << 63;
  return (b & sign) ? ~b : (b | sign);
}

static inline uint64_t
pair_fnv (uint64_t h, uint64_t v)
{
  return (h ^ v) * 1099511628211ULL;
}

#define PAIR_FNV_INIT 1469598103934665603ULL

static inline uint64_t
pair_fnv_buf (uint64_t h, const double *p, unsigned n)
{
  for (unsigned i = 0; i < n; ++i)
    h = pair_fnv (h, pair_bits (p + i));
  return h;
}

/* Frozen seed pointer for lane-vector ``vec``: the corpus is walked in scalar
 * order, so vector k of a call is seed[(vec * lanes) + lane]. */
static inline const double *
pair_seed_vec (const double *base, unsigned vec, unsigned lanes)
{
  return base + (size_t)vec * lanes;
}

#endif /* PAIRED_PROTOCOL_H */
