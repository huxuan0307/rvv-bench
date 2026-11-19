/* Unit test for the float candidates sinf / cosf / powf.
   Copyright (C) 2026 Free Software Foundation, Inc.
   SPDX-License-Identifier: LGPL-2.1-or-later

   Why this file exists: the abi-public case only audits the Nx and fixed-lane
   native-ABI entries of the installed libmvec, so the **array** entries
   (sinfvec_m1/m2, cosfvec_m1/m2, powfvec_m1/m2) would otherwise be reachable
   only through the performance harness.  This unit test exercises them
   directly against the same frozen MPFR corpus rows that abi-data.h carries
   (ABI_DATA_FINITE_PER_FUNC + the explicit C99 specials), plus the contract
   details an array entry has to add on top of the register kernels:

     * n == 0 must not write or raise,
     * partial tails (n = 3, 5, 17) must leave the lanes past n untouched,
     * x == y in place must work (the array ABI only promises exact reuse),
     * flags per row: req must be raised, forbid must stay clear,
     * the RNE result is independent of the caller frm and frm is restored,
     * the Nx entries ignore an incoming vl (they always process VLMAX).

   Output protocol (parsed by collect.py):
     LIBMVEC_CHECK case=float3-unit checks=N failed=M
     LIBMVEC_COMPLETE case=float3-unit status=pass|fail
   Exit status is not used: the bare-metal runtime always halts through
   nemu_trap, so the collector reads the records. */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <riscv_vector.h>
#include "av_runtime.h"
#include "abi-data.h"

#define RVV_CC __attribute__ ((riscv_vector_cc))
#define ARRAY_ENTRY(name) \
  void name (size_t, const float *, const float *, float *)
ARRAY_ENTRY (sinfvec_m1);
ARRAY_ENTRY (sinfvec_m2);
ARRAY_ENTRY (cosfvec_m1);
ARRAY_ENTRY (cosfvec_m2);
ARRAY_ENTRY (powfvec_m1);
ARRAY_ENTRY (powfvec_m2);

#define NX_ENTRY(name) vfloat32m1_t _ZGV1Nxv_##name (vfloat32m1_t) RVV_CC; \
  vfloat32m2_t _ZGV2Nxv_##name (vfloat32m2_t) RVV_CC
NX_ENTRY (sinf);
NX_ENTRY (cosf);

static void array_sinf_m1 (size_t n, const float *x, float *y)
{ sinfvec_m1 (n, x, x, y); }
static void array_sinf_m2 (size_t n, const float *x, float *y)
{ sinfvec_m2 (n, x, x, y); }
static void array_cosf_m1 (size_t n, const float *x, float *y)
{ cosfvec_m1 (n, x, x, y); }
static void array_cosf_m2 (size_t n, const float *x, float *y)
{ cosfvec_m2 (n, x, x, y); }
static void array_powf_m1 (size_t n, const float *x, const float *z, float *y)
{ powfvec_m1 (n, x, z, y); }
static void array_powf_m2 (size_t n, const float *x, const float *z, float *y)
{ powfvec_m2 (n, x, z, y); }

struct family
{
  const char *name;
  const abi_case32 *rows;
  unsigned nrows, binary;
  void (*m1) (size_t, const float *, const float *, float *);
  void (*m2) (size_t, const float *, const float *, float *);
};

