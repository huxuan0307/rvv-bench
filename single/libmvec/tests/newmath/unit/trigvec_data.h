/* IBM Accurate Mathematical Library table, from GNU libc sincostab.c.
   Copyright (C) 2001-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later */
#ifndef TRIGVEC_DATA_H
#define TRIGVEC_DATA_H
#include <stddef.h>
/* The original 110 records: sin_hi, sin_lo, cos_hi, cos_lo at i/128. */
extern const double trigvec_sincostab[440];
/* Array API. The historical unit gate tests RNE; the final rounding
   contract is open. Exact x == y is supported; partial overlap is not.
   The third argument is unused and may be NULL. Only |x| >= 0x1.921fbp26
   (including nonfinite inputs) can call scalar libm for range reduction. */
void sinvec_m1(size_t, const double *, const double *, double *);
void sinvec_m2(size_t, const double *, const double *, double *);
void cosvec_m1(size_t, const double *, const double *, double *);
void cosvec_m2(size_t, const double *, const double *, double *);
#endif
