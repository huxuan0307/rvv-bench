/* Shared probe protocol for the baremetal libmvec latency harness.
 *
 * A probe is one closed timing window: it applies a chained step K times inside
 * a single begin/end rdcycle pair and reports the raw window.  All numbers are
 * raw cycles; host-side regression (latency_parse.py) turns K -> cycles into
 * slope/intercept.
 */
#ifndef LATENCY_PROBE_H
#define LATENCY_PROBE_H

#include <stdint.h>

/* 1 first + 2 warmup + 7 steady windows per (entry, K).  The steady rounds are
 * the only ones used for the regression; the first/warmup rounds stay in the
 * raw log so a reviewer can see the settling. */
#ifndef LAT_ROUNDS
#define LAT_ROUNDS 10
#endif

/* Untimed chain steps executed once per (entry, K) before round 0.  Both maps
 * are contracting (|dx'/dx| < 0.65), so this reaches the closed-loop operating
 * point before anything is timed. */
#ifndef LAT_PRELUDE
#define LAT_PRELUDE 16
#endif

/* K values are parsed from this string at runtime so a build can narrow the
 * matrix without touching the driver.  K == 0 measures window overhead only. */
#ifndef LAT_K_LIST
#define LAT_K_LIST "0,4,8,16,32"
#endif

/* Optional ';'-separated allow list of entry names.  Empty means "every entry
 * of every enabled group"; a calibration build uses it to run only the probes
 * whose gates it needs, without recompiling group tables. */
#ifndef LAT_ONLY_ENTRIES
#define LAT_ONLY_ENTRIES ""
#endif

#define LAT_MAX_K 16

/* Number of dependent addi in one step of the primary nop probe.  The
 * calibration also runs nop_addi64 (64 addi per step) as the long-window
 * control; both are reported in LAT_HEADER. */
#ifndef LAT_NOP_BLOCK
#define LAT_NOP_BLOCK 1
#endif

/* VLMAX of e64 at VLEN=128, checked at runtime (vlenb == 16). */
#define LAT_LANES_M1 2u
#define LAT_LANES_M2 4u

/* One measured window.  begin/cycles are the raw rdcycle pair (cycles is
 * end-begin); checksum covers the out-of-window oracle replay output, and
 * chain_checksum covers the chained state written back by the window. */
typedef struct {
  uint64_t begin;
  uint64_t cycles;
  uint64_t checksum;
  uint64_t chain_checksum;
} lat_window;

typedef void (*lat_probe_fn)(unsigned k, unsigned round, lat_window *w);

typedef struct {
  const char *name;
  lat_probe_fn run;
} lat_probe;

typedef struct {
  const char *name;
  const lat_probe *probes;
  unsigned count;
} lat_probe_group;

#define LAT_PROBE_COUNT(a) (sizeof(a) / sizeof((a)[0]))

/* Each group table is defined by one latency_driver.c translation unit and
 * only declared when the build asked for that group. */
extern const lat_probe_group lat_group_calib;
extern const lat_probe_group lat_group_atan;
extern const lat_probe_group lat_group_acos_nx;
extern const lat_probe_group lat_group_acos_masked;

/* Oracle counters, reset by latency_main.c before every (entry, K) block. */
void lat_reset_counters(void);
unsigned lat_checks_count(void);
unsigned lat_failed_count(void);

#endif /* LATENCY_PROBE_H */
