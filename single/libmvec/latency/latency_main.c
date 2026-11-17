/* Entry point of the baremetal libmvec latency harness.
 *
 * Runs a fixed matrix: enabled probe groups x K values x LAT_ROUNDS windows.
 * Every window is buffered in RAM and printed only after the whole matrix has
 * been measured.  That is not cosmetic: one UART line costs more than a
 * thousand guest cycles on the RTL model, so printing between windows would
 * both dominate the program and inject a variable delay into the next window.
 *
 * Output grammar (all integers decimal except the hex checksums):
 *   LAT_HEADER,protocol=1,vlenb=,vlen=,rounds=,prelude=,serial=,nop_block=,
 *              nop_block64=,k=,groups=,entries=
 *   LAT,entry=<name>,K=<k>,phase=<first|warmup|steady>,calls=<k>,begin=<c>,
 *       cycles=<c>,checksum=<hex16>
 *   LAT_CHAIN,entry=<name>,K=<k>,chain_checksum=<hex16>,stable=<yes|no>
 *   LAT_CHECK,entry=<name>,checks=<n>,failed=<n>
 *   LAT_END,status=<ok|fail>,checks=<n>,failed=<n>
 *   LAT_ERROR,...   (oracle mismatch, unsupported K, bad vlenb, full buffer)
 *
 * The LAT_ROUNDS windows of one (entry, K) appear in round order: round 0 is
 * `first`, rounds 1-2 are `warmup`, rounds 3+ are `steady`.  Only `steady`
 * feeds the medians in latency_parse.py, so the phase stays explicit in every
 * line and the raw settling remains reviewable.
 */
#include <stdint.h>
#include <stddef.h>
#include <riscv_vector.h>
#include "av_runtime.h"
#include "latency_probe.h"

/* Oracle counters shared with the probe translation units. */
unsigned lat_checks;
unsigned lat_failed;

#ifndef LAT_MAX_RECORDS
#define LAT_MAX_RECORDS 4096
#endif

typedef struct {
  const char *entry;
  uint16_t k;
  uint16_t round;
  lat_window w;
} lat_record;

static lat_record lat_records[LAT_MAX_RECORDS];
static unsigned lat_nrecords;
static unsigned lat_dropped;

static unsigned lat_k[LAT_MAX_K];
static unsigned lat_nk;

static int
lat_str_eq_n (const char *a, const char *b, unsigned n)
{
  for (unsigned i = 0; i < n; ++i)
    if (a[i] != b[i])
      return 0;
  return 1;
}

/* Empty allow list selects every entry. */
static int
lat_wanted (const char *name)
{
  const char *p = LAT_ONLY_ENTRIES;
  if (!*p)
    return 1;
  while (*p)
    {
      const char *start = p;
      unsigned len;
      while (*p && *p != ';')
	++p;
      len = (unsigned) (p - start);
      if (len && lat_str_eq_n (start, name, len) && name[len] == '\0')
	return 1;
      if (*p == ';')
	++p;
    }
  return 0;
}

static const char *
lat_phase (unsigned round)
{
  return round == 0 ? "first" : (round < 3 ? "warmup" : "steady");
}

static void
lat_parse_k_list (void)
{
  const char *p = LAT_K_LIST;
  lat_nk = 0;
  while (*p && lat_nk < LAT_MAX_K)
    {
      unsigned v = 0;
      int digits = 0;
      while (*p >= '0' && *p <= '9')
	{
	  v = v * 10u + (unsigned) (*p - '0');
	  ++p;
	  digits = 1;
	}
      if (digits)
	lat_k[lat_nk++] = v;
      else
	++p;
      while (*p == ' ' || *p == ',' || *p == '\t')
	++p;
    }
}

static void
lat_push (const char *entry, unsigned k, unsigned round, const lat_window *w)
{
  if (lat_nrecords >= LAT_MAX_RECORDS)
    {
      ++lat_dropped;
      return;
    }
  lat_records[lat_nrecords].entry = entry;
  lat_records[lat_nrecords].k = (uint16_t) k;
  lat_records[lat_nrecords].round = (uint16_t) round;
  lat_records[lat_nrecords].w = *w;
  ++lat_nrecords;
}

