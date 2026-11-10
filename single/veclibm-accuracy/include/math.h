// Minimal <math.h> C++ overload subset used by this upstream test tree.
// -nostdinc++ intentionally removes libstdc++'s wrapper. The C system header
// alone silently narrows cos(long double), sin(long double), log2(long double).
// Preserve those standard overload semantics with the real libm entry points.
#ifndef VECLIBM_ACCURACY_MATH_H
#define VECLIBM_ACCURACY_MATH_H
#include_next <math.h>
#ifdef __cplusplus
inline long double cos(long double x) { return cosl(x); }
inline float cos(float x) { return cosf(x); }
inline long double sin(long double x) { return sinl(x); }
inline float sin(float x) { return sinf(x); }
inline long double log2(long double x) { return log2l(x); }
inline float log2(float x) { return log2f(x); }
#endif
#endif
