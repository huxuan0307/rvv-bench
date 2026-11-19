/* Compile separately for each named scalar/automatic implementation. */
#include "kernel.h"
#include <math.h>
#ifndef KERNEL_NAME
#error KERNEL_NAME required
#endif
__attribute__((noinline, noclone))
void KERNEL_NAME(size_t n, const real_t *restrict x,
                 const real_t *restrict z, real_t *restrict y)
{
#if AUTO_VECTOR
#pragma omp simd
#endif
    for (size_t i=0; i<n; ++i) {
#if BINARY_INPUT
        y[i] = FUNC(x[i], z[i]);
#else
        y[i] = FUNC(x[i]);
#endif
    }
}
