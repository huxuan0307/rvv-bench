/* Entry point of the paired exact-input measurement (protocol=2).
 *
 * One BIN measures every enabled case in both modes on the same frozen header:
 *
 *   latency   chained calls, next argument = bridge(result, frozen seed)
 *   tput      the same ordered frozen seeds, independent calls (S streams)
 *
 * The run is fully driven by build-time lists so a matrix slice is one build:
 *   PAIRED_N_LIST  scalar elements per pass (comma separated)
 *   PAIRED_B_LIST  passes per timing interval (throughput; latency uses B=1)
 *   PAIRED_K_LIST  batch multipliers
 *   PAIRED_S_LIST  throughput stream counts (1,2,4)
 *   PAIRED_MODES   both / latency / tput
 *   PAIRED_ROUNDS  windows per (case, mode, N, B, K, S)
 *
 * Output grammar: see paired_protocol.h.  Every PAIR line carries the mode,
 * the case, the full window workpoint (N, lanes, batch, K, streams, round) and
 * the frozen-seed FNV identity printed once per (function, N) as PAIR_SEED.
 * A BIN that skips a point because it is not representable (N not a multiple
 * of the lane count, N larger than the frozen corpus, ncall not divisible by
 * S) says so with PAIR_SKIP instead of silently measuring something else.
 */
#include <stdint.h>
#include "av_runtime.h"
#include <riscv_vector.h>

#include "paired_protocol.h"
#include "paired_api.h"

#ifndef PAIRED_VLENB
#define PAIRED_VLENB 16
#endif
#ifndef PAIRED_CANDIDATE
#define PAIRED_CANDIDATE "unspecified"
#endif
#ifndef PAIRED_LTO
#define PAIRED_LTO "off"
#endif
#ifndef PAIRED_MODES
#define PAIRED_MODES "both"
#endif
#ifndef PAIRED_N_LIST
#define PAIRED_N_LIST "128,256"
#endif
#ifndef PAIRED_B_LIST
#define PAIRED_B_LIST "8"
#endif
#ifndef PAIRED_K_LIST
#define PAIRED_K_LIST "0,1,2,4,8"
#endif
#ifndef PAIRED_S_LIST
#define PAIRED_S_LIST "1,2,4"
#endif
#ifndef PAIRED_MAX_LIST
#define PAIRED_MAX_LIST 16
#endif
#ifndef PAIRED_ONLY_CASES
#define PAIRED_ONLY_CASES ""
#endif
/* Requested timing_repeat: "auto" estimates R from a one-window calibration
 * sample of the same point, or an explicit integer in [1, 64].  The probes
 * repeat their unit work R times inside one rdcycle interval, so a window
 * covers R x (unit calls) calls and its length W can be brought above the
 * publish threshold without touching the workload definition. */
#ifndef PAIRED_TIMING_REPEAT
#define PAIRED_TIMING_REPEAT "1"
#endif

/* Live knobs read by the probes. */
unsigned pair_n;
unsigned pair_batch;
unsigned pair_k;
unsigned pair_streams;
unsigned pair_repeat = 1;
const double *pair_seed0;
const double *pair_seed1;
unsigned pair_seed_count;

/* Oracle counters incremented by the probes. */
unsigned pair_checks;
unsigned pair_failed;

static unsigned pair_n_list[PAIRED_MAX_LIST];
static unsigned pair_b_list[PAIRED_MAX_LIST];
static unsigned pair_k_list[PAIRED_MAX_LIST];
static unsigned pair_s_list[PAIRED_MAX_LIST];
static unsigned pair_nn, pair_nb, pair_nk, pair_ns;

typedef struct
{
  const char *name;
  const pair_probe_group *group;
  void (*seed_init) (unsigned);
  const double *(*seed0) (void);
  const double *(*seed1) (void);
  unsigned (*seed_count) (void);
  uint64_t (*seed_fnv) (unsigned);
  uint64_t (*pair_fnv) (unsigned);
} pair_function;

