/* Experimental array API; GNU libc derived LGPL-2.1-or-later kernel. */
#ifndef EXPERIMENTAL_POWVEC_H
#define EXPERIMENTAL_POWVEC_H
#include <stddef.h>
/* Experimental accuracy tests run under RNE; the final API rounding contract
   is open, and other rounding modes have not been accuracy-validated.
   No frm access occurs inside either kernel.
   x and z are read before stores in each chunk; exact y==x or y==z is valid.
   Other partially overlapping arrays are not supported. n==0 reads nothing. */
void powvec_m1(size_t n, const double *x, const double *z, double *y);
void powvec_m2(size_t n, const double *x, const double *z, double *y);
#endif
