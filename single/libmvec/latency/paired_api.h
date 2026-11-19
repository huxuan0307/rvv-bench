/* Interface exported by each paired_<function>.c translation unit. */
#ifndef PAIRED_API_H
#define PAIRED_API_H

#include "paired_protocol.h"

extern const pair_probe_group pair_group_atan;
extern const pair_probe_group pair_group_atan2;
extern const pair_probe_group pair_group_acos;
extern const pair_probe_group pair_group_log10;
extern const pair_probe_group pair_group_calib;

/* Seed identity for one function: convert the frozen header prefixes to
 * doubles (seed_init), then expose pointers, the element count actually
 * loaded, and FNV-1a hashes of the exact header bit patterns. */
void pair_atan_seed_init (unsigned n);
const double *pair_atan_seed0_ptr (void);
const double *pair_atan_seed1_ptr (void);
unsigned pair_atan_seed_count (void);
uint64_t pair_atan_seed_fnv (unsigned n);
uint64_t pair_atan_pair_fnv (unsigned n);

void pair_atan2_seed_init (unsigned n);
const double *pair_atan2_seed0_ptr (void);
const double *pair_atan2_seed1_ptr (void);
unsigned pair_atan2_seed_count (void);
uint64_t pair_atan2_seed_fnv (unsigned n);
uint64_t pair_atan2_pair_fnv (unsigned n);

void pair_acos_seed_init (unsigned n);
const double *pair_acos_seed0_ptr (void);
const double *pair_acos_seed1_ptr (void);
unsigned pair_acos_seed_count (void);
uint64_t pair_acos_seed_fnv (unsigned n);
uint64_t pair_acos_pair_fnv (unsigned n);

void pair_log10_seed_init (unsigned n);
const double *pair_log10_seed0_ptr (void);
const double *pair_log10_seed1_ptr (void);
unsigned pair_log10_seed_count (void);
uint64_t pair_log10_seed_fnv (unsigned n);
uint64_t pair_log10_pair_fnv (unsigned n);

#endif /* PAIRED_API_H */
