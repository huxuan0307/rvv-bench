/* Shared sinf/cosf polynomial data.
   Copyright (C) 2018-2026 Free Software Foundation, Inc.
   This file is part of the GNU C Library.

   The GNU C Library is free software; you can redistribute it and/or
   modify it under the terms of the GNU Lesser General Public
   License as published by the Free Software Foundation; either
   version 2.1 of the License, or (at your option) any later version.

   The GNU C Library is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
   Lesser General Public License for more details.

   You should have received a copy of the GNU Lesser General Public
   License along with the GNU C Library; if not, see
   <https://www.gnu.org/licenses/>.  */

#include "scalar_tables.h"

/* Field-by-field copy of sysdeps/ieee754/flt-32/s_sincosf_data.c
   (__sincosf_table[2], the sincos_t type in sincosf_poly.h) taken at the
   TOINT_INTRINSICS == 0 arm of its #if, which is the RISC-V generic
   configuration this repository targets.  Nothing is rounded, scaled or
   re-associated: every initializer below is the full 53-bit double literal of
   the glibc source, so the tables can be diffed member by member.

     sincos_t member | this file       | glibc value (entry 0 == entry 1)
     ----------------+-----------------+-----------------------------------
     sign[4]         | sign[4]         | { 1.0, -1.0, -1.0, 1.0 }
     hpi_inv         | hpi_inv         | 0x1.45F306DC9C883p+23  (= (2/pi)*2^24,
                     |                 | the !TOINT_INTRINSICS arm; the
                     |                 | TOINT arm uses ...p-1 instead)
     hpi             | hpi             | 0x1.921FB54442D18p0    (pi/2)
     c0..c4          | c0..c4          | 0x1p0, -0x1.ffffffd0c621cp-2,
                     |                 | 0x1.55553e1068f19p-5, -0x1.6c087e89a359dp-10,
                     |                 | 0x1.99343027bf8c3p-16   (cosine poly)
     s1..s3          | s1..s3          | -0x1.555545995a603p-3, 0x1.1107605230bc4p-7,
                     |                 | -0x1.994eb3774cf24p-13  (sine poly)

   Entry 1 of __sincosf_table is documented in glibc as "the 2nd entry computes
   -cos (x) rather than cos (x) to get negation for free": sign, hpi_inv and
   hpi are identical to entry 0 and only the cosine polynomial c0..c4 is
   negated (the sine polynomial is unchanged).  The RVV kernels read entry 0
   only and reproduce entry 1 by negating the correctly rounded cosine result
   with an exact vfneg when (n & 2), which is the same bits as evaluating the
   negated polynomial.  Entry 1 is still kept here verbatim so a reviewer can
   diff both entries against the scalar source without re-deriving the sign
   relation. */
const struct sincosfvec_data sincosfvec_data = {
  {
    {
      { 1.0, -1.0, -1.0, 1.0 },
      0x1.45F306DC9C883p+23,
      0x1.921FB54442D18p0,
      0x1p0,
      -0x1.ffffffd0c621cp-2,
      0x1.55553e1068f19p-5,
      -0x1.6c087e89a359dp-10,
      0x1.99343027bf8c3p-16,
      -0x1.555545995a603p-3,
      0x1.1107605230bc4p-7,
      -0x1.994eb3774cf24p-13
    },
    {
      { 1.0, -1.0, -1.0, 1.0 },
      0x1.45F306DC9C883p+23,
      0x1.921FB54442D18p0,
      -0x1p0,
      0x1.ffffffd0c621cp-2,
      -0x1.55553e1068f19p-5,
      0x1.6c087e89a359dp-10,
      -0x1.99343027bf8c3p-16,
      -0x1.555545995a603p-3,
      0x1.1107605230bc4p-7,
      -0x1.994eb3774cf24p-13
    }
  }
};
