#ifndef XS_LIBMVEC_KERNEL_H
#define XS_LIBMVEC_KERNEL_H
#include <stddef.h>
#include <stdint.h>
#ifndef FUNC
#error FUNC required
#endif
#ifndef FLOAT_INPUT
#define FLOAT_INPUT 0
#endif
#ifndef BINARY_INPUT
#define BINARY_INPUT 0
#endif
#if FLOAT_INPUT
typedef float real_t;
typedef uint32_t bits_t;
#else
typedef double real_t;
typedef uint64_t bits_t;
#endif
typedef void (*kernel_fn)(size_t, const real_t *, const real_t *, real_t *);
#define DECLARE_KERNEL(name) void name(size_t, const real_t *, const real_t *, real_t *)
DECLARE_KERNEL(scalar_strict);
DECLARE_KERNEL(scalar_fast);
DECLARE_KERNEL(auto_m1);
DECLARE_KERNEL(auto_m2);
DECLARE_KERNEL(direct_m1);
DECLARE_KERNEL(direct_m2);
DECLARE_KERNEL(direct_m4);
DECLARE_KERNEL(direct_m8);
DECLARE_KERNEL(empty_kernel);
DECLARE_KERNEL(copy_kernel);
#endif
