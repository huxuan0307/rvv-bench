/* Empty call targets with the exact vector ABI of the libmvec entries.
 *
 * Slope of an empty-call chain is the per-call overhead (jalr + ret + register
 * moves) that every timed entry pays before and after its kernel.  The masked
 * variants mirror the fixed-size masked acos ABI, which passes an extra mask
 * argument, so the subtraction stays apples-to-apples.
 *
 * The functions must stay empty: latency build audit checks that their bodies
 * contain nothing but `ret`, and the compiler must not inline them (separate
 * translation unit, -fno-lto, no -flto in the link).
 */
#include <riscv_vector.h>

#define LAT_RVV_CC __attribute__((riscv_vector_cc))

LAT_RVV_CC vfloat64m1_t
latency_noop_m1 (vfloat64m1_t x)
{
  return x;
}

LAT_RVV_CC vfloat64m2_t
latency_noop_m2 (vfloat64m2_t x)
{
  return x;
}

/* Mirrors _ZGV1M2v_acos: vbool64_t mask + vfloat64m1_t value. */
LAT_RVV_CC vfloat64m1_t
latency_noop_mask_m2 (vbool64_t mask, vfloat64m1_t x)
{
  (void) mask;
  return x;
}

/* Mirrors _ZGV2M4v_acos: vbool32_t mask + vfloat64m2_t value. */
LAT_RVV_CC vfloat64m2_t
latency_noop_mask_m4 (vbool32_t mask, vfloat64m2_t x)
{
  (void) mask;
  return x;
}
