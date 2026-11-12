/* GNU libc scalar table layouts; LGPL-2.1-or-later.
   Copyright (C) 2017-2026 Free Software Foundation, Inc.
   Derived from dbl-64/math_config.h and flt-32/math_config.h. */
#ifndef SCALAR_TABLES_H
#define SCALAR_TABLES_H
#include <stdint.h>
struct expvec_data {
 double invln2N,negln2hiN,negln2loN,poly[4],shift;
 double exp2_shift,exp2_poly[5];
 double neglog10_2hiN,neglog10_2loN,exp10_poly[5],invlog10_2N;
 uint64_t tab[256];
};
extern const struct expvec_data expvec_data;
struct expfvec_data {
 uint64_t tab[32];double shift_scaled,poly[3],invln2_scaled,poly_scaled[3],shift;
};
extern const struct expfvec_data expfvec_data;
struct logfvec_data {
 struct {double invc,logc;} tab[16];double ln2,poly[3];
};
extern const struct logfvec_data logfvec_data;
#endif
