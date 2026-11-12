/* Experimental RVV adaptation of GNU libc dbl-64/e_log_data.c.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later
   See LICENSES.md for source provenance and the full license location. */
#ifndef LOGVEC_DATA_H
#define LOGVEC_DATA_H
struct logvec_data {
    double ln2hi, ln2lo;
    double poly[5];
    double poly1[11];
    struct { double invc, logc; } tab[128];
};
extern const struct logvec_data logvec_data;
#endif
