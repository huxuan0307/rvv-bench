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
 * The exp/log/sin/cos/pow maps added later have the same job:
 *
 *   exp:  y = exp(x) in (0, inf)
 *         x' = clamp(y, 0.1, 2.0) - 1.0  in [-0.9, 1.0]
 *   log:  y = log(x) in (-inf, inf) (x > 0 on the orbit)
 *         x' = clamp(y, 0.1, 2.0)        in [0.1, 2.0]
 *   sin/cos: x' = y in [-1, 1]  (identity: the call itself closes the loop)
 *   pow:  y = pow(x, z) > 0
 *         x' = clamp(y, 0.5, 2.0), z' = x' + 0.5
 *
 * The clamp needs two instructions (max + min) and exp/pow add one more for
 * the affine step, so a map is 0-3 vector FP instructions.  The orbits
 * converge to their unique fixed point (exp: x = 0, log: x = 0.1, sin/cos:
 * x = 0, pow: x = 2, z = 2.5), so the timed windows measure one reproducible
 * operating point.  The build compiles with
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

/* exp: (0, inf) -> [-0.9, 1.0].  clamp(y,0.1,2.0) - 1.0 = three instructions:
 * the max and min are the clamp, the subtract is the affine closing step. */
static inline vfloat64m1_t
lat_map_exp_m1(vfloat64m1_t y, size_t vl)
{
  y = __riscv_vfmax_vf_f64m1(y, 0.1, vl);
  y = __riscv_vfmin_vf_f64m1(y, 2.0, vl);
  return __riscv_vfsub_vf_f64m1(y, 1.0, vl);
}

static inline vfloat64m2_t
lat_map_exp_m2(vfloat64m2_t y, size_t vl)
{
  y = __riscv_vfmax_vf_f64m2(y, 0.1, vl);
  y = __riscv_vfmin_vf_f64m2(y, 2.0, vl);
  return __riscv_vfsub_vf_f64m2(y, 1.0, vl);
}

/* log: (-inf, inf) -> [0.1, 2.0]. */
static inline vfloat64m1_t
lat_map_log_m1(vfloat64m1_t y, size_t vl)
{
  y = __riscv_vfmax_vf_f64m1(y, 0.1, vl);
  return __riscv_vfmin_vf_f64m1(y, 2.0, vl);
}

static inline vfloat64m2_t
lat_map_log_m2(vfloat64m2_t y, size_t vl)
{
  y = __riscv_vfmax_vf_f64m2(y, 0.1, vl);
  return __riscv_vfmin_vf_f64m2(y, 2.0, vl);
}

/* sin/cos: the call already returns a value in [-1, 1], which is the input
 * domain, so the identity map closes the loop with no extra instruction.
 * Keeping it as a function (rather than dropping the map argument) keeps the
 * probe macros uniform across all five functions. */
static inline vfloat64m1_t
lat_map_identity_m1(vfloat64m1_t y, size_t vl)
{
  (void) vl;
  return y;
}

static inline vfloat64m2_t
lat_map_identity_m2(vfloat64m2_t y, size_t vl)
{
  (void) vl;
  return y;
}

/* pow: (x, z) -> x' = clamp(y,0.5,2.0), z' = x'+0.5.  The two pieces are
 * separate one/two-instruction helpers so the probe keeps both vectors in
 * their own registers instead of forcing an aggregate return. */
static inline vfloat64m1_t
lat_map_pow_x_m1(vfloat64m1_t y, size_t vl)
{
  y = __riscv_vfmax_vf_f64m1(y, 0.5, vl);
  return __riscv_vfmin_vf_f64m1(y, 2.0, vl);
}

static inline vfloat64m2_t
lat_map_pow_x_m2(vfloat64m2_t y, size_t vl)
{
  y = __riscv_vfmax_vf_f64m2(y, 0.5, vl);
  return __riscv_vfmin_vf_f64m2(y, 2.0, vl);
}

static inline vfloat64m1_t
lat_map_pow_z_m1(vfloat64m1_t x, size_t vl)
{
  return __riscv_vfadd_vf_f64m1(x, 0.5, vl);
}

static inline vfloat64m2_t
lat_map_pow_z_m2(vfloat64m2_t x, size_t vl)
{
  return __riscv_vfadd_vf_f64m2(x, 0.5, vl);
}

#endif /* LATENCY_MAP_H */
