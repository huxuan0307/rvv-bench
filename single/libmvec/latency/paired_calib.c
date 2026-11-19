/* Bridge calibration controls for the paired protocol (group calib).
 *
 * These probes do not call any libmvec entry.  They exist so the acceptance
 * check can show that the exact-input bridge of paired_bridge.h is a real
 * dependency edge on this machine, and that the dependency can be observed
 * growing linearly when more FP latency is injected:
 *
 *   calib_reload    load-only chain (no FP arithmetic) -> window floor
 *   calib_bridge    x = seed + (x - x)                 -> bridge-only slope
 *   calib_injectN   bridge + N dependent finite vfadd  -> injected slope
 *
 * A hardware zero-idiom shortcut that removed the vfsub->vfadd dependency
 * would make calib_bridge equal calib_reload, and the injected slope would not
 * grow with N; the acceptance parser rejects that combination instead of
 * publishing absolute cycle numbers on an unproven bridge.
 */
#include <stdint.h>
#include "av_runtime.h"
#include <riscv_vector.h>

#include "paired_protocol.h"
#include "paired_bridge.h"

static double pair_calib_tbuf[8] __attribute__ ((aligned (64)));

/* (A) seed reload only. */
static void
probe_calib_reload (unsigned round, pair_window *w)
{
  const size_t vl = 2;
  const unsigned lanes = 2;
  const unsigned ncall = pair_n / lanes;
  unsigned ni = 1;
  vfloat64m1_t x;
  (void)round;
  if (ni == ncall)
    ni = 0;
  x = __riscv_vle64_v_f64m1 (pair_seed0, vl);
  w->steps = ncall * pair_batch * pair_k * pair_repeat;
  w->calls = w->steps;
  w->begin = PAIR_WIN_BEGIN ();
  for (unsigned i = 0; i < w->steps; ++i)
    {
      x = __riscv_vle64_v_f64m1 (pair_seed_vec (pair_seed0, ni, lanes), vl);
      /* Consume each vector so the compiler cannot sink the loads. */
      __asm__ volatile ("" : : "vr"(x) : "memory");
      if (++ni == ncall)
        ni = 0;
    }
  w->cycles = PAIR_WIN_END () - w->begin;
  __riscv_vse64_v_f64m1 (pair_calib_tbuf, x, vl);
  w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_calib_tbuf, lanes);
}

/* (B) bridge only, no call: x = seed + (x - x). */
static void
probe_calib_bridge (unsigned round, pair_window *w)
{
  const size_t vl = 2;
  const unsigned lanes = 2;
  const unsigned ncall = pair_n / lanes;
  unsigned ni = 1;
  vfloat64m1_t x;
  (void)round;
  if (ni == ncall)
    ni = 0;
  x = __riscv_vle64_v_f64m1 (pair_seed0, vl);
  w->steps = ncall * pair_batch * pair_k * pair_repeat;
  w->calls = w->steps;
  w->begin = PAIR_WIN_BEGIN ();
  for (unsigned i = 0; i < w->steps; ++i)
    {
      x = lat_pair_reset_m1 (
          x,
          __riscv_vle64_v_f64m1 (pair_seed_vec (pair_seed0, ni, lanes), vl));
      if (++ni == ncall)
        ni = 0;
    }
  w->cycles = PAIR_WIN_END () - w->begin;
  __riscv_vse64_v_f64m1 (pair_calib_tbuf, x, vl);
  w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_calib_tbuf, lanes);
}

/* (C) bridge + N dependent finite vfadd: the injected slope must be linear in
 * N.  The added vector is a non-zero normal constant, so this is a genuine
 * numeric FP dependency, not another zero idiom. */
#define PAIR_CALIB_INJECT(TAG, DEPTH)                                         \
  static void probe_##TAG (unsigned round, pair_window *w)                    \
  {                                                                           \
    const size_t vl = 2;                                                      \
    const unsigned lanes = 2;                                                 \
    const unsigned ncall = pair_n / lanes;                                    \
    unsigned ni = 1;                                                          \
    vfloat64m1_t x, one = __riscv_vfmv_v_f_f64m1 (1.0, vl);                   \
    (void)round;                                                              \
    if (ni == ncall)                                                          \
      ni = 0;                                                                 \
    x = __riscv_vle64_v_f64m1 (pair_seed0, vl);                               \
    w->steps = ncall * pair_batch * pair_k * pair_repeat;                     \
    w->calls = w->steps;                                                      \
    w->begin = PAIR_WIN_BEGIN ();                                             \
    for (unsigned i = 0; i < w->steps; ++i)                                   \
      {                                                                       \
        x = lat_pair_reset_m1 (                                               \
            x, __riscv_vle64_v_f64m1 (pair_seed_vec (pair_seed0, ni, lanes),  \
                                      vl));                                   \
        for (unsigned j = 0; j < (DEPTH); ++j)                                \
          x = __riscv_vfadd (x, one, vl);                                     \
        if (++ni == ncall)                                                    \
          ni = 0;                                                             \
      }                                                                       \
    w->cycles = PAIR_WIN_END () - w->begin;                                   \
    __riscv_vse64_v_f64m1 (pair_calib_tbuf, x, vl);                           \
    w->checksum = pair_fnv_buf (PAIR_FNV_INIT, pair_calib_tbuf, lanes);       \
  }

PAIR_CALIB_INJECT (calib_inject8, 8)
PAIR_CALIB_INJECT (calib_inject16, 16)
PAIR_CALIB_INJECT (calib_inject32, 32)

static const pair_probe pair_calib_probes[] = {
  { "calib_reload", "calib_reload", "calib", "m1", "fixed", "unmasked", 2, 0,
    probe_calib_reload, 0 },
  { "calib_bridge", "calib_bridge", "calib", "m1", "fixed", "unmasked", 2, 0,
    probe_calib_bridge, 0 },
  { "calib_inject8", "calib_inject8", "calib", "m1", "fixed", "unmasked", 2, 0,
    probe_calib_inject8, 0 },
  { "calib_inject16", "calib_inject16", "calib", "m1", "fixed", "unmasked", 2,
    0, probe_calib_inject16, 0 },
  { "calib_inject32", "calib_inject32", "calib", "m1", "fixed", "unmasked", 2,
    0, probe_calib_inject32, 0 },
};

const pair_probe_group pair_group_calib
    = { "calib", pair_calib_probes, PAIR_PROBE_COUNT (pair_calib_probes) };