static void
lat_print_k_list (void)
{
  for (unsigned i = 0; i < lat_nk; ++i)
    printf ("%s%u", i ? ";" : "", lat_k[i]);
}

static void
lat_print_groups (void)
{
  int first = 1;
#define LAT_PRINT_GROUP(name)						\
  do {									\
    printf ("%s%s", first ? "" : ";", (name));				\
    first = 0;								\
  } while (0)
#ifdef LAT_GROUP_CALIB
  LAT_PRINT_GROUP ("calib");
#endif
#ifdef LAT_GROUP_ATAN
  LAT_PRINT_GROUP ("atan");
#endif
#ifdef LAT_GROUP_ACOS_NX
  LAT_PRINT_GROUP ("acos_nx");
#endif
#ifdef LAT_GROUP_ACOS_MASKED
  LAT_PRINT_GROUP ("acos_masked");
#endif
#ifdef LAT_GROUP_EXP
  LAT_PRINT_GROUP ("exp");
#endif
#ifdef LAT_GROUP_LOG
  LAT_PRINT_GROUP ("log");
#endif
#ifdef LAT_GROUP_SIN
  LAT_PRINT_GROUP ("sin");
#endif
#ifdef LAT_GROUP_COS
  LAT_PRINT_GROUP ("cos");
#endif
#ifdef LAT_GROUP_POW
  LAT_PRINT_GROUP ("pow");
#endif
#undef LAT_PRINT_GROUP
  if (first)
    printf ("none");
}

static void
lat_print_entries (void)
{
  int first = 1;
#define LAT_PRINT_ENTRIES(group)					\
  do {									\
    for (unsigned i = 0; i < (group).count; ++i)			\
      if (lat_wanted ((group).probes[i].name))				\
	{								\
	  printf ("%s%s", first ? "" : ";", (group).probes[i].name);	\
	  first = 0;							\
	}								\
  } while (0)
#ifdef LAT_GROUP_CALIB
  LAT_PRINT_ENTRIES (lat_group_calib);
#endif
#ifdef LAT_GROUP_ATAN
  LAT_PRINT_ENTRIES (lat_group_atan);
#endif
#ifdef LAT_GROUP_ACOS_NX
  LAT_PRINT_ENTRIES (lat_group_acos_nx);
#endif
#ifdef LAT_GROUP_ACOS_MASKED
  LAT_PRINT_ENTRIES (lat_group_acos_masked);
#endif
#ifdef LAT_GROUP_EXP
  LAT_PRINT_ENTRIES (lat_group_exp);
#endif
#ifdef LAT_GROUP_LOG
  LAT_PRINT_ENTRIES (lat_group_log);
#endif
#ifdef LAT_GROUP_SIN
  LAT_PRINT_ENTRIES (lat_group_sin);
#endif
#ifdef LAT_GROUP_COS
  LAT_PRINT_ENTRIES (lat_group_cos);
#endif
#ifdef LAT_GROUP_POW
  LAT_PRINT_ENTRIES (lat_group_pow);
#endif
#undef LAT_PRINT_ENTRIES
  if (first)
    printf ("none");
}

static void
lat_run_group (const lat_probe_group *g, unsigned *checks, unsigned *failed)
{
  *checks = 0;
  *failed = 0;
  for (unsigned pi = 0; pi < g->count; ++pi)
    {
      const lat_probe *p = &g->probes[pi];
      if (!lat_wanted (p->name))
	continue;
      lat_checks = 0;
      lat_failed = 0;
      for (unsigned ki = 0; ki < lat_nk; ++ki)
	{
	  unsigned k = lat_k[ki];
	  for (unsigned r = 0; r < LAT_ROUNDS; ++r)
	    {
	      lat_window w;

	      w.begin = 0;
	      w.cycles = 0;
	      w.checksum = 0;
	      w.chain_checksum = 0;
	      /* Default: one chained call per K step.  The throughput probes
	       * overwrite this with 4*K (four independent streams). */
	      w.calls = k;
	      p->run (k, r, &w);
	      lat_push (p->name, k, r, &w);
	    }
	}
      printf ("LAT_CHECK,entry=%s,checks=%u,failed=%u\n",
	      p->name, lat_checks, lat_failed);
      *checks += lat_checks;
      *failed += lat_failed;
    }
}

