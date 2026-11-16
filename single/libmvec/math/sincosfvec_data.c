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

/* Verbatim from sysdeps/ieee754/flt-32/s_sincosf_data.c for a target with
   TOINT_INTRINSICS == 0 (RISC-V generic: hpi_inv is scaled by 2^23 so the
   quadrant ends up in bits 24..31).  The second entry is the first entry with
   the cosine polynomial negated; the RVV kernel keeps only table[0] and
   applies the table[1] sign as an exact negate after the cosine polynomial. */
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