static const pair_function pair_functions[] = {
#ifdef PAIRED_GROUP_ATAN
  { "atan", &pair_group_atan, pair_atan_seed_init, pair_atan_seed0_ptr,
    pair_atan_seed1_ptr, pair_atan_seed_count, pair_atan_seed_fnv,
    pair_atan_pair_fnv },
#endif
#ifdef PAIRED_GROUP_ATAN2
  { "atan2", &pair_group_atan2, pair_atan2_seed_init, pair_atan2_seed0_ptr,
    pair_atan2_seed1_ptr, pair_atan2_seed_count, pair_atan2_seed_fnv,
    pair_atan2_pair_fnv },
#endif
#ifdef PAIRED_GROUP_ACOS
  { "acos", &pair_group_acos, pair_acos_seed_init, pair_acos_seed0_ptr,
    pair_acos_seed1_ptr, pair_acos_seed_count, pair_acos_seed_fnv,
    pair_acos_pair_fnv },
#endif
#ifdef PAIRED_GROUP_LOG10
  { "log10", &pair_group_log10, pair_log10_seed_init, pair_log10_seed0_ptr,
    pair_log10_seed1_ptr, pair_log10_seed_count, pair_log10_seed_fnv,
    pair_log10_pair_fnv },
#endif
#ifdef PAIRED_GROUP_CALIB
  { "calib", &pair_group_calib, pair_atan_seed_init, pair_atan_seed0_ptr,
    pair_atan_seed1_ptr, pair_atan_seed_count, pair_atan_seed_fnv,
    pair_atan_pair_fnv },
#endif
};

#define PAIR_NFUNCS (sizeof (pair_functions) / sizeof (pair_functions[0]))

#if PAIRED_TRACE
static const char *
pair_phase (unsigned round)
{
  return round == 0 ? "first" : (round < 3 ? "warmup" : "steady");
}
#endif

/* Comma-separated non-negative integers from a build-time list. */
static unsigned
pair_parse_list (const char *text, unsigned *out, unsigned cap)
{
  unsigned count = 0;
  const char *p = text;
  while (*p && count < cap)
    {
      unsigned v = 0;
      int digits = 0;
      while (*p >= '0' && *p <= '9')
        {
          v = v * 10u + (unsigned)(*p - '0');
          ++p;
          ++digits;
        }
      if (digits)
        out[count++] = v;
      else
        ++p;
      while (*p == ',' || *p == ';' || *p == ' ' || *p == '\t')
        ++p;
    }
  return count;
}

static void
pair_print_header (void)
{
  printf ("PAIR_HEADER,protocol=%d,vlenb=%u,vlen=%u,candidate=%s,lto=%s,"
          "modes=%s,lengths=%s,batches=%s,streams=%s,k=%s,rounds=%u,serial=%u,"
          "trace=%d,seed_order=scalar_prefix,latency_batch=1,"
          "timing_repeat=%s,window_min=%u,window_target=%u,max_repeat=%u,"
          "min_steady=%u,only_cases=%s\n",
          PAIRED_PROTOCOL, (unsigned)PAIRED_VLENB, (unsigned)PAIRED_VLENB * 8,
          PAIRED_CANDIDATE, PAIRED_LTO, PAIRED_MODES, PAIRED_N_LIST,
          PAIRED_B_LIST, PAIRED_S_LIST, PAIRED_K_LIST, (unsigned)PAIRED_ROUNDS,
          (unsigned)PAIRED_SERIAL, PAIRED_TRACE, PAIRED_TIMING_REPEAT,
          (unsigned)PAIRED_WINDOW_MIN, (unsigned)PAIRED_WINDOW_TARGET,
          (unsigned)PAIRED_MAX_REPEAT, (unsigned)PAIRED_MIN_STEADY,
          PAIRED_ONLY_CASES);
}

#if PAIRED_TRACE
static void
pair_print_window (const pair_probe *p, const char *mode, unsigned n,
                   unsigned batch, unsigned k, unsigned streams,
                   unsigned round, const pair_window *w)
{
  printf ("PAIR,probe=%s,case=%s,function=%s,mode=%s,N=%u,L=%u,batch=%u,K=%u,"
          "streams=%u,round=%u,phase=%s,calls=%llu,begin=%llu,cycles=%llu,"
          "checksum=%llx,arg_fnv=%llx,mask_fnv=%llx,zero_bad=%u,"
          "recover_bad=%u,finite_bad=%u,trace=%d,timing_repeat=%u\n",
          p->name, p->case_id, p->function, mode, n, p->lanes, batch, k,
          streams, round, pair_phase (round), (unsigned long long)w->calls,
          (unsigned long long)w->begin, (unsigned long long)w->cycles,
          (unsigned long long)w->checksum, (unsigned long long)w->arg_fnv,
          (unsigned long long)w->mask_fnv, w->zero_bad, w->recover_bad,
          w->finite_bad, PAIRED_TRACE, w->repeat);
}
#endif

static uint64_t pair_records;
static uint64_t pair_checks_total;

#if !PAIRED_TRACE
/* Flush bounded chunks outside timed windows. A prefix is diagnostic until
 * the terminal row; every case is declared once before the first window. */
