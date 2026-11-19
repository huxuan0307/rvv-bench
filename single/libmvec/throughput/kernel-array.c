/* Auxiliary profile only: same full harness/auto/scalar, direct calls array ABI. */
#include "kernel.h"
#ifndef ARRAY_SYMBOL
#error ARRAY_SYMBOL required
#endif
extern void ARRAY_SYMBOL(size_t n, const real_t *x, const real_t *z, real_t *y);
__attribute__((noinline,noclone))
void KERNEL_NAME(size_t n,const real_t*x,const real_t*z,real_t*y) {
 ARRAY_SYMBOL(n,x,z,y);
}