/* One LAT_CHAIN line per (entry, K): the chained state after the last window
 * plus whether all windows of that block produced the same state.  A stable
 * state proves the closed loop really ran; a changing one means the map is not
 * contracting or the call was optimised away. */
static void
lat_print_chain_lines (void)
{
  for (unsigned i = 0; i < lat_nrecords; ++i)
    {
      const char *entry = lat_records[i].entry;
      unsigned k = lat_records[i].k;
      unsigned first_idx = i;
      int last = 1;
      int stable = 1;
      uint64_t value = lat_records[i].w.chain_checksum;

      for (unsigned j = i + 1; j < lat_nrecords; ++j)
	if (lat_records[j].entry == entry && lat_records[j].k == k)
	  {
	    last = 0;
	    break;
	  }
      if (!last)
	continue;
      while (first_idx > 0
	     && lat_records[first_idx - 1].entry == entry
	     && lat_records[first_idx - 1].k == k)
	--first_idx;
      for (unsigned j = first_idx; j <= i; ++j)
	if (lat_records[j].w.chain_checksum != value)
	  stable = 0;
      printf ("LAT_CHAIN,entry=%s,K=%u,chain_checksum=%llx,stable=%s\n",
	      entry, k, (unsigned long long) value, stable ? "yes" : "no");
    }
}

static void
lat_print_records (void)
{
  for (unsigned i = 0; i < lat_nrecords; ++i)
    {
      const lat_record *rec = &lat_records[i];
      printf ("LAT,entry=%s,K=%u,phase=%s,calls=%u,begin=%llu,cycles=%llu,"
	      "checksum=%llx\n",
	      rec->entry, rec->k, lat_phase (rec->round), rec->w.calls,
	      (unsigned long long) rec->w.begin,
	      (unsigned long long) rec->w.cycles,
	      (unsigned long long) rec->w.checksum);
    }
}

int
main (void)
{
  uint64_t vlenb;
  unsigned total_checks = 0, total_failed = 0, failed, checks;

  __asm__ volatile ("csrr %0, vlenb" : "=r"(vlenb));
  if (vlenb != 16)
    {
      printf ("LAT_ERROR,entry=main,kind=vlenb,vlenb=%llu\n",
	      (unsigned long long) vlenb);
      return 1;
    }

  lat_parse_k_list ();
  if (lat_nk == 0)
    {
      printf ("LAT_ERROR,entry=main,kind=empty_k_list\n");
      return 1;
    }

  printf ("LAT_HEADER,protocol=1,vlenb=16,vlen=128,rounds=%u,prelude=%u,"
	  "serial=%u,nop_block=%u,nop_block64=64,k=",
	  (unsigned) LAT_ROUNDS, (unsigned) LAT_PRELUDE, (unsigned) LAT_SERIAL,
	  (unsigned) LAT_NOP_BLOCK);
  lat_print_k_list ();
  printf (",groups=");
  lat_print_groups ();
  printf (",entries=");
  lat_print_entries ();
  printf ("\n");

#ifdef LAT_GROUP_CALIB
  lat_run_group (&lat_group_calib, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_ATAN
  lat_run_group (&lat_group_atan, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_ACOS_NX
  lat_run_group (&lat_group_acos_nx, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_ACOS_MASKED
  lat_run_group (&lat_group_acos_masked, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_EXP
  lat_run_group (&lat_group_exp, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_LOG
  lat_run_group (&lat_group_log, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_SIN
  lat_run_group (&lat_group_sin, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_COS
  lat_run_group (&lat_group_cos, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif
#ifdef LAT_GROUP_POW
  lat_run_group (&lat_group_pow, &checks, &failed);
  total_checks += checks;
  total_failed += failed;
#endif

  if (lat_dropped)
    {
      printf ("LAT_ERROR,entry=main,kind=record_buffer_full,dropped=%u\n",
	      lat_dropped);
      total_failed += lat_dropped;
    }

  lat_print_records ();
  lat_print_chain_lines ();
  printf ("LAT_END,status=%s,checks=%u,failed=%u\n",
	  total_failed ? "fail" : "ok", total_checks, total_failed);
  return total_failed ? 1 : 0;
}