#define PAIRED_FLUSH_WINDOWS 64
typedef struct
{
  unsigned cidx, round, n, batch, k, streams, repeat;
  char mode;
  uint64_t calls, cycles;
} pair_capture_record;

static pair_capture_record pair_capture[PAIRED_FLUSH_WINDOWS];
static unsigned pair_capture_n;

static void
pair_capture_flush (void)
{
  for (unsigned i = 0; i < pair_capture_n; ++i)
    {
      const pair_capture_record *r = &pair_capture[i];
      printf ("P,%u,%c,%u,%u,%u,%u,%u,%llu,%llu,%u\n", r->cidx, r->mode, r->n,
              r->batch, r->k, r->streams, r->round,
              (unsigned long long)r->calls, (unsigned long long)r->cycles,
              r->repeat);
    }
  pair_capture_n = 0;
}

static void
pair_record_window (unsigned cidx, const char *mode, unsigned n,
                    unsigned batch, unsigned k, unsigned streams,
                    unsigned round, const pair_window *w)
{
  pair_capture[pair_capture_n++] = (pair_capture_record){
    .cidx = cidx,
    .round = round,
    .n = n,
    .batch = batch,
    .k = k,
    .streams = streams,
    .repeat = w->repeat,
    .mode = mode[0],
    .calls = w->calls,
    .cycles = w->cycles,
  };
  if (pair_capture_n == PAIRED_FLUSH_WINDOWS)
    pair_capture_flush ();
}
#endif /* !PAIRED_TRACE */

/* Fixed R is shared across points; auto estimates R per point and mode from
 * one unreported window. K=0 is a diagnostic, not a minimum-length sample. */
static unsigned pair_repeat_spec_value = 1;
static int pair_repeat_auto;

static int
pair_parse_timing_repeat (const char *spec)
{
  unsigned v = 0;
  const char *p = spec;
  if (spec[0] == 'a' && spec[1] == 'u' && spec[2] == 't' && spec[3] == 'o'
      && spec[4] == '\0')
    {
      pair_repeat_auto = 1;
      pair_repeat_spec_value = 1;
      return 0;
    }
  if (!*p)
    return -1;
  while (*p >= '0' && *p <= '9')
    {
      v = v * 10u + (unsigned)(*p - '0');
      if (v > PAIRED_MAX_REPEAT)
        return -1;
      ++p;
    }
  if (*p != '\0' || v < 1 || v > PAIRED_MAX_REPEAT)
    return -1;
  pair_repeat_auto = 0;
  pair_repeat_spec_value = v;
  return 0;
}

/* Optional ';'-separated allow list of probe names (the <lmul>_<layout>_<mask>
 * suffix of a case).  Empty selects every case; a matrix split uses it so each
 * parallel BIN measures one slice. */
static int
pair_case_wanted (const char *name)
{
  const char *p = PAIRED_ONLY_CASES;
  if (!*p)
    return 1;
  while (*p)
    {
      const char *start = p;
      unsigned len;
      while (*p && *p != ';')
        ++p;
      len = (unsigned)(p - start);
      if (len)
        {
          unsigned i = 0;
          while (i < len && name[i] && name[i] == start[i])
            ++i;
          if (i == len && name[i] == '\0')
            return 1;
        }
      if (*p == ';')
        ++p;
    }
  return 0;
}

static int
pair_probe_enabled (const pair_probe *p, int do_lat, int do_tput)
{
  return pair_case_wanted (p->name)
         && ((do_lat && p->latency) || (do_tput && p->throughput));
}

#if !PAIRED_TRACE
static void
pair_print_cases (int do_lat, int do_tput)
{
  unsigned cidx = 0;
  for (unsigned fi = 0; fi < PAIR_NFUNCS; ++fi)
    {
      const pair_probe_group *group = pair_functions[fi].group;
      for (unsigned pi = 0; group && pi < group->count; ++pi)
        {
          const pair_probe *p = &group->probes[pi];
          if (pair_probe_enabled (p, do_lat, do_tput))
            printf ("PAIR_CASE,%u,%s,%s,%u\n", cidx++, p->case_id, p->function,
                    p->lanes);
        }
    }
}
#endif

