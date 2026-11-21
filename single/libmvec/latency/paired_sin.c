/* Paired exact-input probes for binary64 sin (protocol 2). */
#define PAIRED_FN sin
#define PAIRED_FN_STR "sin"
#define PAIRED_BINARY 0
#define PAIRED_DATA_HEADER "sin.h"

#define PAIRED_GROUP pair_group_sin
#define PAIRED_TABLE pair_sin_cases
#define PAIRED_SEED_INIT pair_sin_seed_init
#define PAIRED_SEED0 pair_sin_seed0_ptr
#define PAIRED_SEED1 pair_sin_seed1_ptr
#define PAIRED_SEED_COUNT pair_sin_seed_count
#define PAIRED_SEED_FNV pair_sin_seed_fnv
#define PAIRED_PAIR_FNV pair_sin_pair_fnv

#include "paired_probe_impl.inc"
