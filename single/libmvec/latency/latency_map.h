/* Affine feedback maps that close the latency dependency chain.
 *
 * The harness measures  y = f(x)  followed by  x' = map(y),  so the next call
 * consumes the previous result instead of an independent input.  Each map is
 * exactly two vector FP instructions and is a contraction, which keeps the
 * chain inside the domain of f forever:
 *
 *   atan: y = atan(x)  in (-pi/2, pi/2)
 *         x' = (2/pi)*y               in (-1, 1)      -> |dx'/dx| <= 2/pi
 *   acos: y = acos(x)  in [0, pi]     (x already in [-1, 1])
 *         x' = (2/pi)*y - 1           in [-1, 1]      -> |dx'/dx| = (2/pi)/sqrt(1-x^2)
 *
 * Both orbits converge to their unique fixed point near x = 0, so the timed
 * windows measure one reproducible operating point.  The build compiles with
 * -ffp-contract=off: GCC would otherwise fold the pair into a single vfmacc
 * and "map-only" would stop being two instructions.
 */
#ifndef LATENCY_MAP_H
#define LATENCY_MAP_H

#include <riscv_vector.h>

/* Nearest double to 2/pi. */
#define LAT_TWO_OVER_PI 0.63661977236758134308

/* acos: [0, pi] -> [-1, 1]. */
static inline vfloat64m1_t
lat_map_acos_m1(vfloat64m1_t y, size_t vl)
{
  y = __riscv_vfmul_vf_f64m1(y, LAT_TWO_OVER_PI, vl);
  return __riscv_vfadd_vf_f64m1(y, -1.0, vl);
}

static inline vfloat64m2_t
lat_map_acos_m2(vfloat64m2_t y, size_t vl)
{
  y = __riscv_vfmul_vf_f64m2(y, LAT_TWO_OVER_PI, vl);
  return __riscv_vfadd_vf_f64m2(y, -1.0, vl);
}

/* atan: (-pi/2, pi/2) -> (-1, 1).  The explicit +0.0 keeps the step shape
 * identical to the acos map (two instructions, same dependency structure). */
static inline vfloat64m1_t
lat_map_atan_m1(vfloat64m1_t y, size_t vl)
{
  y = __riscv_vfmul_vf_f64m1(y, LAT_TWO_OVER_PI, vl);
  return __riscv_vfadd_vf_f64m1(y, 0.0, vl);
}

static inline vfloat64m2_t
lat_map_atan_m2(vfloat64m2_t y, size_t vl)
{
  y = __riscv_vfmul_vf_f64m2(y, LAT_TWO_OVER_PI, vl);
  return __riscv_vfadd_vf_f64m2(y, 0.0, vl);
}

#endif /* LATENCY_MAP_H */