static uint64_t
pair_run_mode (const pair_probe *p, unsigned cidx, const char *mode,
               const unsigned *n_list, unsigned ncount, const unsigned *b_list,
               unsigned bcount, const unsigned *s_list, unsigned scount)
{
  pair_probe_fn run = (mode[0] == 'l') ? p->latency : p->throughput;
  uint64_t block_failed = 0;
  if (run == 0)
    return 0;
  for (unsigned ni = 0; ni < ncount; ++ni)
    {
      unsigned n = n_list[ni];
      if (n > pair_seed_count)
        {
          printf (
              "PAIR_SKIP,case=%s,mode=%s,kind=n_gt_corpus,N=%u,corpus=%u\n",
              p->case_id, mode, n, pair_seed_count);
          continue;
        }
      if (n % p->lanes != 0)
        {
          printf ("PAIR_SKIP,case=%s,mode=%s,kind=n_not_multiple_of_lanes,"
                  "N=%u,L=%u\n",
                  p->case_id, mode, n, p->lanes);
          continue;
        }
      unsigned ncall = n / p->lanes;
      pair_checks = 0;
      pair_failed = 0;
      for (unsigned bi = 0; bi < bcount; ++bi)
        for (unsigned ki = 0; ki < pair_nk; ++ki)
          {
            unsigned k = pair_k_list[ki];
            for (unsigned si = 0; si < scount; ++si)
              {
                unsigned s = s_list[si];
                if (ncall % s != 0)
                  {
                    printf ("PAIR_SKIP,case=%s,mode=%s,kind=ncall_not_"
                            "divisible_by_streams,N=%u,L=%u,B=%u,K=%u,streams="
                            "%u\n",
                            p->case_id, mode, n, p->lanes, b_list[bi], k, s);
                    continue;
                  }
                if (pair_repeat_auto)
                  {
                    /* Estimate the per-window cost c from one discarded window
                     * of this exact point, then fix R =
                     * clamp(ceil(target/unit),1,64) so the formal rounds reach
                     * the publish window length.  The calibration window is
                     * not recorded and does not use the round budget; its
                     * oracle check is the same one the rounds run. */
                    pair_window cal;
                    uint64_t unit;
                    unsigned rep;
                    cal.begin = 0;
                    cal.cycles = 0;
                    cal.calls = 0;
                    cal.checksum = 0;
                    cal.arg_fnv = PAIR_FNV_INIT;
                    cal.mask_fnv = PAIR_FNV_INIT;
                    cal.steps = 0;
                    cal.repeat = 1;
                    cal.zero_bad = 0;
                    cal.recover_bad = 0;
                    cal.finite_bad = 0;
                    pair_n = n;
                    pair_batch = b_list[bi];
                    pair_k = k;
                    pair_streams = s;
                    pair_repeat = 1;
                    run (0, &cal);
                    unit = cal.cycles;
                    if (unit == 0)
                      rep = PAIRED_MAX_REPEAT;
                    else if (unit >= PAIRED_WINDOW_TARGET)
                      rep = 1;
                    else
                      rep = 1 + (PAIRED_WINDOW_TARGET - 1) / unit;
                    if (rep > PAIRED_MAX_REPEAT)
                      rep = PAIRED_MAX_REPEAT;
                    pair_repeat = rep;
                    if (k && rep == PAIRED_MAX_REPEAT
                        && unit * rep < PAIRED_WINDOW_MIN)
                      printf ("PAIR_WARN,case=%s,mode=%s,N=%u,B=%u,K=%u,S=%u,"
                              "kind=window_too_short,window_min=%u,window_"
                              "cycles=%llu,"
                              "timing_repeat=%u\n",
                              p->case_id, mode, n, b_list[bi], k, s,
                              (unsigned)PAIRED_WINDOW_MIN,
                              (unsigned long long)unit * rep, rep);
                  }
                else
                  pair_repeat = pair_repeat_spec_value;
                for (unsigned round = 0; round < PAIRED_ROUNDS; ++round)
                  {
                    pair_window w;
                    w.begin = 0;
                    w.cycles = 0;
                    w.calls = 0;
                    w.checksum = 0;
                    w.arg_fnv = PAIR_FNV_INIT;
                    w.mask_fnv = PAIR_FNV_INIT;
                    w.steps = 0;
                    w.repeat = pair_repeat;
                    w.zero_bad = 0;
                    w.recover_bad = 0;
                    w.finite_bad = 0;
                    pair_n = n;
                    pair_batch = b_list[bi];
                    pair_k = k;
                    pair_streams = s;
                    run (round, &w);
#if PAIRED_TRACE
                    pair_print_window (p, mode, n, b_list[bi], k, s, round,
                                       &w);
#else
                    pair_record_window (cidx, mode, n, b_list[bi], k, s, round,
                                        &w);
#endif
                    ++pair_records;
                  }
              }
          }
      printf ("PAIR_CHECK,case=%s,mode=%s,N=%u,checks=%u,failed=%u\n",
              p->case_id, mode, n, pair_checks, pair_failed);
      pair_checks_total += pair_checks;
      block_failed += pair_failed;
    }
  return block_failed;
}

