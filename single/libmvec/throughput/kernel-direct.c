/* Public Nx ABI processes VLMAX regardless of caller vl: full batches only. */
#include "kernel.h"
#include <riscv_vector.h>
#ifndef VECTOR_T
#error VECTOR_T required
#endif
#if BINARY_INPUT
extern VECTOR_T VECTOR_SYMBOL(VECTOR_T, VECTOR_T);
#else
extern VECTOR_T VECTOR_SYMBOL(VECTOR_T);
#endif
__attribute__((noinline, noclone))
void KERNEL_NAME(size_t n, const real_t *x, const real_t *z, real_t *y)
{
    size_t vl = SETVLMAX();
    size_t i = 0;
    for (; i+vl<=n; i+=vl) {
        VECTOR_T a=VLOAD(x+i,vl);
#if BINARY_INPUT
        VECTOR_T b=VLOAD(z+i,vl);
        VECTOR_T r=VECTOR_SYMBOL(a,b);
#else
        VECTOR_T r=VECTOR_SYMBOL(a);
#endif
        VSTORE(y+i,r,vl);
    }
#if N16_TRIM
    if (i<n) scalar_fast(n-i,x+i,z+i,y+i);
#else
    if (i<n) scalar_strict(n-i,x+i,z+i,y+i);
#endif
}
