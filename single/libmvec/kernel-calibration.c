#include "kernel.h"
__attribute__((noinline, noclone))
void empty_kernel(size_t n, const real_t *x, const real_t *z, real_t *y)
{
    __asm__ volatile("" :: "r"(n), "r"(x), "r"(z), "r"(y) : "memory");
}
__attribute__((noinline, noclone))
void copy_kernel(size_t n, const real_t *restrict x,
                 const real_t *restrict z, real_t *restrict y)
{
    (void)z;
    for (size_t i=0;i<n;++i) y[i]=x[i];
}