int
main (void)
{
  uint64_t vlenb;
  const char *modes = PAIRED_MODES;
  int do_lat = 1, do_tput = 1;
  uint64_t failed_total = 0;
  unsigned cidx = 0;

  __asm__ volatile ("csrr %0, vlenb" : "=r"(vlenb));
  if (vlenb != PAIRED_VLENB)
    {
      printf ("PAIR_ERROR,entry=main,kind=vlenb,vlenb=%llu,want=%u\n",
              (unsigned long long)vlenb, (unsigned)PAIRED_VLENB);
      return 1;
    }

  if (pair_parse_timing_repeat (PAIRED_TIMING_REPEAT) != 0)
    {
      printf ("PAIR_ERROR,entry=main,kind=timing_repeat,spec=%s\n",
              PAIRED_TIMING_REPEAT);
      return 2;
    }
  if (PAIRED_TRACE)
    {
      /* The trace pass is untimed and asserts bit-exact argument identity;
       * repeating it would only add output, never a longer timed window. */
      if (pair_repeat_auto)
        pair_repeat_auto = 0;
      else if (pair_repeat_spec_value != 1)
        {
          printf ("PAIR_ERROR,entry=main,kind=trace_requires_timing_repeat_1,"
                  "spec=%s\n",
                  PAIRED_TIMING_REPEAT);
          return 2;
        }
    }
  pair_repeat = pair_repeat_auto ? 1 : pair_repeat_spec_value;

  pair_nn = pair_parse_list (PAIRED_N_LIST, pair_n_list, PAIRED_MAX_LIST);
  pair_nb = pair_parse_list (PAIRED_B_LIST, pair_b_list, PAIRED_MAX_LIST);
  pair_nk = pair_parse_list (PAIRED_K_LIST, pair_k_list, PAIRED_MAX_LIST);
  pair_ns = pair_parse_list (PAIRED_S_LIST, pair_s_list, PAIRED_MAX_LIST);
  if (pair_nn == 0 || pair_nk == 0 || pair_nb == 0 || pair_ns == 0)
    {
      printf ("PAIR_ERROR,entry=main,kind=empty_knob_list\n");
      return 1;
    }
  if (modes[0] == 'l')
    do_tput = 0;
  else if (modes[0] == 't')
    do_lat = 0;

  pair_print_header ();
#if !PAIRED_TRACE
  pair_print_cases (do_lat, do_tput);
#endif

  for (unsigned fi = 0; fi < PAIR_NFUNCS; ++fi)
    {
      const pair_function *f = &pair_functions[fi];
      unsigned max_n = 0;
      for (unsigned i = 0; i < pair_nn; ++i)
        if (pair_n_list[i] > max_n)
          max_n = pair_n_list[i];
      f->seed_init (max_n);
      pair_seed0 = f->seed0 ();
      pair_seed1 = f->seed1 ();
      pair_seed_count = f->seed_count ();
      for (unsigned i = 0; i < pair_nn; ++i)
        {
          unsigned n = pair_n_list[i];
          if (n > pair_seed_count)
            continue;
          printf ("PAIR_SEED,function=%s,N=%u,count=%u,unary_fnv=%llx,"
                  "pair_fnv=%llx\n",
                  f->name, n, pair_seed_count,
                  (unsigned long long)f->seed_fnv (n),
                  (unsigned long long)f->pair_fnv (n));
        }
      if (f->group == 0)
        continue;
      for (unsigned pi = 0; pi < f->group->count; ++pi)
        {
          const pair_probe *p = &f->group->probes[pi];
          if (!pair_probe_enabled (p, do_lat, do_tput))
            continue;
          if (do_lat && p->latency)
            {
              unsigned one = 1;
              unsigned sone = 1;
              failed_total += pair_run_mode (p, cidx, "latency", pair_n_list,
                                             pair_nn, &one, 1, &sone, 1);
            }
          if (do_tput && p->throughput)
            failed_total
                += pair_run_mode (p, cidx, "tput", pair_n_list, pair_nn,
                                  pair_b_list, pair_nb, pair_s_list, pair_ns);
          ++cidx;
        }
    }

#if !PAIRED_TRACE
  pair_capture_flush ();
#endif
  if (!pair_records)
    {
      printf ("PAIR_ERROR,entry=main,kind=no_windows\n");
      ++failed_total;
    }
  printf ("PAIR_END,status=%s,checks=%llu,failed=%llu,records=%llu\n",
          failed_total ? "fail" : "ok", (unsigned long long)pair_checks_total,
          (unsigned long long)failed_total, (unsigned long long)pair_records);
  return failed_total ? 1 : 0;
}