#define FAM(name, NAME, binary) \
  { #name, abi_cases_##name, ABI_CASES_##NAME, binary, \
    name##vec_m1, name##vec_m2 }

static const struct family families[] = {
  FAM (sinf, SINF, 0), FAM (cosf, COSF, 0), FAM (powf, POWF, 1),
};

#define MAXFAIL 8
static uint64_t checks, failed;
static const char *stage = "?";
static const struct family *fam;
static unsigned row_idx, lane_idx;

static float b2f (uint32_t u) { float x; memcpy (&x, &u, 4); return x; }
static uint32_t f2b (float x) { uint32_t u; memcpy (&u, &x, 4); return u; }

static unsigned
frm_get (void)
{ unsigned v; __asm__ volatile ("frrm %0" : "=r" (v)); return v & 7u; }
static void
frm_set (unsigned v)
{ __asm__ volatile ("fsrm %0" : : "r" (v) : "memory"); }
static unsigned
fflags_get (void)
{ unsigned v; __asm__ volatile ("frflags %0" : "=r" (v)); return v & ABI_FE_ALL; }
static void
fflags_set (unsigned v)
{ __asm__ volatile ("fsflags %0" : : "r" (v) : "memory"); }

static uint32_t
order32 (uint32_t b)
{ return (b >> 31) ? (uint32_t) ~b : (b | 0x80000000u); }
static uint32_t
ulp32 (uint32_t a, uint32_t b)
{
  uint32_t x = order32 (a), y = order32 (b);
  return x > y ? x - y : y - x;
}

static void
fail (const char *what, uint64_t got, uint64_t want)
{
  ++failed;
  if (failed > MAXFAIL)
    return;
  printf_ ("FAIL family=%s lmul=%s what=%s stage=%s row=%u lane=%u "
           "got=0x%08llx want=0x%08llx\n", fam->name,
           fam->m1 == NULL ? "?" : "m?", what, stage, row_idx, lane_idx,
           (unsigned long long) got, (unsigned long long) want);
}

static int
lanes_ok (const abi_case32 *c, uint32_t got, const char *lmul)
{
  (void) lmul;
  ++checks;
  if (c->kind == ABI_KIND_NAN)
    {
      /* NaN payload is not promised; both must be NaN. */
      if (((got >> 23) & 0xff) != 0xff || (got & 0x7fffffu) == 0)
        { fail ("nan-kind", got, c->ref); return 0; }
      return 1;
    }
  if (c->kind == ABI_KIND_EXACT ? (got != c->ref) : (ulp32 (got, c->ref) > 1))
    { fail (c->kind == ABI_KIND_EXACT ? "exact" : "ulp", got, c->ref); return 0; }
  return 1;
}

static void
verify_flags (const abi_case32 *c, unsigned fl)
{
  ++checks;
  unsigned missing = c->req & ~fl, extra = fl & c->forbid;
  if (missing | extra)
    {
      fail ("flags", fl, (uint64_t) (c->req | c->forbid) | ((uint64_t) missing << 8)
                        | ((uint64_t) extra << 16));
    }
}

/* One row at a time: also checks the flag contract, which a bulk call mixes. */
static void
check_rows_one_by_one (const struct family *f)
{
  for (unsigned i = 0; i < f->nrows; ++i)
    {
      const abi_case32 *c = &f->rows[i];
      float x = b2f (c->x), z = b2f (c->y), out[2] = { 0.5f, 0.25f };
      row_idx = i;
      fflags_set (0);
      f->m1 (1, &x, &z, out);
      verify_flags (c, fflags_get ());
      lanes_ok (c, f2b (out[0]), "m1");
      fflags_set (0);
      f->m2 (1, &x, &z, out + 1);
      verify_flags (c, fflags_get ());
      lanes_ok (c, f2b (out[1]), "m2");
    }
}

/* Bulk call: every row in one array entry invocation, checks lane order. */
static void
check_rows_bulk (const struct family *f)
{
  static float x[ABI_CASES_POWF + 8], z[ABI_CASES_POWF + 8], y[ABI_CASES_POWF + 8];
  for (unsigned i = 0; i < f->nrows; ++i)
    { x[i] = b2f (f->rows[i].x); z[i] = b2f (f->rows[i].y); }
  const unsigned guard = 8;
  for (unsigned i = 0; i < guard; ++i)
    { x[f->nrows + i] = b2f (0x5a5a5a5au); z[f->nrows + i] = b2f (0x5a5a5a5au); }
  f->m1 (f->nrows, x, z, y);
  for (unsigned i = 0; i < f->nrows; ++i)
    { row_idx = i; lane_idx = i; lanes_ok (&f->rows[i], f2b (y[i]), "m1"); }
  memset (y, 0, sizeof y);
  f->m2 (f->nrows, x, z, y);
  for (unsigned i = 0; i < f->nrows; ++i)
    { row_idx = i; lane_idx = i; lanes_ok (&f->rows[i], f2b (y[i]), "m2"); }
}

/* Array-entry contract: n == 0, tails, in-place reuse. */
static void
check_array_contract (const struct family *f)
{
  const abi_case32 *c = &f->rows[0];
  float x[32], z[32], y[32];
  for (unsigned i = 0; i < 32; ++i)
    { x[i] = b2f (c->x); z[i] = b2f (c->y); y[i] = 0.5f; }

  stage = "n0";
  y[0] = 0.75f;
  f->m1 (0, x, z, y);
  ++checks;
  if (f2b (y[0]) != 0x3f400000u) fail ("n0-write", f2b (y[0]), 0x3f400000u);
  f->m2 (0, x, z, y);
  ++checks;
  if (f2b (y[0]) != 0x3f400000u) fail ("n0-write", f2b (y[0]), 0x3f400000u);

  stage = "tail";
  static const unsigned lens[] = { 1, 3, 5, 17 };
  for (unsigned t = 0; t < sizeof lens / sizeof lens[0]; ++t)
    {
      unsigned n = lens[t];
      for (unsigned i = 0; i < 32; ++i) y[i] = 0.5f;
      f->m1 (n, x, z, y);
      for (unsigned i = n; i < 32; ++i)
        { ++checks; if (f2b (y[i]) != 0x3f000000u) fail ("tail-write", f2b (y[i]), 0x3f000000u); }
      for (unsigned i = 0; i < n; ++i)
        { row_idx = i; lane_idx = i; lanes_ok (c, f2b (y[i]), "m1"); }
      for (unsigned i = 0; i < 32; ++i) y[i] = 0.5f;
      f->m2 (n, x, z, y);
      for (unsigned i = n; i < 32; ++i)
        { ++checks; if (f2b (y[i]) != 0x3f000000u) fail ("tail-write", f2b (y[i]), 0x3f000000u); }
      for (unsigned i = 0; i < n; ++i)
        { row_idx = i; lane_idx = i; lanes_ok (c, f2b (y[i]), "m2"); }
    }

  stage = "inplace";
  /* x == y is the only aliasing an array ABI promises. */
  for (unsigned i = 0; i < 32; ++i) x[i] = b2f (c->x);
  f->m1 (8, x, z, x);
  row_idx = 0;
  for (unsigned i = 0; i < 8; ++i)
    { lane_idx = i; lanes_ok (c, f2b (x[i]), "m1"); }
  for (unsigned i = 0; i < 32; ++i) x[i] = b2f (c->x);
  f->m2 (8, x, z, x);
  for (unsigned i = 0; i < 8; ++i)
    { lane_idx = i; lanes_ok (c, f2b (x[i]), "m2"); }
}

/* frm: entries must produce the RNE bits for all five legal frm values and
   restore the caller's frm. */
static void
check_frm (const struct family *f)
{
  const abi_case32 *c = &f->rows[0];
  float x[8], z[8], y[8], base[8];
  for (unsigned i = 0; i < 8; ++i) { x[i] = b2f (c->x); z[i] = b2f (c->y); }
  stage = "frm";
  frm_set (0); fflags_set (0);
  f->m1 (8, x, z, y);
  memcpy (base, y, sizeof base);
  for (unsigned mode = 1; mode <= 4; ++mode)
    {
      frm_set (mode); fflags_set (0);
      f->m1 (8, x, z, y);
      ++checks;
      if (frm_get () != mode) fail ("frm-restored", frm_get (), mode);
      for (unsigned i = 0; i < 8; ++i)
        { ++checks; if (f2b (y[i]) != f2b (base[i])) fail ("frm-result", f2b (y[i]), f2b (base[i])); }
    }
  frm_set (0);
  fflags_set (0);
  f->m2 (8, x, z, y);
  for (unsigned mode = 1; mode <= 4; ++mode)
    {
      frm_set (mode); fflags_set (0);
      f->m2 (8, x, z, y);
      ++checks;
      if (frm_get () != mode) fail ("frm-restored", frm_get (), mode);
      for (unsigned i = 0; i < 8; ++i)
        { ++checks; if (f2b (y[i]) != f2b (base[i])) fail ("frm-result", f2b (y[i]), f2b (base[i])); }
    }
  frm_set (0);
  fflags_set (0);
}

/* Nx entries ignore the incoming vl and always process VLMAX. */
static void
check_vl_independence (void)
{
  float in[8] = { 1.0f, 2.0f, -3.0f, 0.5f, 4.0f, -1.5f, 0.25f, 8.0f };
  size_t vl1 = __riscv_vsetvlmax_e32m1 (), vl2 = __riscv_vsetvlmax_e32m2 ();
  float base1[8], base2[8], got[8];
  stage = "vl";
  fam = &families[0];
  vfloat32m1_t r1 = _ZGV1Nxv_sinf (__riscv_vle32_v_f32m1 (in, vl1));
  __riscv_vse32_v_f32m1 (base1, r1, vl1);
  __asm__ volatile ("vsetivli zero, 1, e32, m1, ta, ma");
  r1 = _ZGV1Nxv_sinf (__riscv_vle32_v_f32m1 (in, vl1));
  __riscv_vse32_v_f32m1 (got, r1, vl1);
  for (size_t i = 0; i < vl1; ++i)
    { ++checks; if (got[i] != base1[i]) fail ("vl-sinf", f2b (got[i]), f2b (base1[i])); }

  vfloat32m2_t r2 = _ZGV2Nxv_cosf (__riscv_vle32_v_f32m2 (in, vl2));
  __riscv_vse32_v_f32m2 (base2, r2, vl2);
  __asm__ volatile ("vsetivli zero, 1, e32, m2, ta, ma");
  r2 = _ZGV2Nxv_cosf (__riscv_vle32_v_f32m2 (in, vl2));
  __riscv_vse32_v_f32m2 (got, r2, vl2);
  for (size_t i = 0; i < vl2; ++i)
    { ++checks; if (got[i] != base2[i]) fail ("vl-cosf", f2b (got[i]), f2b (base2[i])); }
}

int
main (void)
{
  unsigned vlen_bits = (unsigned) __riscv_vlenb () * 8u;
  printf_ ("float3 unit start vlenb=%u\n", (unsigned) __riscv_vlenb ());
  printf_ ("LIBMVEC_CONFIG case=float3-unit function=abi vlen_bits=%u "
           "seed=%u rounds=0\n", vlen_bits, ABI_DATA_SEED);
  for (unsigned i = 0; i < sizeof families / sizeof families[0]; ++i)
    {
      fam = &families[i];
      stage = "rows1";
      check_rows_one_by_one (fam);
      stage = "rowsN";
      check_rows_bulk (fam);
      stage = "contract";
      check_array_contract (fam);
      stage = "frm";
      check_frm (fam);
    }
  check_vl_independence ();
  printf_ ("LIBMVEC_CHECK case=float3-unit checks=%llu failed=%llu\n",
           (unsigned long long) checks, (unsigned long long) failed);
  printf_ ("LIBMVEC_COMPLETE case=float3-unit status=%s\n",
           failed == 0 ? "pass" : "fail");
  return 0;
}
