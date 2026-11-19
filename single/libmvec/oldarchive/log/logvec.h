/* SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef LOGVEC_H
#define LOGVEC_H
#include <stddef.h>
/* Array entries. Historical precision testing used RNE; behavior in other
   rounding modes and the final API contract require separate evaluation.
   Arrays may be identical (in-place), but may not partially overlap.
   Only nonpositive, nonfinite and positive-subnormal lanes call scalar log.
   Ordinary lanes, including near-one and short tails, always use RVV. */
void logvec_m1(size_t n, const double *x, const double *unused, double *y);
void logvec_m2(size_t n, const double *x, const double *unused, double *y);
#endif
